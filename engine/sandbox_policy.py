"""The execution contract for repository task code (#440).

The Codespaces bridge is not a security boundary: it runs repository-controlled
commands as a child process under the same Unix user, with the same filesystem and
the same ability to read ``/proc/<ppid>/environ``. Clearing a few environment
variables and pointing ``HOME`` at a temporary directory does not change that. The
fix is not a longer list of forbidden commands — fixed command names never made
repository code trusted — it is a disposable boundary around whatever runs.

This module is that boundary, expressed as data rather than as a shell script
sprinkled through the executor. It produces the exact ``docker run`` argument
vector for a task and refuses to produce one that is weaker than the contract. A
sandbox you can only verify by reading the caller is not verifiable at all, so
``assert_sandbox_argv`` re-checks a finished vector: callers that build their own
argv get the same scrutiny as this module's own builder.

What the contract guarantees, and why each flag is there:

``--runtime=runsc``
    gVisor, not the default ``runc``. A plain container shares the host kernel, so
    a kernel bug is an escape; runsc intercepts syscalls in a user-space kernel.
    It needs no virtualization support, which is what makes it usable on an
    unprivileged CI VM.

``--network=none``
    No egress at all, including no loopback route to the host. A task that needs a
    socket does not get one by default; the loopback inside the sandbox is
    gVisor's own netstack.

``--read-only`` plus a bounded tmpfs
    The root filesystem cannot be modified, and the only writable paths are a
    ``noexec,nosuid,nodev`` tmpfs and one explicitly declared scratch directory.

``--cap-drop=ALL``, ``--security-opt=no-new-privileges``, non-root ``--user``
    Even inside the sandbox the task holds no capabilities and cannot gain any,
    and it never runs as uid 0.

``--pids-limit``, ``--memory``/``--memory-swap``, ``--cpus``, ``--ulimit``
    A fork bomb, a memory bomb and a busy loop all end at the same wall as an
    ordinary failure instead of taking the host with them.

No ``--env``, no ``--device``, no credential mounts, no Docker socket
    The environment is the single easiest place to leak a token, so the contract
    has no way to pass one. ``assert_sandbox_argv`` refuses vectors that try.

Wall-clock enforcement is not a Docker flag: the executor must run this under a
timeout and kill the whole process group, then revoke the task lease. That is
stated here so the contract is not mistaken for complete coverage.

Nothing in this module executes anything, reads the environment, or touches the
network. It is pure data construction and validation, which is what makes it
testable without a sandbox.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CEILINGS",
    "NON_ROOT_USER",
    "SANDBOX_RUNTIME",
    "SandboxLimits",
    "SandboxRequest",
    "assert_sandbox_argv",
    "build_docker_argv",
    "reviewed_ref_only",
]

SANDBOX_RUNTIME = "runsc"
# A fixed, non-root identity. The host uid is never reused: uid 0 inside the
# sandbox must not look like uid 0 outside it in any log or file ownership.
NON_ROOT_USER = "10001:10001"
WORKSPACE_MOUNT = "/workspace"
SCRATCH_MOUNT = "/scratch"

# Hard ceilings. A caller may ask for less, never for more: resource limits that
# can be raised by the code they constrain are not limits.
CEILINGS = {
    "memory_mb": 4096,
    "cpus": 4.0,
    "pids": 512,
    "wall_seconds": 1800,
    "scratch_mb": 2048,
}

_DIGEST_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
_BAD_ARG = re.compile(r"[\x00\n\r]")

# Flags that would weaken the contract if they ever appeared in an argument
# vector, whatever produced it.
_FORBIDDEN_FLAGS = {
    "--privileged": "a privileged container has no boundary worth the name",
    "--cap-add": "capabilities are not granted to task code",
    "--device": "host devices are not exposed to task code",
    "--net=host": "host networking disables the egress boundary",
    "--network=host": "host networking disables the egress boundary",
    "--pid=host": "host PID namespace exposes the parent process",
    "--ipc=host": "host IPC namespace exposes shared memory",
    "--userns=host": "host user namespace removes uid separation",
    "--uts=host": "host UTS namespace is never needed here",
}


class SandboxPolicyError(ValueError):
    """A sandbox request that cannot be run within the contract."""


def _bounded_int(name: str, value: object, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxPolicyError(f"{name} must be an integer")
    if value < minimum:
        raise SandboxPolicyError(f"{name} must be at least {minimum}")
    ceiling = CEILINGS[name]
    if value > ceiling:
        raise SandboxPolicyError(f"{name} of {value} exceeds the ceiling of {ceiling}")
    return value


@dataclass(frozen=True)
class SandboxLimits:
    """Resource bounds for one task. Asking for more than ``CEILINGS`` is refused."""

    memory_mb: int = 512
    cpus: float = 1.0
    pids: int = 64
    wall_seconds: int = 120
    scratch_mb: int = 32

    def validated(self) -> "SandboxLimits":
        _bounded_int("memory_mb", self.memory_mb)
        _bounded_int("pids", self.pids)
        _bounded_int("wall_seconds", self.wall_seconds)
        _bounded_int("scratch_mb", self.scratch_mb)
        if isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float)):
            raise SandboxPolicyError("cpus must be a number")
        if not 0 < float(self.cpus) <= CEILINGS["cpus"]:
            raise SandboxPolicyError(
                f"cpus must be greater than 0 and at most {CEILINGS['cpus']}"
            )
        return self


@dataclass(frozen=True)
class SandboxRequest:
    """One task to run. Note what cannot be expressed: no env, no mounts, no caps."""

    image: str
    command: tuple[str, ...]
    source_dir: str
    limits: SandboxLimits = SandboxLimits()

    def validated(self) -> "SandboxRequest":
        if not isinstance(self.image, str) or not _DIGEST_IMAGE.match(self.image):
            # A tag is a moving target: "python:3.12" today is not the image the
            # review approved. Only a digest names the thing that actually runs.
            raise SandboxPolicyError(
                "image must be pinned by digest (name@sha256:<64 hex characters>)"
            )
        if not self.command:
            raise SandboxPolicyError("command must not be empty")
        for arg in self.command:
            if not isinstance(arg, str) or not arg:
                raise SandboxPolicyError("every command argument must be a non-empty string")
            if _BAD_ARG.search(arg):
                raise SandboxPolicyError("command arguments must not contain NUL or newlines")
        if not isinstance(self.source_dir, str) or not self.source_dir.startswith("/"):
            raise SandboxPolicyError("source_dir must be an absolute path")
        normalised = self.source_dir.rstrip("/")
        if normalised in ("", "/") or ".." in normalised.split("/"):
            raise SandboxPolicyError("source_dir must be a specific directory, not / or ..")
        self.limits.validated()
        return self


def build_docker_argv(request: SandboxRequest) -> list[str]:
    """Return the hardened ``docker run`` argument vector for one task.

    The order is fixed so that the vector is testable and diffable: identity and
    boundary first, then limits, then mounts, then the image and its command.
    """
    req = request.validated()
    limits = req.limits
    return [
        "docker", "run", "--rm",
        # boundary
        f"--runtime={SANDBOX_RUNTIME}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={NON_ROOT_USER}",
        # Disable any image-defined entrypoint so it cannot intercept the command.
        "--entrypoint=",
        # limits
        f"--pids-limit={limits.pids}",
        f"--memory={limits.memory_mb}m",
        f"--memory-swap={limits.memory_mb}m",  # no swap headroom above the cap
        f"--cpus={float(limits.cpus):g}",
        "--ulimit=nofile=256:256",
        "--ulimit=core=0:0",
        # Only the bounded scratch directory is writable; /tmp remains read-only.
        f"--mount=type=bind,src={req.source_dir.rstrip('/')},dst={WORKSPACE_MOUNT},readonly",
        f"--tmpfs={SCRATCH_MOUNT}:rw,noexec,nosuid,nodev,size={limits.scratch_mb}m",
        f"--workdir={WORKSPACE_MOUNT}",
        req.image,
        *req.command,
    ]


def assert_sandbox_argv(argv: list[str], *, expected_source_dir: str) -> list[str]:
    """Re-check the complete Docker contract, including exact values and mounts.

    expected_source_dir must come from the trusted checkout resolver, not task
    input. Requiring it makes a hand-built vector unable to swap the approved
    workspace for /, a home directory, runner credentials, or another host path.
    """
    import math
    import posixpath

    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SandboxPolicyError("argv must be a list of strings")
    if len(argv) < 3 or argv[0:2] != ["docker", "run"]:
        raise SandboxPolicyError("argv must begin with 'docker run'")
    if not isinstance(expected_source_dir, str) or not expected_source_dir.startswith("/"):
        raise SandboxPolicyError("expected_source_dir must be an absolute trusted workspace path")
    if _BAD_ARG.search(expected_source_dir):
        raise SandboxPolicyError("expected_source_dir contains an invalid character")
    source = expected_source_dir.rstrip("/")
    if not source or source == "/" or posixpath.normpath(source) != source or ".." in source.split("/"):
        raise SandboxPolicyError("expected_source_dir must be a canonical workspace directory")
    parts = [p for p in source.split("/") if p]
    forbidden_roots = ("/root", "/tmp", "/var", "/etc", "/proc", "/sys", "/dev", "/run",
                       "/usr", "/bin", "/sbin", "/mnt", "/media", "/opt")
    if any(source == root or source.startswith(root + "/") for root in forbidden_roots):
        raise SandboxPolicyError("expected_source_dir is inside a sensitive host path")
    if source == "/home" or source == "/home/runner" or (
        source.startswith("/home/") and not (
            len(parts) >= 5 and parts[:3] == ["home", "runner", "work"]
        )
    ):
        raise SandboxPolicyError("expected_source_dir is not the canonical workspace")

    if any(_BAD_ARG.search(arg) for arg in argv):
        raise SandboxPolicyError("argv arguments must not contain NUL or newlines")

    image_indexes = [i for i, arg in enumerate(argv) if _DIGEST_IMAGE.fullmatch(arg)]
    if len(image_indexes) != 1:
        raise SandboxPolicyError("argv must contain exactly one digest-pinned image")
    image_index = image_indexes[0]
    if image_index < 3 or image_index == len(argv) - 1:
        raise SandboxPolicyError("argv must contain Docker options, an image, and a command")
    options = argv[2:image_index]
    command = argv[image_index + 1:]
    if not command or any(not item for item in command):
        raise SandboxPolicyError("container command must contain non-empty arguments")

    for arg in options:
        for flag, why in _FORBIDDEN_FLAGS.items():
            if arg == flag or arg.startswith(flag + "="):
                raise SandboxPolicyError(f"{flag} is refused: {why}")
        if "docker.sock" in arg:
            raise SandboxPolicyError("a Docker socket mount hands the task the host daemon; refused")
        if arg in ("-v", "--volume") or arg.startswith("--volume="):
            raise SandboxPolicyError("volume flags are refused; use the exact --mount contract")
        if arg == "-e" or arg.startswith("--env") or arg.startswith("-e="):
            raise SandboxPolicyError("the contract has no environment path: secrets cannot be passed in")

    def one_exact(value: str) -> None:
        if options.count(value) != 1:
            raise SandboxPolicyError(f"argv requires exactly one {value}")

    for required in (
        "--rm", f"--runtime={SANDBOX_RUNTIME}", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        f"--user={NON_ROOT_USER}", "--entrypoint=", "--ulimit=nofile=256:256",
        "--ulimit=core=0:0", "--workdir=/workspace",
    ):
        one_exact(required)

    value_prefixes = ("--pids-limit=", "--memory=", "--memory-swap=", "--cpus=",
                      "--mount=", "--tmpfs=")
    allowed_exact = {
        "--rm", f"--runtime={SANDBOX_RUNTIME}", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        f"--user={NON_ROOT_USER}", "--entrypoint=", "--ulimit=nofile=256:256",
        "--ulimit=core=0:0", "--workdir=/workspace",
    }
    for arg in options:
        if arg not in allowed_exact and not arg.startswith(value_prefixes):
            raise SandboxPolicyError(f"unexpected Docker option: {arg}")

    def single_value(prefix: str) -> str:
        matches = [arg[len(prefix):] for arg in options if arg.startswith(prefix)]
        if len(matches) != 1 or not matches[0]:
            raise SandboxPolicyError(f"argv requires exactly one {prefix}<value>")
        return matches[0]

    def bounded_number(name: str, raw: str, ceiling: float, suffix: str = "") -> float:
        if suffix:
            if not raw.endswith(suffix):
                raise SandboxPolicyError(f"{name} must use {suffix} units")
            raw = raw[:-len(suffix)]
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
            raise SandboxPolicyError(f"{name} must be a positive numeric value")
        value = float(raw)
        if not math.isfinite(value) or value <= 0 or value > ceiling:
            raise SandboxPolicyError(f"{name} exceeds its allowed positive ceiling")
        return value

    bounded_number("pids", single_value("--pids-limit="), CEILINGS["pids"])
    memory = bounded_number("memory", single_value("--memory="), CEILINGS["memory_mb"], "m")
    swap = bounded_number("memory-swap", single_value("--memory-swap="), CEILINGS["memory_mb"], "m")
    if swap != memory:
        raise SandboxPolicyError("--memory-swap must equal --memory; swap headroom is forbidden")
    bounded_number("cpus", single_value("--cpus="), CEILINGS["cpus"])

    mounts = [arg[len("--mount="):] for arg in options if arg.startswith("--mount=")]
    if len(mounts) != 1:
        raise SandboxPolicyError("exactly one workspace bind mount is required")
    expected_mount = f"type=bind,src={source},dst={WORKSPACE_MOUNT},readonly"
    if mounts[0] != expected_mount:
        raise SandboxPolicyError("bind mount must be exactly the trusted read-only workspace")

    tmpfs = [arg[len("--tmpfs="):] for arg in options if arg.startswith("--tmpfs=")]
    if len(tmpfs) != 1:
        raise SandboxPolicyError("exactly one bounded writable scratch tmpfs is required")
    scratch_match = re.fullmatch(r"/scratch:rw,noexec,nosuid,nodev,size=([0-9]+)m", tmpfs[0])
    if not scratch_match:
        raise SandboxPolicyError("scratch tmpfs must be /scratch with rw,noexec,nosuid,nodev and a size")
    bounded_number("scratch", scratch_match.group(1), CEILINGS["scratch_mb"])

    return argv

def reviewed_ref_only(ref: str, *, allow_pr_heads: bool = False) -> str:
    """Refuse to run task code from anything but reviewed main.

    This is the fallback contract in the same breath as the sandbox: even a real
    boundary should not be handed code the owner never reviewed, so a PR head, a
    fork or a branch ref fails closed by default.
    """
    if allow_pr_heads:
        if not isinstance(ref, str) or not ref.startswith("refs/pull/"):
            raise SandboxPolicyError("allow_pr_heads only permits refs/pull/*")
        return "/workspace"
    if ref != "refs/heads/main":
        raise SandboxPolicyError(
            f"refusing {ref!r}: only reviewed refs/heads/main may execute task code"
        )
    return "/workspace"
