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
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from engine.sandbox_policy import SandboxLimits, SandboxRequest, assert_sandbox_argv, build_docker_argv

LIVE = os.environ.get("RAAWAA_SANDBOX_LIVE") == "1"
IMAGE = os.environ.get("RAAWAA_SANDBOX_IMAGE", "")
SECRET_MARKER = "raawaa-probe-secret-do-not-leak"

# Each probe prints a single JSON object. Anything the probe could not even attempt
# is reported as an error string, so the assertion below can tell "denied" from
# "never ran".
PROBE = r"""
import json, os, socket, sys

result = {}
try:
    with open("/proc/1/environ", "rb") as handle:
        result["parent_environ"] = handle.read(4096).decode("utf-8", "replace")[:400]
except OSError as exc:
    result["parent_environ_error"] = type(exc).__name__
try:
    result["own_environ"] = "|".join(sorted(os.environ))[:400]
except OSError as exc:
    result["own_environ_error"] = type(exc).__name__
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


@unittest.skipUnless(_sandbox_available(), "no live sandbox: set RAAWAA_SANDBOX_LIVE=1 and RAAWAA_SANDBOX_IMAGE")
class SandboxEscapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path(__file__).resolve().parents[2]
        # Pollute the parent environment on purpose. If any of it is visible inside
        # the sandbox, the probe will say so.
        os.environ["RAAWAA_PROBE_SECRET"] = SECRET_MARKER
        cls.vector = build_docker_argv(SandboxRequest(
            image=IMAGE,
            command=("python3", "-c", PROBE),
            source_dir=str(cls.workspace),
            limits=SandboxLimits(memory_mb=512, cpus=1.0, pids=64, wall_seconds=90, scratch_mb=32),
        ))
        assert_sandbox_argv(cls.vector)
        completed = subprocess.run(
            cls.vector, capture_output=True, text=True, timeout=120, check=False,
        )
        cls.completed = completed
        cls.output = {}
        if completed.returncode == 0:
            last = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
            cls.output = json.loads(last[-1]) if last else {}

    def test_the_probe_actually_ran(self):
        # A container that never started proves nothing, and a silent pass here
        # would be the worst possible outcome for this file.
        self.assertEqual(self.completed.returncode, 0, self.completed.stderr[-800:])
        self.assertTrue(self.output, "the probe produced no JSON: it may not have run")

    def test_the_parent_environment_is_not_readable(self):
        leaked = self.output.get("parent_environ", "")
        self.assertNotIn(SECRET_MARKER, leaked)
        self.assertNotIn(SECRET_MARKER, self.output.get("own_environ", ""))

    def test_the_public_network_is_unreachable(self):
        self.assertTrue(
            str(self.output.get("network", "")).startswith("denied"),
            self.output.get("network"),
        )

    def test_credentials_and_the_docker_socket_are_absent(self):
        for path in ("/var/run/docker.sock", "/root/.ssh"):
            self.assertFalse(self.output.get("exists:" + path), path)

    def test_the_root_filesystem_cannot_be_written(self):
        self.assertTrue(
            str(self.output.get("root_write", "")).startswith("denied"),
            self.output.get("root_write"),
        )


if __name__ == "__main__":
    unittest.main()
