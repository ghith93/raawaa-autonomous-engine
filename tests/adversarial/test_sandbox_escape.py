"""Adversarial probes: does the sandbox actually hold?

Everything else in the repository proves intent. This file is the only thing that
can prove a boundary, and it only proves it where a boundary exists — so it is
skipped unless both of these are set:

    RAAWAA_SANDBOX_LIVE=1
    RAAWAA_SANDBOX_IMAGE=<image>@sha256:<64 hex>

Run it from ``.github/workflows/sandbox-adversarial.yml``, which installs gVisor on
an ephemeral runner. A green run there is evidence; a green run of the rest of the
suite is not, and #440's release rule says so explicitly.

Each probe is a small program that *tries* to escape and reports what it saw. The
harness asserts on its output, so a probe that fails to run at all is a failure
rather than a pass — silence must never read as "contained".

A denial on its own proves nothing, so the probe carries a **positive control**: it
reads ``/workspace/README.md``, a file the read-only source mount guarantees. If
that read fails, the probe's file access is broken and the harness fails before it
looks at the canary. Without that control, a wrong or mistyped canary path returns
``denied:FileNotFoundError`` and the "file outside the mount is unreadable" test
passes while proving nothing.

Environment isolation is proved the same way, not by patching this process. A
marker patched into the *test process* only shows what the sandbox does with its
own environment; it never shows whether the sandbox can read the environment of
another host process. So ``_start_host_helper`` execs a real helper process with
the marker in its **initial exec environment**, ``_helper_environ_visible_on_host``
proves on the host that the marker really is in that helper's
``/proc/<pid>/environ`` (positive control), and the sandbox probe then tries to
read only that exact PID's ``/proc`` file. The probe emits a typed status, never
environment contents: ``marker_readable`` (leak), ``unreadable``/``missing``
(isolated, but only meaningful after the host control passed), or
``inconclusive``. A helper that died or a host control that failed fails the run
rather than reading as containment.

The host canary has the same problem in a different form. A canary under a ``0700``
home directory is unreadable because of Unix permissions, not because of the
sandbox. ``_canary_base_dir`` therefore picks a world-traversable directory outside
the (symlink-resolved) workspace, ``_create_canary`` chmods the file and directory
and verifies both, and the marker carries a per-run nonce so a stale file cannot
stand in for this run. The offline half of this contract is tested in
``test_probe_harness.py``.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from engine.sandbox_policy import SandboxLimits, SandboxRequest, assert_sandbox_argv, build_docker_argv

LIVE = os.environ.get("RAAWAA_SANDBOX_LIVE") == "1"
IMAGE = os.environ.get("RAAWAA_SANDBOX_IMAGE", "")
SECRET_MARKER = "raawaa-probe-secret-do-not-leak"
# Name and value of the synthetic marker placed in the helper process's *initial*
# exec environment. The name is harmless and carries a per-run nonce so a stale
# helper from an earlier run cannot stand in for this one.
HELPER_ENV_NAME = "RAAWAA_HOST_HELPER_MARKER"
HELPER_ENV_PREFIX = "raawaa-host-helper-env-isolation"
CANARY_MARKER_PREFIX = "raawaa-host-filesystem-canary-must-not-be-readable"

# A file the read-only mount guarantees, used as the positive control.
CONTROL_PATH = "/workspace/README.md"
CONTROL_EXPECTED = "raawaa-autonomous-engine"

# Probes whose key is missing from the output must never be read as containment.
REQUIRED_KEYS = (
    "control",
    "host_canary",
    "secret_marker_present",
    "network",
    "root_readonly",
    "var_tmp_readonly",
    "var_tmp_world_writable",
    "root_write_errno",
    "path:/var/run/docker.sock",
    "path:/root/.ssh",
    "parent_environ",
)

# Each probe prints a single JSON object. Anything the probe could not even attempt
# is reported as an error string, so the assertion below can tell "denied" from
# "never ran".
PROBE_TEMPLATE = r"""
import json, os, socket, stat


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(256)
    except OSError as exc:
        return "denied:" + type(exc).__name__


result = {}
# Positive control: a file the source mount guarantees. If this is denied, the
# probe's file access is broken and every other denial below is meaningless.
result["control"] = read(__CONTROL_PATH__)
result["host_canary"] = read(__HOST_CANARY_PATH__)
# Report a boolean only; never print environment names or values. Checking
# values, not just names, makes a synthetic inherited secret observable.
result["secret_marker_present"] = any(__SECRET_MARKER__ in value for value in os.environ.values())


def parent_environ_state(pid, marker_name, marker_value):
    # Try to read one exact host process's environment; report a status only.
    # A missing PID, a non-numeric PID and an unreadable file are different
    # facts. Collapsing them into "denied" would let a broken path name pass.
    try:
        pid_number = int(pid)
    except (TypeError, ValueError):
        return "inconclusive"
    if pid_number <= 0:
        return "inconclusive"
    try:
        with open("/proc/%d/environ" % pid_number, "rb") as handle:
            raw = handle.read(65536)
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unreadable"
    except OSError:
        return "inconclusive"
    if not raw:
        return "inconclusive"
    # Match the name and value together so an unrelated variable with a similar
    # name cannot be counted as either a leak or an isolation proof.
    needle = (marker_name + "=" + marker_value).encode("utf-8")
    return "marker_readable" if needle in raw else "marker_absent"


result["parent_environ"] = parent_environ_state(
    __PARENT_PID__, __HELPER_ENV_NAME__, __HELPER_ENV_VALUE__)
def path_state(path):
    # exists() collapses PermissionError into False: that is not absence.
    try:
        os.lstat(path)
        return "present"
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "inaccessible"
    except OSError as exc:
        return "error:" + type(exc).__name__

for path in ("/var/run/docker.sock", "/root/.ssh"):
    result["path:" + path] = path_state(path)
try:
    sock = socket.create_connection(("1.1.1.1", 80), timeout=3)
    sock.close()
    result["network"] = "connected"
except OSError as exc:
    result["network"] = "denied:" + type(exc).__name__
# /var/tmp is not one of the contract's writable tmpfs mounts. Test a
# directory writable by an unprivileged user if the root mount were writable;
# permission-denied or a missing path must never stand in for read-only.
try:
    result["root_readonly"] = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
    result["var_tmp_readonly"] = bool(os.statvfs("/var/tmp").f_flag & os.ST_RDONLY)
    mode = os.lstat("/var/tmp").st_mode
    result["var_tmp_world_writable"] = stat.S_ISDIR(mode) and mode & 0o003 == 0o003
    target = "/var/tmp/raawaa-rootfs-proof-" + str(os.getpid())
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        result["root_write_errno"] = exc.errno
    else:
        os.close(fd)
        try:
            os.unlink(target)
        finally:
            result["root_write_errno"] = 0
except OSError:
    # Missing or inaccessible prerequisites make the result inconclusive.
    result.setdefault("root_readonly", None)
    result.setdefault("var_tmp_readonly", None)
    result.setdefault("var_tmp_world_writable", None)
    result.setdefault("root_write_errno", None)
print(json.dumps(result))
"""


# The helper is a real, separate process: it sleeps so the harness can inspect it
# and the sandbox can attempt to read its /proc entry while it is alive.
_HELPER_PROGRAM = "import time\ntime.sleep(600)\n"


def _start_host_helper(marker_name, marker_value, timeout=10.0):
    """Exec a helper process carrying the marker in its initial environment.

    ``subprocess.Popen`` passes ``env`` at exec time, so the marker is genuinely
    part of that process's initial environment rather than something patched in
    afterwards. Returns the ``Popen`` object; the caller must terminate it.
    """
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        marker_name: marker_value,
    }
    process = subprocess.Popen(
        [sys.executable, "-c", _HELPER_PROGRAM],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for exec to complete so /proc/<pid>/environ exists for the control.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(f"/proc/{process.pid}/environ").exists():
            return process
        if process.poll() is not None:
            raise RuntimeError(f"host helper exited early with code {process.returncode}")
        time.sleep(0.02)
    process.kill()
    raise RuntimeError("host helper did not start within the timeout")


def _helper_environ_visible_on_host(pid, marker_name, marker_value):
    """Host-side positive control: the marker must really be in that PID's environ.

    Without this, a sandbox "unreadable" result could be explained by a marker
    that was never there, and the isolation test would pass while proving nothing.
    """
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError as exc:
        raise AssertionError(
            f"host cannot read the helper environment ({type(exc).__name__}); "
            "the sandbox isolation probe would be meaningless"
        ) from None
    needle = f"{marker_name}={marker_value}".encode("utf-8")
    if needle not in raw:
        # Report only that the control failed; never echo environment contents.
        raise AssertionError("host positive control failed: the helper environment lacks the marker")


def _stop_helper(process, timeout=10.0):
    """Terminate the host helper and reap it; never leave a sleeper behind."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)


def _assert_parent_environ_isolated(output):
    """Sandbox must not be able to read the live host helper's environment.

    ``marker_absent`` is *not* containment: a different process could answer for
    the same PID, or the read could land on a recycled PID. Only an explicit
    denial (``unreadable``) or a genuinely gone process (``missing``) counts, and
    both are meaningful only after the host positive control has passed.
    """
    status = output.get("parent_environ")
    if status == "marker_readable":
        raise AssertionError("sandbox read a host process environment: isolation failed")
    if status in ("unreadable", "missing"):
        return
    if status == "marker_absent":
        raise AssertionError(
            "parent_environ=marker_absent is not an isolation proof; the read may "
            "have hit a different process"
        )
    raise AssertionError(f"parent_environ status is not usable evidence: {status!r}")


def _sandbox_available():
    return LIVE and bool(IMAGE) and shutil.which("docker") is not None


def _canary_base_dir(workspace, candidates=None):
    """Return a writable directory outside ``workspace`` that uid 10001 can traverse.

    A canary is only evidence if the sandbox user *could* have read it when the
    boundary fails. A ``0700`` home directory would deny the read no matter what
    the container can see, so such a location is refused instead of silently used.
    """
    resolved_workspace = Path(workspace).resolve()
    for candidate in candidates if candidates is not None else (Path("/var/tmp"), Path.home()):
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            continue
        if resolved == resolved_workspace or resolved.is_relative_to(resolved_workspace):
            continue
        try:
            mode = resolved.stat().st_mode
        except OSError:
            continue
        if not mode & 0o001:  # not traversable by "other" -> denial would be cosmetic
            continue
        if not os.access(resolved, os.W_OK):
            continue
        return resolved
    raise RuntimeError(
        "no world-traversable directory outside the workspace is writable; a canary "
        "there would be denied by permissions rather than by the sandbox"
    )


def _create_canary(base):
    """Create the host-side canary and return ``(path, marker)``.

    The marker is unique per run so a leftover file from an earlier run cannot be
    mistaken for this one, and the file/directory modes are verified because a
    canary the sandbox user could not read proves nothing.
    """
    marker = f"{CANARY_MARKER_PREFIX}-{uuid.uuid4().hex}"
    directory = Path(tempfile.mkdtemp(prefix="raawaa-sandbox-canary-", dir=str(base)))
    os.chmod(directory, 0o755)
    canary = directory / "canary.txt"
    canary.write_text(marker, encoding="utf-8")
    os.chmod(canary, 0o644)
    if canary.stat().st_mode & 0o004 != 0o004 or directory.stat().st_mode & 0o001 != 0o001:
        raise RuntimeError("the canary is not world-readable and world-traversable")
    if canary.read_text(encoding="utf-8") != marker:
        raise RuntimeError("the canary content changed before the probe ran")
    return canary, marker


def _probe_program(canary_path, control_path=CONTROL_PATH, parent_pid=None,
                   helper_env_name=HELPER_ENV_NAME, helper_env_value=""):
    """Substitute exact, repr-quoted values into the probe template.

    ``parent_pid`` must be the PID of the live host helper. It is never defaulted
    to a plausible number: an empty PID makes the probe report ``inconclusive``,
    which fails the isolation assertion instead of producing a false pass.
    """
    sentinels = {
        "__HOST_CANARY_PATH__": repr(str(canary_path)),
        "__CONTROL_PATH__": repr(str(control_path)),
        "__SECRET_MARKER__": repr(SECRET_MARKER),
        "__PARENT_PID__": repr("" if parent_pid is None else str(int(parent_pid))),
        "__HELPER_ENV_NAME__": repr(helper_env_name),
        "__HELPER_ENV_VALUE__": repr(helper_env_value),
    }
    for sentinel in sentinels:
        if sentinel not in PROBE_TEMPLATE:
            # An edit that drops the placeholder would silently produce a probe
            # that reads nothing, and "read nothing" must never look like "denied".
            raise RuntimeError(f"probe template has lost its {sentinel} placeholder")
    program = PROBE_TEMPLATE
    for sentinel, value in sentinels.items():
        program = program.replace(sentinel, value)
    return program


def _python_command(program):
    """Encode a multiline probe into one newline-free Docker argument.

    The sandbox argv policy rejects newlines in every argument; passing the raw
    multiline template would otherwise fail before the container could start.
    """
    encoded = base64.b64encode(program.encode("utf-8")).decode("ascii")
    return ("python3", "-c", f"exec(__import__('base64').b64decode({encoded!r}))")


def _parse_probe_output(completed):
    """Return the probe's JSON object, or ``{}`` when it did not produce one."""
    if completed.returncode != 0:
        return {}
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        return {}
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assert_probe_usable(output):
    """Raise unless the probe ran and its file-read path demonstrably worked.

    This is the anti-false-test gate: a missing key or a broken positive control
    must fail the run instead of reading as containment.
    """
    if not output:
        raise AssertionError("the probe produced no JSON: it may not have run")
    missing = [key for key in REQUIRED_KEYS if key not in output]
    if missing:
        raise AssertionError(f"probe output is missing {missing}; a missing key must not read as containment")
    for key in ("secret_marker_present", "root_readonly", "var_tmp_readonly", "var_tmp_world_writable"):
        if type(output[key]) is not bool:
            raise AssertionError(f"probe output {key} must be a boolean")
    if type(output["root_write_errno"]) is not int:
        raise AssertionError("probe output root_write_errno must be an integer")
    for key in ("path:/var/run/docker.sock", "path:/root/.ssh"):
        if output[key] not in ("present", "missing", "inaccessible"):
            raise AssertionError(f"probe output {key} is not a recognised path state")
    if output["parent_environ"] not in (
            "marker_readable", "marker_absent", "unreadable", "missing", "inconclusive"):
        raise AssertionError(f"probe output parent_environ is not a recognised state: {output['parent_environ']!r}")
    control = str(output["control"])
    if control.startswith("denied:"):
        raise AssertionError(
            f"positive control failed ({control}); the probe cannot read a file it is supposed to see, "
            "so a denial of the host canary would prove nothing"
        )
    if CONTROL_EXPECTED not in control:
        raise AssertionError(
            f"positive control read {control[:80]!r}, which does not contain {CONTROL_EXPECTED!r}"
        )


def _assert_secret_not_in_environ(output):
    """A missing/non-boolean result is not evidence of secret isolation."""
    if type(output.get("secret_marker_present")) is not bool:
        raise AssertionError("probe must explicitly report secret_marker_present as a boolean")
    if output["secret_marker_present"]:
        raise AssertionError("synthetic host secret was present in the sandbox environment")


def _assert_paths_not_accessible(output):
    """Missing and permission-denied are distinct; neither proves host mounts absent."""
    for path in ("/var/run/docker.sock", "/root/.ssh"):
        state = output.get("path:" + path)
        if state not in ("missing", "inaccessible"):
            raise AssertionError(f"sensitive path is present or unobservable: {path} ({state!r})")


def _assert_root_read_only(output):
    """Require EROFS on a normally writable location, not mere EACCES."""
    import errno

    if (output.get("root_readonly") is not True
            or output.get("var_tmp_readonly") is not True
            or output.get("var_tmp_world_writable") is not True
            or type(output.get("root_write_errno")) is not int
            or output["root_write_errno"] != errno.EROFS):
        raise AssertionError("root read-only proof is inconclusive or failed")


def _require_host_network_control():
    """Prove the host can reach the same address before interpreting denial."""
    try:
        with socket.create_connection(("1.1.1.1", 80), timeout=3):
            pass
    except OSError as exc:
        raise AssertionError(
            f"host network positive control failed ({type(exc).__name__}); egress result is inconclusive"
        ) from None


# ---------------------------------------------------------------------------
# Resource limits under pressure (#440 acceptance criterion 5)
# ---------------------------------------------------------------------------
#
# The eight probes above ask "can the task reach something it should not". This
# one asks a different question: when the task *does* saturate what it is allowed
# to use, does the limit hold -- and does the supervisor still clean up a
# descendant it created while saturated? A configured `--memory` flag is not
# evidence; only an allocation that fails is.
#
# Every observation here is a *bounded* attempt: the probes ask for more than the
# limit and record whether the kernel refused, rather than trying to exhaust the
# host. A probe that could take the runner down would produce no evidence at all.

#: How far past each limit the probe tries to go. A limit that is merely
#: configured but not enforced passes the small request and fails this one.
PRESSURE_OVERSHOOT = 4

#: The memory and pids measurements run in their own child process.
#:
#: This is not tidiness. A cgroup memory limit kills the *process group* with
#: SIGKILL; it does not raise ``MemoryError`` in the allocating process, because
#: Python never gets a chance to raise anything. Measured here on the first live
#: dispatch: the pressure probe died with SIGKILL, so its output was destroyed and
#: six tests failed while ``the_wall_clock_holds`` passed -- the container had
#: done exactly what it promised, and the probe had simply not survived to see it.
#:
#: Running the allocation in a child makes the SIGKILL observable: the parent
#: survives, and "the child was killed while asking for 4x the limit" is a
#: *stronger* statement than "the allocation raised MemoryError".
_PRESSURE_CHILD = r"""
import json, subprocess, sys, time

LIMIT_MIB = int(sys.argv[1])
OVERSHOOT = int(sys.argv[2])

result = {"memory_mib": 0, "memory_method": "anonymous", "pids_held": 0,
          "pids_refused": False}

# --- memory -------------------------------------------------------------
# Anonymous memory, touched page by page. NOT a file: writing to a file at
# increasing offsets measures the filesystem, not the RAM limit, and an earlier
# version of this probe did exactly that -- it "reached 2048 MiB" inside a 32 MiB
# tmpfs because it never grew the file, only rewrote the first block. The live
# test caught it, which is why the number is now a real allocation.
block = 16 * 1024 * 1024
chunks = []
try:
    for _ in range(int(LIMIT_MIB * OVERSHOOT // 16)):
        chunk = bytearray(block)
        for offset in range(0, block, 4096):
            chunk[offset] = 1
        chunks.append(chunk)
        result["memory_mib"] += 16
except MemoryError:
    result["memory_method"] = "MemoryError"
del chunks

# --- process count ------------------------------------------------------
children = []
try:
    for _ in range(OVERSHOOT * 128):
        try:
            children.append(subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"]))
        except (BlockingIOError, OSError):
            result["pids_refused"] = True
            break
except Exception:
    result["pids_refused"] = True
result["pids_held"] = len(children)
for child in children:
    try:
        child.kill()
        child.wait(timeout=5)
    except Exception:
        pass

print(json.dumps(result))
""".strip()

#: The allocation cap inside the container, well under the 512 MiB limit, so a
#: machine with a different page size still reaches the refusal rather than
#: being killed by the OOM killer with no output.
_PRESSURE_PROGRAM = r"""
import base64, json, os, resource, signal, subprocess, sys, time

LIMIT_MIB = {limit_mib}
OVERSHOOT = {overshoot}
CHILD = base64.b64decode({child_b64!r}).decode("utf-8")

result = {{}}

# --- memory and process count, in a child that is allowed to be killed ----
completed = subprocess.run(
    [sys.executable, "-c", CHILD, str(LIMIT_MIB), str(OVERSHOOT)],
    capture_output=True, text=True, timeout=150, check=False,
)
# A negative return code is a signal. Under a cgroup memory limit this is the
# only way the refusal can appear, so it is recorded as the limit doing its job
# rather than as a probe failure.
result["child_signal"] = -completed.returncode if completed.returncode < 0 else None
result["child_killed"] = completed.returncode < 0
observed = {{}}
for line in completed.stdout.strip().splitlines():
    try:
        observed = json.loads(line)
    except ValueError:
        continue
result["memory_mib"] = observed.get("memory_mib")
result["memory_method"] = observed.get("memory_method")
result["pids_held"] = observed.get("pids_held")
result["pids_refused"] = observed.get("pids_refused")

# --- cpu ----------------------------------------------------------------
# Busy-loop for a fixed wall window and compare CPU time to elapsed. On a
# --cpus=1.0 container the ratio cannot meaningfully exceed 1.
started = time.monotonic()
cpu_started = time.process_time()
while time.monotonic() - started < 2.0:
    pass
elapsed = time.monotonic() - started
cpu = time.process_time() - cpu_started
result["cpu_ratio"] = round(cpu / elapsed, 2) if elapsed else None
result["cpu_count_visible"] = len(os.sched_getaffinity(0))

# --- escaped descendant under pressure ----------------------------------
# A child that calls setsid leaves the process group. Under load is exactly
# when a supervisor's cleanup is most likely to be skipped, so the executor's
# subreaper contract is asserted here as well as offline.
child = subprocess.Popen([sys.executable, "-c",
    "import os,time\n"
    "if os.fork()==0:\n"
    "    os.setsid()\n"
    "    time.sleep(60)\n"
    "os._exit(0)\n"])
child.wait()
result["escaped_child_started"] = True

print(json.dumps(result))
""".strip()


def _child_b64() -> str:
    return base64.b64encode(_PRESSURE_CHILD.encode("utf-8")).decode("ascii")


def _pressure_program(limit_mib: int, overshoot: int) -> str:
    """The probe exactly as it is handed to the container."""
    return _PRESSURE_PROGRAM.format(limit_mib=limit_mib, overshoot=overshoot,
                                    child_b64=_child_b64())


class PressureProbeContractTest(unittest.TestCase):
    """The pressure probe's *shape*, tested without a container.

    The live assertions above can only run on a real runner. These lock the
    property that makes them meaningful: the probe must ask for more than the
    limit, and an unbounded host must NOT look bounded. Without this, a probe
    that silently allocated nothing would pass every live assertion.
    """

    def test_the_probe_overshoots_each_limit(self):
        self.assertGreater(PRESSURE_OVERSHOOT, 1,
                           "a probe that asks for exactly the limit cannot detect it")

    def test_the_allocations_are_touched_not_merely_reserved(self):
        # Over-commit means an untouched buffer proves nothing about memory, so
        # every page is written, not merely reserved.
        self.assertIn("chunk[offset] = 1", _PRESSURE_CHILD)
        self.assertIn("range(0, block, 4096)", _PRESSURE_CHILD)

    def test_memory_is_measured_as_anonymous_memory_not_as_a_file(self):
        """The bug the second live dispatch found.

        An earlier version wrote a file at increasing offsets. That measures the
        filesystem: it "reached 2048 MiB" inside a 32 MiB tmpfs because the file
        never grew, so the write loop only rewrote the first block. A RAM limit
        cannot constrain that. Anonymous memory is what --memory governs.
        """
        self.assertNotIn("handle.seek(offset)", _PRESSURE_CHILD)
        self.assertNotIn('open(SCRATCH', _PRESSURE_CHILD)
        self.assertIn("bytearray(block)", _PRESSURE_CHILD)

    def test_the_probe_reaps_the_children_it_creates(self):
        self.assertIn("child.kill()", _PRESSURE_CHILD)
        self.assertIn("child.wait(", _PRESSURE_CHILD)

    def test_the_probe_reports_rather_than_asserting_inside_the_container(self):
        """A crash inside the sandbox produces no evidence at all."""
        program = _pressure_program(512, PRESSURE_OVERSHOOT)
        self.assertNotIn("raise AssertionError", program)
        self.assertIn("json.dumps(result)", program)

    def test_a_killed_child_is_reported_as_a_signal_not_as_silence(self):
        """The fix for the first live failure, locked here.

        A cgroup memory limit SIGKILLs the group; it never raises MemoryError in
        the allocating process. If the probe did its allocation in the parent it
        would die and destroy its own output -- which is exactly what happened on
        dispatch 42377430224, where six pressure probes failed while the wall
        clock passed. The child must run separately so the kill is observable.
        """
        program = _pressure_program(512, PRESSURE_OVERSHOOT)
        self.assertIn("child_killed", program)
        self.assertIn("child_signal", program)
        self.assertIn("completed.returncode < 0", program)

    def test_an_unbounded_host_does_not_look_bounded(self):
        """The negative control, run for real on this machine.

        On a host with no limits the probe must report numbers *above* the
        thresholds the live tests assert. If it reported bounded values here,
        the live assertions would pass for the wrong reason.
        """
        program = _pressure_program(64, PRESSURE_OVERSHOOT)
        completed = subprocess.run([sys.executable, "-c", program],
                                   capture_output=True, text=True, timeout=180, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
        # The live test asserts memory < 128 MiB for a 64 MiB limit; here, with no
        # limit, it must not look bounded.
        self.assertFalse(observed["child_killed"],
                         "an unlimited host reported a killed memory child")
        self.assertEqual(observed["memory_method"], "anonymous")
        self.assertGreaterEqual(observed["memory_mib"], 128,
                                "an unlimited host reported a bounded allocation")
        self.assertFalse(observed["pids_refused"],
                         "a host with no pids limit refused a fork")


@unittest.skipUnless(_sandbox_available(), "no live sandbox: set RAAWAA_SANDBOX_LIVE=1 and RAAWAA_SANDBOX_IMAGE")
class SandboxResourceLimitTest(unittest.TestCase):
    """Criterion 5: the limits hold when the task actually pushes on them."""

    MEMORY_MIB = 512
    CPUS = 1.0
    PIDS = 64

    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parents[2]
        program = _pressure_program(cls.MEMORY_MIB, PRESSURE_OVERSHOOT)
        cls.vector = build_docker_argv(SandboxRequest(
            image=IMAGE,
            command=_python_command(program),
            source_dir=str(cls.workspace),
            limits=SandboxLimits(memory_mb=cls.MEMORY_MIB, cpus=cls.CPUS,
                                  pids=cls.PIDS, wall_seconds=120, scratch_mb=32),
        ))
        assert_sandbox_argv(cls.vector, expected_source_dir=str(cls.workspace))
        started = time.monotonic()
        cls.completed = subprocess.run(cls.vector, capture_output=True, text=True,
                                       timeout=180, check=False)
        cls.elapsed = time.monotonic() - started

    def _observed(self):
        self.assertEqual(self.completed.returncode, 0,
                         f"pressure probe failed: {self.completed.stderr[-800:]}")
        return json.loads(self.completed.stdout.strip().splitlines()[-1])

    def test_the_pressure_probe_actually_ran(self):
        # Without this, every assertion below could pass on an empty result.
        observed = self._observed()
        self.assertIn("memory_mib", observed)
        self.assertIn("cpu_ratio", observed)

    def test_memory_cannot_exceed_the_limit(self):
        observed = self._observed()
        allocated = observed.get("memory_mib")
        # Two acceptable shapes, and both are refusals. Either the kernel killed
        # the allocating child (what a cgroup limit actually does) or Python
        # raised MemoryError before the limit. Silence is neither, and an
        # allocation far past the limit means the --memory flag is decorative.
        if observed.get("child_killed") and allocated is None:
            return  # the limit enforced itself by killing the child
        self.assertIsNotNone(allocated, "the memory child produced no result at all")
        self.assertLess(allocated, self.MEMORY_MIB * 2,
                        f"reached {allocated} MiB against a {self.MEMORY_MIB} MiB limit")
        self.assertGreater(allocated, 0, "the probe allocated nothing")

    def test_the_process_count_is_bounded(self):
        observed = self._observed()
        if observed.get("pids_held") is None:
            # The same child that measures pids may have been killed for memory
            # first. Say so rather than reading a missing number as a pass.
            self.assertTrue(observed.get("child_killed"),
                            "the pids measurement vanished without the child being killed")
            return
        self.assertLessEqual(observed["pids_held"], self.PIDS + 8,
                             "the --pids-limit did not bound the process count")
        self.assertTrue(observed["pids_refused"],
                        "no fork was ever refused, so the pids limit was never reached")

    def test_cpu_is_bounded_to_one_core(self):
        observed = self._observed()
        ratio = observed["cpu_ratio"]
        self.assertIsNotNone(ratio)
        # --cpus=1.0: a busy loop cannot consume more than about one core-second
        # per wall second. Allow scheduling noise, not a second core.
        self.assertLess(ratio, 1.5, f"cpu ratio {ratio} exceeds a one-core budget")

    def test_the_wall_clock_holds(self):
        # The container's own deadline was 120 s; the outer timeout is 180 s.
        self.assertLess(self.elapsed, 180, "the probe ran past its wall clock")

    def test_a_setsid_descendant_is_not_left_running_under_pressure(self):
        """The escapee must not survive the container's own teardown.

        ``--rm`` removes the container, so the check is that the probe returned
        while a 60 s sleeping descendant existed: if the container had waited for
        it, elapsed would approach the wall clock.
        """
        observed = self._observed()
        self.assertTrue(observed["escaped_child_started"])
        self.assertLess(self.elapsed, 120,
                        "a descendant kept the container alive past its work")


@unittest.skipUnless(_sandbox_available(), "no live sandbox: set RAAWAA_SANDBOX_LIVE=1 and RAAWAA_SANDBOX_IMAGE")
class SandboxEscapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parents[2]
        cls.host_canary, cls.canary_marker = _create_canary(_canary_base_dir(cls.workspace))
        # Cleanup is registered before anything else can fail, so a crash in
        # setup does not leave the canary behind for the next run.
        cls.addClassCleanup(shutil.rmtree, cls.host_canary.parent, True)
        if cls.host_canary.resolve().is_relative_to(cls.workspace):
            raise RuntimeError("host canary must be outside the mounted workspace")
        environment_patch = patch.dict(os.environ, {"RAAWAA_PROBE_SECRET": SECRET_MARKER})
        environment_patch.start()
        cls.addClassCleanup(environment_patch.stop)
        # A separate exec'd host process carrying the marker, so the sandbox probe
        # attempts a *parent/bridge* environment read rather than its own.
        cls.helper_marker = f"{HELPER_ENV_PREFIX}-{uuid.uuid4().hex}"
        cls.helper = _start_host_helper(HELPER_ENV_NAME, cls.helper_marker)
        # Registered immediately so a failure below cannot leave a sleeping process.
        cls.addClassCleanup(_stop_helper, cls.helper)
        _helper_environ_visible_on_host(cls.helper.pid, HELPER_ENV_NAME, cls.helper_marker)
        cls.vector = build_docker_argv(SandboxRequest(
            image=IMAGE,
            command=_python_command(_probe_program(
                cls.host_canary,
                parent_pid=cls.helper.pid,
                helper_env_value=cls.helper_marker,
            )),
            source_dir=str(cls.workspace),
            limits=SandboxLimits(memory_mb=512, cpus=1.0, pids=64, wall_seconds=90, scratch_mb=32),
        ))
        assert_sandbox_argv(cls.vector, expected_source_dir=str(cls.workspace))
        cls.completed = subprocess.run(
            cls.vector, capture_output=True, text=True, timeout=120, check=False,
        )
        cls.output = _parse_probe_output(cls.completed)

    def _observed(self):
        """Return the probe output, failing loudly if it is not usable evidence."""
        self.assertEqual(self.completed.returncode, 0, self.completed.stderr[-800:])
        _assert_probe_usable(self.output)
        return self.output

    def test_the_probe_actually_ran(self):
        # A container that never started proves nothing, and a silent pass here
        # would be the worst possible outcome for this file.
        output = self._observed()
        self.assertIn(CONTROL_EXPECTED, str(output["control"]))

    def test_a_host_created_file_outside_the_workspace_is_not_readable(self):
        output = self._observed()
        canary = str(output["host_canary"])
        self.assertTrue(canary.startswith("denied:"), canary[:200])
        self.assertNotIn(self.canary_marker, canary)
        _assert_secret_not_in_environ(output)

    def test_synthetic_environment_canary_detection(self):
        # Negative control within the same sandbox contract. The harmless test
        # marker is inserted by the child itself, not via a Docker --env flag.
        # It demonstrates that this exact image and generated program detect a
        # marker value, without revealing any environment data in output.
        program = ("import os\nos.environ['RAAWAA_SYNTHETIC_CONTROL'] = "
                   + repr(SECRET_MARKER) + "\n" + _probe_program(self.host_canary))
        vector = build_docker_argv(SandboxRequest(
            image=IMAGE, command=_python_command(program), source_dir=str(self.workspace),
            limits=SandboxLimits(memory_mb=512, cpus=1.0, pids=64, wall_seconds=90, scratch_mb=32),
        ))
        assert_sandbox_argv(vector, expected_source_dir=str(self.workspace))
        completed = subprocess.run(vector, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-800:])
        output = _parse_probe_output(completed)
        _assert_probe_usable(output)
        self.assertIs(output["secret_marker_present"], True)
        self.assertNotIn(SECRET_MARKER, completed.stdout)
        with self.assertRaisesRegex(AssertionError, "synthetic host secret"):
            _assert_secret_not_in_environ(output)

    def test_the_host_helper_is_a_real_process_with_the_marker(self):
        # The host-side positive control in its own right: if this fails, the
        # isolation assertion below would be vacuous.
        self.assertIsNone(self.helper.poll(), "host helper must still be alive")
        _helper_environ_visible_on_host(self.helper.pid, HELPER_ENV_NAME, self.helper_marker)

    def test_the_sandbox_cannot_read_a_host_process_environment(self):
        output = self._observed()
        # Interpret the sandbox result only after the host control passes again.
        _helper_environ_visible_on_host(self.helper.pid, HELPER_ENV_NAME, self.helper_marker)
        _assert_parent_environ_isolated(output)
        self.assertNotIn(self.helper_marker, json.dumps(output),
                         "the helper marker must never be echoed back by the probe")

    def test_the_public_network_is_unreachable(self):
        output = self._observed()
        _require_host_network_control()
        self.assertTrue(
            str(output.get("network", "")).startswith("denied"),
            output.get("network"),
        )

    def test_credentials_and_the_docker_socket_are_not_accessible(self):
        output = self._observed()
        _assert_paths_not_accessible(output)

    def test_the_root_filesystem_cannot_be_written(self):
        output = self._observed()
        _assert_root_read_only(output)


if __name__ == "__main__":
    unittest.main()
