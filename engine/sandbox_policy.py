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


def assert_sandbox_argv(argv: list[str], *, expected_source_dir: str) -> list[str]:
    """Validate exact limits and bind-mount provenance against trusted configuration."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SandboxPolicyError("argv must be a list of strings")
    if len(argv) < 4 or argv[0:2] != ["docker", "run"]:
        raise SandboxPolicyError("argv must begin with 'docker run' and include image and command")
    if not isinstance(expected_source_dir, str) or not expected_source_dir.startswith("/"):
        raise SandboxPolicyError("expected_source_dir must be an absolute trusted workspace path")
    expected_source = expected_source_dir.rstrip("/")
    if expected_source in ("", "/") or ".." in expected_source.split("/"):
        raise SandboxPolicyError("expected_source_dir must be a specific directory")
    image_indexes = [i for i, arg in enumerate(argv[2:], 2) if _DIGEST_IMAGE.fullmatch(arg)]
    if len(image_indexes) != 1:
        raise SandboxPolicyError("argv must contain exactly one digest-pinned image")
    image_index = image_indexes[0]
    if image_index == len(argv) - 1:
        raise SandboxPolicyError("argv must include a command after the image")
    if any(_BAD_ARG.search(arg) for arg in argv):
        raise SandboxPolicyError("argv arguments must not contain NUL or newlines")
    options = argv[2:image_index]
    for flag, why in _FORBIDDEN_FLAGS.items():
        if any(arg == flag or arg.startswith(flag + "=") for arg in options):
            raise SandboxPolicyError(f"{flag} is refused: {why}")
    if any("docker.sock" in arg for arg in options):
        raise SandboxPolicyError("a Docker socket mount hands the task the host daemon; refused")
    if any(arg in ("-v", "--volume") or arg.startswith("--volume=") for arg in options):
        raise SandboxPolicyError("volume flags are refused; use the exact approved --mount")
    if any(arg == "-e" or arg.startswith("--env") or arg.startswith("-e=") for arg in options):
        raise SandboxPolicyError("the contract has no environment path: secrets cannot be passed in")

    users = [arg for arg in options if arg.startswith("--user=")]
    if len(users) != 1:
        raise SandboxPolicyError("argv must contain exactly one user identity")
    if users[0] in ("--user=0", "--user=0:0", "--user=root") or "root" in users[0]:
        raise SandboxPolicyError("argv must not run as root")
    if users[0] != f"--user={NON_ROOT_USER}":
        raise SandboxPolicyError(f"argv must use the approved non-root user {NON_ROOT_USER}")

    fixed = [
        "--rm", f"--runtime={SANDBOX_RUNTIME}", "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        f"--user={NON_ROOT_USER}", "--workdir=/workspace",
    ]
    for flag in fixed:
        if options.count(flag) != 1:
            raise SandboxPolicyError(f"argv must contain exactly one {flag}")

    def one(prefix: str) -> str:
        values = [arg[len(prefix):] for arg in options if arg.startswith(prefix)]
        if len(values) != 1 or not values[0]:
            raise SandboxPolicyError(f"argv is missing or duplicates {prefix}<value>")
        return values[0]

    try:
        pids_raw = one("--pids-limit=")
        memory_raw = one("--memory=")
        swap_raw = one("--memory-swap=")
        cpus_raw = one("--cpus=")
        pids = int(pids_raw)
        memory = int(memory_raw[:-1]) if memory_raw.endswith("m") else 0
        swap = int(swap_raw[:-1]) if swap_raw.endswith("m") else 0
        cpus = float(cpus_raw)
    except (TypeError, ValueError):
        raise SandboxPolicyError("resource limits must use valid numeric values") from None
    if not 1 <= pids <= CEILINGS["pids"]:
        raise SandboxPolicyError(f"pids limit must be between 1 and {CEILINGS['pids']}")
    if not 1 <= memory <= CEILINGS["memory_mb"]:
        raise SandboxPolicyError(f"memory limit must be between 1m and {CEILINGS['memory_mb']}m")
    if swap != memory:
        raise SandboxPolicyError("memory-swap must exactly equal memory; swap headroom is forbidden")
    if not 0 < cpus <= CEILINGS["cpus"]:
        raise SandboxPolicyError(f"cpus must be greater than 0 and at most {CEILINGS['cpus']}")

    ulimits = [arg for arg in options if arg.startswith("--ulimit=")]
    if sorted(ulimits) != ["--ulimit=core=0:0", "--ulimit=nofile=256:256"]:
        raise SandboxPolicyError("argv must contain only the exact nofile and core ulimits")
    tmpfs = [arg for arg in options if arg.startswith("--tmpfs=")]
    pattern = re.compile(r"--tmpfs=(/tmp|/scratch):rw,noexec,nosuid,nodev,size=(\d+)m")
    sizes = {}
    for arg in tmpfs:
        match = pattern.fullmatch(arg)
        if not match or match.group(1) in sizes:
            raise SandboxPolicyError("tmpfs mounts must use exact bounded /tmp and /scratch options")
        sizes[match.group(1)] = int(match.group(2))
    if set(sizes) != {"/tmp", "/scratch"} or sizes["/tmp"] != sizes["/scratch"]:
        raise SandboxPolicyError("argv must contain matching bounded /tmp and /scratch tmpfs mounts")
    if not 1 <= sizes["/scratch"] <= CEILINGS["scratch_mb"]:
        raise SandboxPolicyError(f"scratch limit must not exceed {CEILINGS['scratch_mb']}m")

    mounts = [arg for arg in options if arg.startswith("--mount=")]
    expected_mount = f"--mount=type=bind,src={expected_source},dst={WORKSPACE_MOUNT},readonly"
    if mounts != [expected_mount]:
        raise SandboxPolicyError("argv must contain only the exact trusted workspace bind mount")

    allowed = set(fixed)
    allowed.update(["--pids-limit=" + pids_raw, "--memory=" + memory_raw,
                    "--memory-swap=" + swap_raw, "--cpus=" + cpus_raw])
    allowed.update(ulimits)
    allowed.update(tmpfs)
    allowed.add(expected_mount)
    unknown = [arg for arg in options if arg not in allowed]
    if unknown:
        raise SandboxPolicyError(f"unrecognized or duplicate sandbox options: {unknown!r}")
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
