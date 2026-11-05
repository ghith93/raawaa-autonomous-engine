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
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from engine.sandbox_policy import SandboxLimits, SandboxRequest, assert_sandbox_argv, build_docker_argv

LIVE = os.environ.get("RAAWAA_SANDBOX_LIVE") == "1"
IMAGE = os.environ.get("RAAWAA_SANDBOX_IMAGE", "")
SECRET_MARKER = "raawaa-probe-secret-do-not-leak"
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
    "root_write",
    "exists:/var/run/docker.sock",
    "exists:/root/.ssh",
)

# Each probe prints a single JSON object. Anything the probe could not even attempt
# is reported as an error string, so the assertion below can tell "denied" from
# "never ran".
PROBE_TEMPLATE = r"""
import json, os, socket


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
for path in ("/var/run/docker.sock", "/root/.ssh", "/home", "/etc/shadow"):
    result["exists:" + path] = os.path.exists(path)
try:
    sock = socket.create_connection(("1.1.1.1", 80), timeout=3)
    sock.close()
    result["network"] = "connected"
except OSError as exc:
    result["network"] = "denied:" + type(exc).__name__
try:
    with open("/escaped", "w") as handle:
        handle.write("x")
    result["root_write"] = "succeeded"
except OSError as exc:
    result["root_write"] = "denied:" + type(exc).__name__
print(json.dumps(result))
"""


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


def _probe_program(canary_path, control_path=CONTROL_PATH):
    """Substitute exact, repr-quoted paths into the probe template."""
    for sentinel in ("__HOST_CANARY_PATH__", "__CONTROL_PATH__", "__SECRET_MARKER__"):
        if sentinel not in PROBE_TEMPLATE:
            # An edit that drops the placeholder would silently produce a probe
            # that reads nothing, and "read nothing" must never look like "denied".
            raise RuntimeError(f"probe template has lost its {sentinel} placeholder")
    program = PROBE_TEMPLATE.replace("__HOST_CANARY_PATH__", repr(str(canary_path)))
    program = program.replace("__CONTROL_PATH__", repr(str(control_path)))
    return program.replace("__SECRET_MARKER__", repr(SECRET_MARKER))


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
    for key in ("secret_marker_present", "exists:/var/run/docker.sock", "exists:/root/.ssh"):
        if type(output[key]) is not bool:
            raise AssertionError(f"probe output {key} must be a boolean")
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
        cls.vector = build_docker_argv(SandboxRequest(
            image=IMAGE,
            command=_python_command(_probe_program(cls.host_canary)),
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

    def test_the_public_network_is_unreachable(self):
        output = self._observed()
        self.assertTrue(
            str(output.get("network", "")).startswith("denied"),
            output.get("network"),
        )

    def test_credentials_and_the_docker_socket_are_absent(self):
        output = self._observed()
        for path in ("/var/run/docker.sock", "/root/.ssh"):
            self.assertFalse(output["exists:" + path], path)

    def test_the_root_filesystem_cannot_be_written(self):
        output = self._observed()
        self.assertTrue(
            str(output.get("root_write", "")).startswith("denied"),
            output.get("root_write"),
        )


if __name__ == "__main__":
    unittest.main()
