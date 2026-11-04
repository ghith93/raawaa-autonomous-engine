"""Adversarial probes: does the sandbox actually hold?

Everything else in the repository proves intent. This file is the only thing that
can prove a boundary, and it only proves it where a boundary exists — so it is
skipped unless both of these are set:

    RAAWAA_SANDBOX_LIVE=1
    RAAWAA_SANDBOX_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

Run it from ``.github/workflows/sandbox-adversarial.yml``, which installs gVisor on
an ephemeral runner and mounts a temporary workspace containing only a canary file.
The checked-out repository and its `.git` directory are never mounted into the
sandbox. A green run there is evidence; a green run of the rest of the suite is not.

Each probe is a small program that *tries* to escape and reports what it saw. The
harness asserts on its output, so a probe that fails to run at all is a failure
rather than a pass — silence must never read as "contained".
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.sandbox_policy import SandboxLimits, SandboxRequest, assert_sandbox_argv, build_docker_argv

LIVE = os.environ.get("RAAWAA_SANDBOX_LIVE") == "1"
IMAGE = os.environ.get("RAAWAA_SANDBOX_IMAGE", "")
SECRET_MARKER = "raawaa-probe-secret-do-not-leak"
HOST_CANARY_MARKER = "raawaa-host-filesystem-canary-must-not-be-readable"

# Each probe prints a single JSON object. Anything the probe could not even attempt
# is reported as an error string, so the assertion below can tell "denied" from
# "never ran".
PROBE = r"""
import json, os, socket, sys, subprocess

result = {}
try:
    with open(HOST_CANARY_PATH, "r", encoding="utf-8") as handle:
        result["host_canary"] = handle.read(256)
except OSError as exc:
    result["host_canary"] = "denied:" + type(exc).__name__
try:
    with open(HOST_CREDENTIAL_PATH, "r", encoding="utf-8") as handle:
        result["host_credential"] = handle.read(256)
except OSError as exc:
    result["host_credential"] = "denied:" + type(exc).__name__
try:
    with open(SOURCE_CANARY_PATH, "w", encoding="utf-8") as handle:
        handle.write("modified")
    result["source_write"] = "succeeded"
except OSError as exc:
    result["source_write"] = "denied:" + type(exc).__name__
result["host_env_marker_absent"] = os.environ.get("RAAWAA_PROBE_SECRET") != "raawaa-probe-secret-do-not-leak"
try:
    with open("/scratch/probe-canary", "w", encoding="utf-8") as handle:
        handle.write("scratch")
    result["scratch_write"] = "succeeded"
except OSError as exc:
    result["scratch_write"] = "denied:" + type(exc).__name__
try:
    scratch = os.statvfs("/scratch")
    result["scratch_bytes"] = scratch.f_blocks * scratch.f_frsize
    with open("/tmp/probe-canary", "w", encoding="utf-8") as handle:
        handle.write("tmp")
    tmpfs = os.statvfs("/tmp")
    result["tmp_bytes"] = tmpfs.f_blocks * tmpfs.f_frsize
except OSError as exc:
    result["scratch_bytes_error"] = type(exc).__name__
try:
    with open("/sys/fs/cgroup/pids.max", encoding="utf-8") as handle:
        result["pids_max"] = handle.read().strip()
    with open("/sys/fs/cgroup/memory.max", encoding="utf-8") as handle:
        result["memory_max"] = handle.read().strip()
    with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as handle:
        result["cpu_max"] = handle.read().strip()
except OSError as exc:
    result["cgroup_limits_error"] = type(exc).__name__
children = []
try:
    for _ in range(80):
        try:
            children.append(subprocess.Popen(
                ["/bin/sleep", "20"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
        except OSError:
            result["pids_limit_enforced"] = True
            break
    else:
        result["pids_limit_enforced"] = False
finally:
    for child in children:
        child.kill()
    for child in children:
        child.wait(timeout=3)
try:
    usage_before = 0
    with open("/sys/fs/cgroup/cpu.stat", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("usage_usec "):
                usage_before = int(line.split()[1])
    cpu_children = [
        subprocess.Popen(
            [sys.executable, "-c", "import time; end=time.monotonic()+3; x=0\nwhile time.monotonic()<end: x+=1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(2)
    ]
    started = time.monotonic()
    for child in cpu_children:
        child.wait(timeout=10)
    elapsed = time.monotonic() - started
    usage_after = 0
    with open("/sys/fs/cgroup/cpu.stat", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("usage_usec "):
                usage_after = int(line.split()[1])
    result["cpu_cores_used"] = ((usage_after - usage_before) / 1_000_000) / elapsed
except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    result["cpu_probe_error"] = type(exc).__name__
try:
    memory_probe = subprocess.run(
        [sys.executable, "-c", "data=bytearray(600*1024*1024); data[::4096]=b'\\x01'*(len(data)//4096)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False,
    )
    result["memory_limit_enforced"] = memory_probe.returncode != 0
except (OSError, subprocess.TimeoutExpired) as exc:
    result["memory_probe_error"] = type(exc).__name__
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
        cls.workspace_tmp = tempfile.TemporaryDirectory(
            prefix="raawaa-sandbox-workspace-",
            dir=os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(),
        )
        cls.addClassCleanup(cls.workspace_tmp.cleanup)
        cls.workspace = Path(cls.workspace_tmp.name).resolve(strict=True)
        cls.host_credential_dir = tempfile.TemporaryDirectory(
            prefix="raawaa-host-credentials-", dir=Path.home(),
        )
        cls.addClassCleanup(cls.host_credential_dir.cleanup)
        credential_dir = Path(cls.host_credential_dir.name) / ".ssh"
        credential_dir.mkdir()
        cls.host_credential = credential_dir / "id_rsa"
        cls.host_credential.write_text("host-credential-canary", encoding="utf-8")
        cls.addClassCleanup(cls.host_credential.unlink, missing_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="raawaa-host-canary-", dir=Path.home(), delete=False,
            encoding="utf-8",
        ) as handle:
            cls.host_canary = Path(handle.name)
            cls.addClassCleanup(cls.host_canary.unlink, missing_ok=True)
            handle.write(HOST_CANARY_MARKER)
        with tempfile.NamedTemporaryFile(
            mode="w", prefix=".raawaa-source-canary-", dir=cls.workspace,
            delete=False, encoding="utf-8",
        ) as handle:
            cls.source_canary = Path(handle.name)
            cls.addClassCleanup(cls.source_canary.unlink, missing_ok=True)
            handle.write("source-must-remain-immutable")
        if cls.host_canary.is_relative_to(cls.workspace):
            raise RuntimeError("host canary must be outside the mounted workspace")
        os.environ["RAAWAA_PROBE_SECRET"] = SECRET_MARKER
        cls.vector = build_docker_argv(SandboxRequest(
            image=IMAGE,
            command=("python3", "-c", PROBE.replace("HOST_CANARY_PATH", repr(str(cls.host_canary))).replace("HOST_CREDENTIAL_PATH", repr(str(cls.host_credential))).replace("SOURCE_CANARY_PATH", repr(str(cls.source_canary)))),
            source_dir=str(cls.workspace),
            limits=SandboxLimits(memory_mb=512, cpus=1.0, pids=64, wall_seconds=90, scratch_mb=32),
        ))
        assert_sandbox_argv(cls.vector, expected_source_dir=str(cls.workspace))
        completed = subprocess.run(
            cls.vector, capture_output=True, text=True, timeout=120, check=False,
        )
        cls.completed = completed
        cls.output = {}
        if completed.returncode == 0:
            last = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
            cls.output = json.loads(last[-1]) if last else {}

    @classmethod
    def tearDownClass(cls):
        for path_name in ("host_canary", "source_canary"):
            path = getattr(cls, path_name, None)
            if path:
                path.unlink(missing_ok=True)

    def test_the_probe_actually_ran(self):
        # A container that never started proves nothing, and a silent pass here
        # would be the worst possible outcome for this file.
        self.assertEqual(self.completed.returncode, 0, self.completed.stderr[-800:])
        self.assertTrue(self.output, "the probe produced no JSON: it may not have run")

    def test_a_host_created_file_outside_the_workspace_is_not_readable(self):
        self.assertTrue(
            str(self.output.get("host_canary", "")).startswith("denied:"),
            self.output.get("host_canary"),
        )
        self.assertNotIn(HOST_CANARY_MARKER, self.output.get("host_canary", ""))
        self.assertTrue(
            str(self.output.get("host_credential", "")).startswith("denied:"),
            self.output.get("host_credential"),
        )
        self.assertNotIn("host-credential-canary", self.output.get("host_credential", ""))
        self.assertNotIn(SECRET_MARKER, self.output.get("own_environ", ""))

    def test_the_mounted_source_is_immutable(self):
        self.assertTrue(
            str(self.output.get("source_write", "")).startswith("denied:"),
            self.output.get("source_write"),
        )
        self.assertEqual(self.source_canary.read_text(encoding="utf-8"), "source-must-remain-immutable")

    def test_only_bounded_tmpfs_scratch_paths_are_writable(self):
        self.assertEqual(self.output.get("scratch_write"), "succeeded")
        for key in ("scratch_bytes", "tmp_bytes"):
            self.assertGreater(self.output.get(key, 0), 0)
            self.assertLessEqual(self.output.get(key, 0), 33 * 1024 * 1024)

    def test_host_environment_marker_is_not_inherited(self):
        self.assertTrue(self.output.get("host_env_marker_absent"), self.output)

    def test_cgroup_limits_are_present_and_pid_limit_is_enforced(self):
        self.assertEqual(self.output.get("pids_max"), "64", self.output)
        self.assertEqual(self.output.get("memory_max"), str(512 * 1024 * 1024), self.output)
        cpu_parts = str(self.output.get("cpu_max", "")).split()
        self.assertEqual(len(cpu_parts), 2, self.output)
        self.assertLessEqual(int(cpu_parts[0]) / int(cpu_parts[1]), 1.0)
        self.assertTrue(self.output.get("pids_limit_enforced"), self.output)
        self.assertTrue(self.output.get("memory_limit_enforced"), self.output)
        self.assertLessEqual(self.output.get("cpu_cores_used", 99), 1.5, self.output)

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
