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
#: Allocate ``LIMIT_MIB * OVERSHOOT`` MiB of anonymous memory, touched page by
#: page, and print a marker if it survives.
#:
#: On a real run this container is *expected to die*. A --memory limit is
#: enforced by killing the container, and under gVisor the whole sandbox is torn
#: down -- measured across three dispatches: any in-container observer of this
#: event is destroyed along with the evidence. So the memory claim is asserted
#: from the host, on the container's exit status, not from inside.
_MEMORY_PROGRAM = r"""
import json, sys

LIMIT_MIB = int(sys.argv[1])
OVERSHOOT = int(sys.argv[2])

block = 16 * 1024 * 1024
chunks = []
reached = 0
try:
    for _ in range(int(LIMIT_MIB * OVERSHOOT // 16)):
        chunk = bytearray(block)
        for offset in range(0, block, 4096):
            chunk[offset] = 1
        chunks.append(chunk)
        reached += 16
except MemoryError:
    pass
print(json.dumps({"memory_mib": reached, "survived": True}))
""".strip()

#: A small allocation that must succeed. Without it, a container that dies for
#: any reason at all -- wrong runtime, missing image -- would look like the
#: memory limit working.
_MEMORY_CONTROL_PROGRAM = r"""
import json

block = 16 * 1024 * 1024
chunk = bytearray(block)
for offset in range(0, block, 4096):
    chunk[offset] = 1
print(json.dumps({"memory_mib": 16, "control": "allocated"}))
""".strip()

#: Everything that *can* be observed from inside: the pids bound, the cpu bound,
#: and a setsid descendant. No overshoot here, so the container survives to
#: report.
_BEHAVIOUR_PROGRAM = r"""
import json, os, subprocess, sys, time

LIMIT_MIB = int(sys.argv[1])
OVERSHOOT = int(sys.argv[2])

result = {}

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
result.setdefault("pids_refused", False)
result["pids_held"] = len(children)
for child in children:
    try:
        child.kill()
        child.wait(timeout=5)
    except Exception:
        pass

started = time.monotonic()
cpu_started = time.process_time()
while time.monotonic() - started < 2.0:
    pass
elapsed = time.monotonic() - started
cpu = time.process_time() - cpu_started
result["cpu_ratio"] = round(cpu / elapsed, 2) if elapsed else None
result["cpu_count_visible"] = len(os.sched_getaffinity(0))

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


def _with_args(program: str, *values: int) -> str:
    """Prefix the program with the numeric arguments it reads from argv."""
    prefix = f"import sys\nsys.argv = [{program!r}] + {list(values)!r}\n"
    return prefix + program


def _run_pressure_container(program: str, limits: "SandboxLimits", workspace: Path):
    """Run one pressure container from the host and return (completed, elapsed)."""
    vector = build_docker_argv(SandboxRequest(
        image=IMAGE, command=_python_command(program), source_dir=str(workspace),
        limits=limits,
    ))
    assert_sandbox_argv(vector, expected_source_dir=str(workspace))
    started = time.monotonic()
    completed = subprocess.run(vector, capture_output=True, text=True,
                               timeout=limits.wall_seconds + 60, check=False)
    return completed, time.monotonic() - started


class PressureProbeContractTest(unittest.TestCase):
    """The pressure probes' *shape*, tested without a container.

    The live assertions can only run on a real runner. These lock the properties
    that make them meaningful, and each one exists because a dispatch taught it.
    """

    def test_the_probe_overshoots_each_limit(self):
        self.assertGreater(PRESSURE_OVERSHOOT, 1,
                           "a probe that asks for exactly the limit cannot detect it")

    def test_memory_is_measured_as_anonymous_memory_not_as_a_file(self):
        """Dispatch 42378111101: writing a file at increasing offsets measures the
        filesystem. It 'reached 2048 MiB' inside a 32 MiB tmpfs because the file
        never grew, so a --memory limit could never have constrained it."""
        self.assertNotIn("handle.seek", _MEMORY_PROGRAM)
        self.assertIn("bytearray(block)", _MEMORY_PROGRAM)
        self.assertIn("chunk[offset] = 1", _MEMORY_PROGRAM)

    def test_a_positive_control_exists_beside_the_overshoot(self):
        """Dispatch 42377430224: without a control, any container death at all
        would have read as the memory limit working."""
        self.assertIn("control", _MEMORY_CONTROL_PROGRAM)
        self.assertLess(len(_MEMORY_CONTROL_PROGRAM), len(_MEMORY_PROGRAM) + 400)

    def test_the_memory_claim_is_judged_from_the_host_not_inside(self):
        """Dispatch 42378776731: the kill takes the whole sandbox, so no
        in-container observer survives it. The overshoot program must therefore
        not be the one that asserts anything."""
        self.assertNotIn("raise AssertionError", _MEMORY_PROGRAM)
        self.assertNotIn("result[", _MEMORY_PROGRAM)

    def test_the_behavioural_probes_stay_inside_the_limits_so_they_survive(self):
        """The pids, cpu and descendant observations are useless if the container
        dies before printing them."""
        self.assertIn("print(json.dumps(result))", _BEHAVIOUR_PROGRAM)
        self.assertIn("child.kill()", _BEHAVIOUR_PROGRAM)
        self.assertIn("child.wait(", _BEHAVIOUR_PROGRAM)

    def test_the_process_overshoot_is_bounded_so_it_cannot_flood_the_host(self):
        self.assertIn("OVERSHOOT * 128", _BEHAVIOUR_PROGRAM)

    def test_the_reviewed_probe_count_exceeds_the_annotation_ceiling(self):
        """Why a report file exists at all.

        GitHub keeps at most ten annotations per step. Run 42379096458 emitted
        fourteen and the four dropped were exactly the four criterion-5 probes,
        which made a green run impossible to audit. If the count ever drops to
        ten or fewer this test still holds the reason in place, so removing the
        artifact step stays a deliberate decision rather than an oversight.
        """
        from scripts.run_sandbox_adversarial_tests import (
            EXPECTED_METHODS, EXPECTED_PRESSURE_METHODS,
        )
        total = len(EXPECTED_METHODS) + len(EXPECTED_PRESSURE_METHODS)
        self.assertGreater(total, 10,
                           "if the reviewed set fits the ceiling, say so explicitly")

    def test_every_reviewed_probe_has_a_report_entry(self):
        """The report is only authoritative if it covers the whole reviewed set."""
        from scripts.run_sandbox_adversarial_tests import (
            EXPECTED_METHODS, EXPECTED_PRESSURE_METHODS,
        )
        combined = EXPECTED_METHODS | EXPECTED_PRESSURE_METHODS
        self.assertEqual(len(combined), 14)
        self.assertEqual(combined & {"test_memory_overshoot_is_killed_by_the_limit",
                                     "test_the_process_count_is_bounded",
                                     "test_the_positive_control_allocates_and_survives",
                                     "test_the_wall_clock_holds"},
                         {"test_memory_overshoot_is_killed_by_the_limit",
                          "test_the_process_count_is_bounded",
                          "test_the_positive_control_allocates_and_survives",
                          "test_the_wall_clock_holds"},
                         "the four probes GitHub dropped must stay in the reviewed set")

    def test_an_unbounded_host_does_not_look_bounded(self):
        """The negative control, run for real on this machine.

        On a host with no limits the allocation must sail past the threshold the
        live test asserts. If an unlimited machine looked bounded, the live
        assertion would pass for the wrong reason.
        """
        program = _with_args(_MEMORY_PROGRAM, 64, PRESSURE_OVERSHOOT)
        completed = subprocess.run([sys.executable, "-c", program],
                                   capture_output=True, text=True, timeout=180, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(observed["memory_mib"], 128,
                                "an unlimited host reported a bounded allocation")

    def test_a_touched_allocation_really_is_allocated(self):
        """Touching is not decoration: it is what makes over-commit visible."""
        program = _with_args(_MEMORY_PROGRAM, 16, 1)
        completed = subprocess.run([sys.executable, "-c", program],
                                   capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(observed["memory_mib"], 16)
        self.assertTrue(observed["survived"])


@unittest.skipUnless(_sandbox_available(), "no live sandbox: set RAAWAA_SANDBOX_LIVE=1 and RAAWAA_SANDBOX_IMAGE")
class SandboxResourceLimitTest(unittest.TestCase):
    """Criterion 5: the limits hold when the task actually pushes on them.

    Three containers, because one cannot answer both questions. The overshoot run
    is destroyed by the very mechanism it is testing, so it is judged from the
    host by exit status; the behavioural run stays inside its limits, survives,
    and reports the pids and cpu observations itself.
    """

    MEMORY_MIB = 512
    CPUS = 1.0
    PIDS = 64

    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parents[2]
        limits = SandboxLimits(memory_mb=cls.MEMORY_MIB, cpus=cls.CPUS, pids=cls.PIDS,
                               wall_seconds=120, scratch_mb=32)
        # argv placeholders are filled by the container's own argv, so the
        # limit/overshoot values travel as a prefix the program reads.
        cls.control, cls.control_elapsed = _run_pressure_container(
            _MEMORY_CONTROL_PROGRAM, limits, cls.workspace)
        cls.overshoot, cls.overshoot_elapsed = _run_pressure_container(
            _with_args(_MEMORY_PROGRAM, cls.MEMORY_MIB, PRESSURE_OVERSHOOT),
            limits, cls.workspace)
        cls.behaviour, cls.behaviour_elapsed = _run_pressure_container(
            _with_args(_BEHAVIOUR_PROGRAM, cls.MEMORY_MIB, PRESSURE_OVERSHOOT),
            limits, cls.workspace)
        cls.limits = limits

    @staticmethod
    def _json(completed):
        for line in reversed(completed.stdout.strip().splitlines()):
            try:
                return json.loads(line)
            except ValueError:
                continue
        return None

    def test_the_positive_control_allocates_and_survives(self):
        """Without this, any container death would look like the limit working."""
        self.assertEqual(self.control.returncode, 0,
                         f"control run failed: {self.control.stderr[-500:]}")
        observed = self._json(self.control)
        self.assertIsNotNone(observed, "the control produced no output")
        self.assertEqual(observed["control"], "allocated")

    def test_memory_overshoot_is_killed_by_the_limit(self):
        """The memory claim, asserted from the host.

        Three dispatches established that no in-container observer can report
        this: the kill takes the whole sandbox. So the evidence is the container
        failing to exit cleanly *and* failing before its own deadline -- if it
        had exited 0, the --memory flag would be decorative.
        """
        self.assertIsNone(self._json(self.overshoot),
                          "the overshoot container survived and reported; "
                          "the memory limit did not hold")
        self.assertNotEqual(self.overshoot.returncode, 0,
                            "the overshoot container exited successfully")
        self.assertLess(self.overshoot_elapsed, self.limits.wall_seconds,
                        "the container hit its wall clock, not its memory limit")

    def test_the_process_count_is_bounded(self):
        self.assertEqual(self.behaviour.returncode, 0,
                         f"behaviour probe failed: {self.behaviour.stderr[-500:]}")
        observed = self._json(self.behaviour)
        self.assertIsNotNone(observed, "the behaviour probe produced no output")
        self.assertLessEqual(observed["pids_held"], self.PIDS + 8,
                             "the --pids-limit did not bound the process count")
        self.assertTrue(observed["pids_refused"],
                        "no fork was ever refused, so the pids limit was never reached")

    def test_cpu_is_bounded_to_one_core(self):
        self.assertEqual(self.behaviour.returncode, 0,
                         f"behaviour probe failed: {self.behaviour.stderr[-500:]}")
        observed = self._json(self.behaviour)
        self.assertIsNotNone(observed)
        ratio = observed["cpu_ratio"]
        self.assertIsNotNone(ratio)
        self.assertLess(ratio, 1.5, f"cpu ratio {ratio} exceeds a one-core budget")

    def test_the_wall_clock_holds(self):
        for name, elapsed in (("control", self.control_elapsed),
                              ("overshoot", self.overshoot_elapsed),
                              ("behaviour", self.behaviour_elapsed)):
            self.assertLess(elapsed, self.limits.wall_seconds + 60,
                            f"the {name} run outlived its wall clock")

    def test_a_setsid_descendant_is_not_left_running_under_pressure(self):
        self.assertEqual(self.behaviour.returncode, 0,
                         f"behaviour probe failed: {self.behaviour.stderr[-500:]}")
        observed = self._json(self.behaviour)
        self.assertIsNotNone(observed)
        self.assertTrue(observed["escaped_child_started"])
        # If the container had waited for the 60 s descendant, it would have
        # taken far longer than its own work.
        self.assertLess(self.behaviour_elapsed, 120,
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
