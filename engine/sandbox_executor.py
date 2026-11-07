"""Launch one task inside the sandbox container, under a real wall clock.

What this module is
-------------------
The connection between two contracts that already existed and each explicitly
pointed at the other:

* :mod:`engine.sandbox_policy` builds and *verifies* a hardened ``docker run``
  argument vector -- gVisor ``runsc``, ``--network=none``, read-only rootfs,
  ``--cap-drop=ALL``, non-root, pids/memory/cpu bounds, bounded tmpfs scratch --
  and refuses anything it does not recognise. It never starts anything.
* :mod:`engine.process_supervisor` owns a child process: its process group, its
  wall clock, its bounded output, its dispatch ledger, its typed outcome. Its own
  docstring says it "is the supervisor that must drive it -- a sandbox you launch
  without a hard timeout and a group kill is a sandbox with a side door".

Nothing joined them, so the sandbox existed as a verified *description* of a
container that no code could start. This module is that join, and deliberately
nothing more.

What it is *not*
----------------
Not a scheduler, not a queue, not a second supervisor. It registers one more
operation on the supervisor that already exists. It also does not decide
*whether* untrusted code may run: the release flag does, and this module keeps it
closed unless a caller passes ``release_enabled=True`` explicitly.

Three guarantees worth naming
-----------------------------
**The container vector is trusted data, not task input.** The operation's argv is
a tuple written here. A caller supplies no part of it: the supervisor's parameter
mechanism is for filling declared slots inside a program, and a container vector
must not be parameterised at all -- one slot that found its way into ``--mount``
would be a mount the review never approved. So the vector is compared *whole*
against a freshly rebuilt one and then passed as literals.

**Only reviewed refs execute.** :func:`engine.sandbox_policy.reviewed_ref_only`
had no caller until now. :meth:`SandboxExecutor.prepare` calls it first, so a PR
head, a fork or a topic branch is refused before a container is even considered.

**The container's own deadline fires first.** The workflow's fixed limits put the
container's internal timeout well below the supervisor ceiling, so when the
group kill happens we know which bound it was. ``prepare`` refuses a request
where that ordering would be ambiguous.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

from .process_supervisor import (
    CEILINGS as SUPERVISOR_CEILINGS,
    OperationRegistry,
    OperationSpec,
    ProcessSupervisor,
    SupervisorError,
    SupervisorRefusal,
    default_registry,
)
from .sandbox_policy import (
    SandboxPolicyError,
    SandboxRequest,
    assert_sandbox_argv,
    build_docker_argv,
    reviewed_ref_only,
)

__all__ = [
    "SANDBOX_OPERATION",
    "SandboxExecutor",
    "SandboxExecutorError",
    "SandboxPlan",
    "sandbox_registry",
]

#: The one operation this module adds. Kept as a constant so a caller cannot
#: casually run a container by naming a different operation.
SANDBOX_OPERATION = "sandbox_run"

#: Headroom between the container's internal timeout and the supervisor's
#: ceiling. Positive on purpose: see the module docstring.
KILL_GRACE_SECONDS = 30


class SandboxExecutorError(RuntimeError):
    """The sandbox was refused or could not be launched.

    Raised only before or instead of a container start, so a caller can treat it
    as "nothing ran". A container that started and misbehaved comes back as the
    supervisor's :class:`ProcessOutcome` with a classification instead.
    """


@dataclass(frozen=True)
class SandboxPlan:
    """What was verified, so a refusal or a run can be explained afterwards."""

    argv: tuple[str, ...]
    image: str
    wall_seconds: int
    ref: str

    def summary(self) -> dict[str, Any]:
        """Small, log-safe. No argv, no host paths, no environment."""
        return {"image": self.image, "wall_seconds": self.wall_seconds, "ref": self.ref}


def _docker_executable(binary: str) -> str:
    """Resolve the container runtime, or explain that this is configuration.

    A bare name goes through ``PATH``; an absolute path must itself be a runnable
    file. The absolute form exists so a test can point at a stand-in instead of
    manipulating ``PATH``, which would make "which docker did we run" ambiguous
    in exactly the situation where that question matters.
    """
    if not isinstance(binary, str) or not binary:
        raise SandboxExecutorError("docker_binary must be a non-empty string")
    if os.sep in binary:
        if not os.path.isabs(binary):
            raise SandboxExecutorError("docker_binary must be absolute when it contains a path")
        if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
            raise SandboxExecutorError(f"{binary!r} is not a runnable file")
        return os.path.realpath(binary)
    resolved = shutil.which(binary)
    if resolved is None:
        # A missing runtime is a configuration fault, not a containment result.
        # Reporting it as an outcome would let "docker vanished" look like
        # "the sandbox held".
        raise SandboxExecutorError(f"{binary!r} is not on PATH")
    return os.path.realpath(resolved)


def sandbox_registry(argv: tuple[str, ...], *, wall_seconds: float,
                     docker: str | None = None) -> OperationRegistry:
    """The default registry plus one ``docker run`` operation for this vector.

    ``executable`` must be an absolute path to a real file, so the regression
    test points this at a stand-in rather than needing a container runtime; the
    argv comparison in :meth:`SandboxExecutor.prepare` is what guarantees the
    *first* element is that same program.
    """
    if len(argv) < 2 or argv[1] != "run":
        raise SandboxExecutorError("argv must be a 'docker run' vector")
    executable = docker or _docker_executable(argv[0])
    spec = OperationSpec(
        name=SANDBOX_OPERATION,
        executable=executable,
        arguments=tuple(argv[2:]),
        wall_seconds=float(wall_seconds),
        purpose="run one reviewed command inside the hardened sandbox container",
        # A container vector is long and entirely reviewed; the default 16-arg
        # bound is for probes with a few slots.
        max_arguments=SUPERVISOR_CEILINGS["max_arguments"],
    ).validated()
    return default_registry().with_operation(spec)


class SandboxExecutor:
    """Run one reviewed command inside the hardened container.

    ``release_enabled`` defaults to ``False`` and needs an explicit ``True``: the
    sandbox boundary and "is this feature released" are different questions, and
    conflating them is how a flag flipped in a fixture stays flipped in
    production.
    """

    def __init__(self, *, release_enabled: bool = False, docker_binary: str = "docker",
                 ledger: Any = None):
        self.release_enabled = release_enabled is True
        self._docker_binary = docker_binary
        # The ledger is what makes "at most once" true across a crash. Without
        # it the supervisor can only promise that *this* process saw one spawn,
        # so a retry after a killed worker starts a second container for the
        # same task. Callers that can retry must supply one.
        self._ledger = ledger

    @property
    def has_durable_ledger(self) -> bool:
        """Whether a retry after a crash is deduplicated, rather than repeated."""
        return self._ledger is not None

    def prepare(self, request: SandboxRequest, *, ref: str,
                allow_pr_heads: bool = False) -> SandboxPlan:
        """Verify the ref and the container contract. Starts nothing."""
        if not self.release_enabled:
            raise SandboxExecutorError(
                "sandbox execution is disabled: the Codespace release flag remains false"
            )
        try:
            reviewed_ref_only(ref, allow_pr_heads=allow_pr_heads)
        except SandboxPolicyError as exc:
            raise SandboxExecutorError(f"ref refused: {exc}") from None

        try:
            validated = request.validated()
            rebuilt = build_docker_argv(validated)
            assert_sandbox_argv(rebuilt, expected_source_dir=validated.source_dir)
        except SandboxPolicyError as exc:
            raise SandboxExecutorError(f"container contract refused: {exc}") from None

        gate = [arg for arg in rebuilt if arg.startswith("--runtime=")]
        if gate != ["--runtime=runsc"]:
            raise SandboxExecutorError("argv must select the gVisor runtime")

        wall = int(validated.limits.wall_seconds)
        ceiling = float(SUPERVISOR_CEILINGS["wall_seconds"])
        if wall + KILL_GRACE_SECONDS > ceiling:
            raise SandboxExecutorError(
                f"sandbox wall_seconds ({wall}) plus grace ({KILL_GRACE_SECONDS}) exceeds "
                f"the supervisor ceiling ({ceiling:.0f}); the container's own deadline "
                "would not be observable"
            )

        return SandboxPlan(argv=tuple(rebuilt), image=validated.image,
                           wall_seconds=wall, ref=ref)

    def run(self, request: SandboxRequest, *, task_id: str, ref: str, **kwargs: Any):
        """Prepare, then hand the exact vector to a supervisor.

        Returns the supervisor's :class:`ProcessOutcome` unchanged: re-wrapping it
        would create a second place where "succeeded" is decided.
        """
        plan = self.prepare(request, ref=ref)
        docker = _docker_executable(self._docker_binary)
        registry = sandbox_registry(plan.argv, wall_seconds=plan.wall_seconds, docker=docker)
        supervisor = ProcessSupervisor(operations=registry, ledger=self._ledger)
        try:
            return supervisor.run_operation(
                SANDBOX_OPERATION,
                None,
                task_id=task_id,
                wall_seconds=plan.wall_seconds + KILL_GRACE_SECONDS,
                **kwargs,
            )
        except SupervisorRefusal as exc:
            raise SandboxExecutorError(f"supervisor refused the sandbox: {exc}") from None
        except SupervisorError as exc:
            raise SandboxExecutorError(f"supervisor could not run the sandbox: {exc}") from None
