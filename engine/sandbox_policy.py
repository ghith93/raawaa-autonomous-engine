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
        # limits
        f"--pids-limit={limits.pids}",
        f"--memory={limits.memory_mb}m",
        f"--memory-swap={limits.memory_mb}m",  # no swap headroom above the cap
        f"--cpus={float(limits.cpus):g}",
        "--ulimit=nofile=256:256",
        "--ulimit=core=0:0",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={limits.scratch_mb}m",
        # mounts: source read-only, one bounded writable scratch
        f"--mount=type=bind,src={req.source_dir.rstrip('/')},dst={WORKSPACE_MOUNT},readonly",
        f"--tmpfs={SCRATCH_MOUNT}:rw,noexec,nosuid,nodev,size={limits.scratch_mb}m",
        f"--workdir={WORKSPACE_MOUNT}",
        req.image,
        *req.command,
    ]


def assert_sandbox_argv(argv: list[str]) -> list[str]:
    """Re-check a finished argument vector against the contract.

    Used on this module's own output and on any vector a caller assembles by hand,
    because the interesting failures are the ones nobody intended: a helpful
    ``--privileged`` added to make a test pass, a ``-v /var/run/docker.sock``
    copied from a CI recipe. Returns the vector so it can be used inline.
    """
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SandboxPolicyError("argv must be a list of strings")
    if len(argv) < 2 or argv[0] != "docker" or argv[1] != "run":
        raise SandboxPolicyError("argv must begin with 'docker run'")

    for arg in argv:
        for flag, why in _FORBIDDEN_FLAGS.items():
            if arg == flag or arg.startswith(flag + "="):
                raise SandboxPolicyError(f"{flag} is refused: {why}")
        if "docker.sock" in arg:
            raise SandboxPolicyError(
                "a Docker socket mount hands the task the host daemon; refused"
            )
        if arg in ("-v", "--volume") or arg.startswith("--volume="):
            # The contract mounts through --mount so that "readonly" is explicit;
            # the -v short form is refused rather than parsed, because a parser is
            # another place for a missing ",ro" to hide.
            raise SandboxPolicyError(
                "volume flags are refused; use --mount with explicit readonly"
            )
        if arg == "-e" or arg.startswith("--env") or arg.startswith("-e="):
            raise SandboxPolicyError(
                "the contract has no environment path: secrets cannot be passed in"
            )
        if arg.startswith("--mount=type=bind") and ",readonly" not in arg:
            raise SandboxPolicyError("every bind mount must be read-only")

    required = (
        f"--runtime={SANDBOX_RUNTIME}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    )
    for flag in required:
        if flag not in argv:
            raise SandboxPolicyError(f"argv is missing {flag}")

    user_flags = [a for a in argv if a.startswith("--user=")]
    if not user_flags:
        raise SandboxPolicyError("argv must set a non-root user")
    if any(u == "--user=0" or u == "--user=0:0" or "root" in u for u in user_flags):
        raise SandboxPolicyError("argv must not run as root")
    for prefix in ("--pids-limit=", "--memory=", "--cpus="):
        if not any(a.startswith(prefix) for a in argv):
            raise SandboxPolicyError(f"argv is missing {prefix}<value>")
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
