"""Bounded, allowlisted supervisor for the engine's first real child processes.

What this module is
-------------------
A single narrow bridge between "the engine decided to run something" and "an
operating-system process exists". Before it, the engine had exactly two
execution stories: a deterministic in-process handler, and the Codespaces
bridge, which ran repository code with the bridge's own uid and could not
interrupt a task once it had started. ``batch_runner`` says so itself: its time
budget is an *admission* budget, because a synchronous task already running
cannot be stopped. This module is the missing piece — the thing that owns a
child process, its whole process group, its wall clock, its output and its
outcome classification.

What it is *not*
----------------
It is **not a sandbox**. A supervised child still runs as the same Unix user,
sees the same filesystem and can read ``/proc/<ppid>/environ``. What is
guaranteed here is *containment of the process*, not isolation of the code:

* the executable and its argument vector come from a registry written in
  trusted code, never from a task payload;
* the child gets a constructed environment, never the parent's;
* the child gets its own session/process group, so the whole tree can be
  signalled at once;
* the wall clock is hard, and expiry terminates then force-kills;
* output is drained, bounded and redacted, so a chatty or hostile child cannot
  fill the disk or publish a token;
* a lost task lease stops the work rather than letting it finish into nothing.

Arbitrary code execution therefore stays disabled. The boundary that makes
untrusted *code* safe lives in :mod:`engine.sandbox_policy` (gVisor + a
throwaway runner), and this module is the supervisor that must drive it — a
sandbox you launch without a hard timeout and a group kill is a sandbox with a
side door.

Why an allowlist rather than a command string
---------------------------------------------
A command string is a program in a language whose interpreter is the shell, so
"sanitising" one is really writing a second parser and hoping it agrees with
``/bin/sh``. Here there is no string to parse: a task names an operation, and
the operation is a tuple that already exists in this file. Parameters may only
fill declared slots, each with a full-match pattern. A parameter can never
become the executable, never become an interpreter's code flag (enforced at
registry construction), and never reach a shell — a shell is refused as an
allowlisted executable outright.

Why the outcome is a classification and not a boolean
-----------------------------------------------------
Exit codes lie about the interesting cases. A process killed by our own timeout
exits ``-9``; a process killed because the lease vanished exits the same way; a
process that exited ``0`` while its children kept running has not finished; and
a process whose result we could not record has an *unknown* result, not a
successful one. So every run resolves to exactly one of ``succeeded``,
``failed``, ``timed_out``, ``cancelled``, ``refused`` or ``ambiguous``, and
callers are expected to treat ``ambiguous`` as "do not retry, investigate".

Why a dispatch ledger
---------------------
A supervisor that can be killed at any instant needs to know what it was doing
when it died. Every run is recorded *before* the process starts, under an id
derived from the task, the operation and the parameters. After a crash the row
is still ``in_flight`` with no evidence of how it ended, so reconciliation
quarantines it instead of replaying it. A duplicate delivery of the same work
gets the recorded outcome and never a second process. That is the whole
idempotency story: the ledger is the existing engine SQLite database with one
more table, not a second queue or a second database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from engine.redaction import contains_secret_shape, redact
from engine.task_store import DEFAULT_DB

__all__ = [
    "CEILINGS",
    "CLASSIFICATIONS",
    "ENV_ALLOWLIST",
    "SUPERVISOR_VERSION",
    "ArgSlot",
    "DispatchLedgerError",
    "DispatchRecord",
    "LeaseFenceLost",
    "LeaseWatchdog",
    "OperationRegistry",
    "OperationSpec",
    "ProcessOutcome",
    "ProcessSupervisor",
    "SqliteDispatchLedger",
    "SupervisorConfigurationError",
    "SupervisorError",
    "SupervisorRefusal",
    "build_child_environment",
    "child_environment_names",
    "default_registry",
    "make_lease_fence",
]

#: Bumped whenever the dispatch identity or the execution semantics change.
#: It is part of the dispatch id, so an upgraded supervisor never mistakes an
#: old record for its own.
SUPERVISOR_VERSION = "process-supervisor:v1"

#: Hard upper bounds. A caller may ask for less, never for more: a limit the
#: constrained code can raise is not a limit.
CEILINGS = {
    "wall_seconds": 900.0,
    "grace_seconds": 10.0,
    "output_bytes": 131072,
    "arguments": 16,
    "operations": 32,
}

#: Byte cap on how much of each stream is *kept*. The reader keeps draining
#: until this multiple, then closes its end, because a parent that stops reading
#: a full pipe deadlocks the child against a goal it can never reach.
DRAIN_OVERFLOW_MULTIPLIER = 16

#: How long the output pipes may stay open after the tracked process is gone
#: before that is treated as evidence of a surviving descendant. A leader that
#: exited cleanly closes its pipes with the exit; only an inherited descriptor
#: keeps them open, and 250 ms is far longer than closing a descriptor takes.
PIPE_CLOSE_GRACE_SECONDS = 0.25

#: Exactly the parent variables a child may see. Anything else — ``GITHUB_TOKEN``,
#: ``ENGINE_API_TOKEN``, ``CLOUDFLARE_API_TOKEN``, provider keys — is simply not
#: copied. ``PYTHONPATH`` is absent on purpose: it is a module-injection path
#: disguised as configuration.
ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
)

#: Variables the supervisor sets itself. A child's home and temporary space are
#: inside the disposable directory the run owns, so "write somewhere harmless"
#: writes somewhere that is deleted.
CHILD_ENV_NAMES: tuple[str, ...] = (
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "CI",
    "LANG",
    "PYTHONIOENCODING",
)

#: Belt and braces over the allowlist: even an allowlisted *name* that looks
#: like a credential is dropped. The allowlist cannot contain one today; the
#: check is here so that a future edit to it cannot quietly reintroduce a leak.
_SECRET_NAME = re.compile(
    r"(?i)(token|secret|passw|credential|api[_-]?key|private[_-]?key|"
    r"authorization|cookie|session|auth)"
)

_BAD_TEXT = re.compile(r"[\x00\n\r]")
_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")

#: Executables that are a language rather than a program. Refused by name (and
#: by resolved basename) because an allowlisted shell turns every later
#: argument into a program again.
_SHELL_BASENAMES = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish", "busybox",
     "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
)

#: --wipe is not a thing; these are the flags that mean "an interpreter will
#: treat the next argument as code". A slot is never allowed to sit there.
_CODE_FLAGS = frozenset({"-c", "-e", "--eval", "-Command", "-EncodedCommand"})

CLASSIFICATIONS = frozenset(
    {"succeeded", "failed", "timed_out", "cancelled", "refused", "ambiguous"}
)


class SupervisorError(RuntimeError):
    """Base class for supervisor failures that are not a caller's mistake."""


class SupervisorConfigurationError(SupervisorError, ValueError):
    """An operation registry entry that cannot be run safely."""


class SupervisorRefusal(SupervisorError, ValueError):
    """A request that was refused before any process was started."""


class DispatchLedgerError(SupervisorError):
    """The dispatch ledger could not confirm what happened; outcome is unknown."""


class LeaseFenceLost(SupervisorError):
    """Task lease ownership could not be confirmed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Operations: the only programs this module will start
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgSlot:
    """One parameter position in an argument vector.

    ``pattern`` is applied with :func:`re.fullmatch`, so it must describe the
    whole value. There is no "sanitise then pass" step: an argument that does
    not match exactly is refused, which keeps the accepted language small
    enough to read.
    """

    name: str
    pattern: str
    description: str = ""

    def validated(self) -> "ArgSlot":
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(self.name):
            raise SupervisorConfigurationError(
                f"argument slot name {self.name!r} must match [a-z][a-z0-9_]*"
            )
        if not isinstance(self.pattern, str) or not self.pattern:
            raise SupervisorConfigurationError(
                f"argument slot {self.name!r} must declare a pattern"
            )
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:  # pragma: no cover - configuration typo
            raise SupervisorConfigurationError(
                f"argument slot {self.name!r} has an invalid pattern: {exc}"
            ) from None
        if compiled.fullmatch("") is not None:
            raise SupervisorConfigurationError(
                f"argument slot {self.name!r} must not accept the empty string"
            )
        return self

    def check(self, value: object) -> str:
        if not isinstance(value, str) or not value:
            raise SupervisorRefusal(
                f"parameter {self.name!r} must be a non-empty string"
            )
        if _BAD_TEXT.search(value):
            raise SupervisorRefusal(
                f"parameter {self.name!r} must not contain NUL or newlines"
            )
        if not re.fullmatch(self.pattern, value):
            raise SupervisorRefusal(
                f"parameter {self.name!r} does not match the declared schema"
            )
        return value


@dataclass(frozen=True)
class OperationSpec:
    """A fixed program plus a fixed argument template.

    ``arguments`` holds literal strings and :class:`ArgSlot` instances. Nothing
    else can appear, so the executable is a property of trusted code and the
    only thing a caller influences is the value inside a declared slot.
    """

    name: str
    executable: str
    arguments: tuple[Any, ...] = ()
    wall_seconds: float = 30.0
    grace_seconds: float = 2.0
    output_bytes: int = 16384
    working_directory: str | None = None
    purpose: str = ""

    def validated(self) -> "OperationSpec":
        if not isinstance(self.name, str) or not _NAME_PATTERN.fullmatch(self.name):
            raise SupervisorConfigurationError(
                f"operation name {self.name!r} must match [a-z][a-z0-9_]*"
            )
        executable = self.executable
        if not isinstance(executable, str) or not executable:
            raise SupervisorConfigurationError(f"{self.name}: executable is required")
        if _BAD_TEXT.search(executable) or not os.path.isabs(executable):
            raise SupervisorConfigurationError(
                f"{self.name}: executable must be an absolute path without NUL or newlines"
            )
        if os.path.basename(executable).lower() in _SHELL_BASENAMES:
            raise SupervisorConfigurationError(
                f"{self.name}: refusing a shell ({os.path.basename(executable)}) as an "
                "allowlisted executable; it turns every argument into a program"
            )
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise SupervisorConfigurationError(
                f"{self.name}: executable {executable!r} is missing or not executable"
            )

        if not isinstance(self.arguments, tuple):
            raise SupervisorConfigurationError(f"{self.name}: arguments must be a tuple")
        if len(self.arguments) > CEILINGS["arguments"]:
            raise SupervisorConfigurationError(
                f"{self.name}: at most {CEILINGS['arguments']} arguments are supported"
            )
        slot_names: set[str] = set()
        for index, item in enumerate(self.arguments):
            if isinstance(item, ArgSlot):
                item.validated()
                if item.name in slot_names:
                    raise SupervisorConfigurationError(
                        f"{self.name}: duplicate argument slot {item.name!r}"
                    )
                slot_names.add(item.name)
                if index and self.arguments[index - 1] in _CODE_FLAGS:
                    raise SupervisorConfigurationError(
                        f"{self.name}: argument slot {item.name!r} may not follow "
                        f"{self.arguments[index - 1]!r}; a caller-supplied value must "
                        "never become interpreted code"
                    )
                continue
            if not isinstance(item, str) or not item:
                raise SupervisorConfigurationError(
                    f"{self.name}: every argument must be a non-empty string or an ArgSlot"
                )
            if _BAD_TEXT.search(item):
                raise SupervisorConfigurationError(
                    f"{self.name}: literal arguments must not contain NUL or newlines"
                )

        if not isinstance(self.wall_seconds, (int, float)) or isinstance(self.wall_seconds, bool):
            raise SupervisorConfigurationError(f"{self.name}: wall_seconds must be a number")
        if not 0 < float(self.wall_seconds) <= CEILINGS["wall_seconds"]:
            raise SupervisorConfigurationError(
                f"{self.name}: wall_seconds must be between 0 and {CEILINGS['wall_seconds']:.0f}"
            )
        if not isinstance(self.grace_seconds, (int, float)) or isinstance(self.grace_seconds, bool):
            raise SupervisorConfigurationError(f"{self.name}: grace_seconds must be a number")
        if not 0 < float(self.grace_seconds) <= CEILINGS["grace_seconds"]:
            raise SupervisorConfigurationError(
                f"{self.name}: grace_seconds must be between 0 and {CEILINGS['grace_seconds']:.0f}"
            )
        if isinstance(self.output_bytes, bool) or not isinstance(self.output_bytes, int):
            raise SupervisorConfigurationError(f"{self.name}: output_bytes must be an integer")
        if not 1 <= self.output_bytes <= CEILINGS["output_bytes"]:
            raise SupervisorConfigurationError(
                f"{self.name}: output_bytes must be between 1 and {CEILINGS['output_bytes']}"
            )
        if self.working_directory is not None:
            directory = self.working_directory
            if not isinstance(directory, str) or not os.path.isabs(directory):
                raise SupervisorConfigurationError(
                    f"{self.name}: working_directory must be an absolute path"
                )
            if os.path.realpath(directory) != directory.rstrip("/"):
                raise SupervisorConfigurationError(
                    f"{self.name}: working_directory must be a canonical path"
                )
            if not os.path.isdir(directory):
                raise SupervisorConfigurationError(
                    f"{self.name}: working_directory {directory!r} does not exist"
                )
        return self

    @property
    def slot_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.arguments if isinstance(item, ArgSlot))

    def build_argv(self, parameters: Mapping[str, Any] | None = None) -> tuple[str, ...]:
        """Resolve the exact argument vector for this call, or refuse it."""
        supplied = dict(parameters or {})
        slots = set(self.slot_names)
        unknown = sorted(set(supplied) - slots)
        if unknown:
            # The important case is a caller that tries to pass "executable",
            # "shell" or "command": there is no such parameter, and inventing
            # one is not a typo to be tolerated.
            raise SupervisorRefusal(
                f"operation {self.name!r} has no parameter(s) {unknown!r}"
            )
        missing = sorted(slots - set(supplied))
        if missing:
            raise SupervisorRefusal(
                f"operation {self.name!r} requires parameter(s) {missing!r}"
            )
        argv: list[str] = [self.executable]
        for item in self.arguments:
            argv.append(item.check(supplied[item.name]) if isinstance(item, ArgSlot) else item)
        if any(_BAD_TEXT.search(arg) for arg in argv):
            raise SupervisorRefusal("argument vector must not contain NUL or newlines")
        if len(argv) - 1 > CEILINGS["arguments"]:
            raise SupervisorRefusal("argument vector is longer than the ceiling")
        return tuple(argv)


class OperationRegistry:
    """The closed set of programs the engine may start. Trusted code only."""

    def __init__(self, operations: Iterable[OperationSpec]):
        specs: dict[str, OperationSpec] = {}
        for spec in operations:
            if not isinstance(spec, OperationSpec):
                raise SupervisorConfigurationError("registry entries must be OperationSpec")
            spec.validated()
            if spec.name in specs:
                raise SupervisorConfigurationError(f"duplicate operation {spec.name!r}")
            specs[spec.name] = spec
        if not specs:
            raise SupervisorConfigurationError("an operation registry must not be empty")
        if len(specs) > CEILINGS["operations"]:
            raise SupervisorConfigurationError(
                f"at most {CEILINGS['operations']} operations are supported"
            )
        self._specs = specs

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def get(self, name: object) -> OperationSpec:
        if not isinstance(name, str) or not name:
            raise SupervisorRefusal("an operation name is required")
        spec = self._specs.get(name)
        if spec is None:
            raise SupervisorRefusal(f"operation {name!r} is not allowlisted")
        return spec

    def describe(self) -> list[dict[str, Any]]:
        """A JSON-safe description for CLI output. Carries no environment."""
        return [
            {
                "operation": spec.name,
                "executable": spec.executable,
                "arguments": [
                    {"literal": item} if isinstance(item, str)
                    else {"slot": item.name, "pattern": item.pattern}
                    for item in spec.arguments
                ],
                "wall_seconds": float(spec.wall_seconds),
                "grace_seconds": float(spec.grace_seconds),
                "output_bytes": spec.output_bytes,
                "working_directory": spec.working_directory,
                "purpose": spec.purpose,
            }
            for spec in (self._specs[name] for name in self.names())
        ]


def _resolve_executable(candidate: str) -> str:
    path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
    if not path:
        raise SupervisorConfigurationError(f"executable {candidate!r} was not found")
    resolved = os.path.realpath(path)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise SupervisorConfigurationError(
            f"executable {candidate!r} does not resolve to a runnable file"
        )
    return resolved


def default_registry(root: str | Path | None = None) -> OperationRegistry:
    """The production allowlist: deterministic, read-only, zero-network probes.

    Every entry here is a *local* question with a local answer — what commit is
    checked out, is the tree clean, what does the interpreter look like. They
    exist so the supervisor can be exercised end to end by the real engine
    against the real repository without a model, a provider, a network call or
    a cent of spend. The registry is expected to grow; it is not expected to
    grow a general "run this command" entry.
    """
    repository_root = os.path.realpath(
        str(root) if root is not None else Path(__file__).resolve().parent.parent
    )
    git = _resolve_executable("git")
    python = _resolve_executable(sys.executable)
    return OperationRegistry(
        [
            OperationSpec(
                name="git_head_revision",
                executable=git,
                arguments=("rev-parse", "HEAD"),
                wall_seconds=30.0,
                output_bytes=4096,
                working_directory=repository_root,
                purpose="Report the checked-out commit; read-only.",
            ),
            OperationSpec(
                name="git_worktree_status",
                executable=git,
                # --no-optional-locks keeps `status` from refreshing the index,
                # so a read-only probe cannot dirty the tree it is inspecting.
                arguments=("--no-optional-locks", "status", "--short", "--branch"),
                wall_seconds=60.0,
                output_bytes=16384,
                working_directory=repository_root,
                purpose="Report worktree cleanliness; read-only.",
            ),
            OperationSpec(
                name="git_recent_subjects",
                executable=git,
                arguments=(
                    "log",
                    "--format=%s",
                    "--max-count",
                    ArgSlot("count", r"(?:20|1[0-9]|[1-9])", "how many commit subjects"),
                ),
                wall_seconds=30.0,
                output_bytes=8192,
                working_directory=repository_root,
                purpose="Report recent commit subjects for a bounded count.",
            ),
            OperationSpec(
                name="python_platform_probe",
                executable=python,
                arguments=("-m", "platform"),
                wall_seconds=30.0,
                output_bytes=4096,
                purpose="Report the interpreter's platform string; read-only, no network.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Child environment
# ---------------------------------------------------------------------------


def build_child_environment(
    isolated_home: str, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Construct the child environment from an allowlist, never from the parent.

    The parent environment is the single easiest place to leak a credential:
    it is inherited by default, it is invisible in the code that starts the
    process, and it is exactly what ``/proc/<ppid>/environ`` exposes to a
    descendant. So nothing is copied wholesale. Nine named variables are
    copied, ``HOME`` and the temporary paths point into the caller's disposable
    directory, and everything else simply does not exist for the child.

    A value that still has a credential *shape* is refused rather than copied.
    Failing closed here is cheap — no allowlisted variable has any business
    carrying a token — and it turns a silent leak into a visible refusal.
    """
    source = os.environ if base is None else base
    if not isinstance(isolated_home, str) or not os.path.isabs(isolated_home):
        raise SupervisorRefusal("isolated_home must be an absolute path")
    if _BAD_TEXT.search(isolated_home):
        raise SupervisorRefusal("isolated_home must not contain NUL or newlines")

    environment: dict[str, str] = {}
    for name in ENV_ALLOWLIST:
        value = source.get(name)
        if not isinstance(value, str) or not value:
            continue
        if _SECRET_NAME.search(name):
            continue
        if _BAD_TEXT.search(value):
            continue
        if contains_secret_shape(value):
            raise SupervisorRefusal(
                f"refusing to inherit {name}: its value has a credential shape"
            )
        environment[name] = value

    # The child's home and temporary space are inside the disposable directory
    # the supervisor owns, so a child that writes "somewhere harmless" writes
    # somewhere that is deleted when the run ends.
    for name in ("HOME", "USERPROFILE", "TMPDIR", "TMP", "TEMP"):
        environment[name] = isolated_home
    environment["CI"] = "true"
    environment.setdefault("LANG", "C.UTF-8")
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def child_environment_names() -> tuple[str, ...]:
    """The variable *names* a child can see. Never the values."""
    return tuple(sorted(set(ENV_ALLOWLIST) | set(CHILD_ENV_NAMES)))


# ---------------------------------------------------------------------------
# Dispatch ledger: durable memory of what was started and what became of it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    task_id: str
    operation: str
    argv_digest: str
    status: str
    classification: str | None
    exit_code: int | None
    output_truncated: bool
    detail: str | None
    created_at: str
    updated_at: str

    @property
    def terminal(self) -> bool:
        return self.status in {"settled", "quarantined"}


class DispatchLedger(Protocol):
    """The three facts a supervisor needs to survive its own death."""

    def begin(
        self,
        dispatch_id: str,
        *,
        task_id: str,
        operation: str,
        argv_digest: str,
        lease_fingerprint: str | None = None,
    ) -> DispatchRecord | None: ...

    def settle(
        self,
        dispatch_id: str,
        *,
        classification: str,
        exit_code: int | None,
        output_truncated: bool,
    ) -> None: ...

    def quarantine(self, dispatch_id: str, detail: str) -> None: ...

    def get(self, dispatch_id: str) -> DispatchRecord | None: ...

    def reconcile(self, stale_after_seconds: float = 900.0) -> int: ...


_DISPATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS process_dispatches (
  dispatch_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  argv_digest TEXT NOT NULL,
  lease_fingerprint TEXT,
  status TEXT NOT NULL CHECK(status IN ('in_flight','settled','quarantined')),
  classification TEXT,
  exit_code INTEGER,
  output_truncated INTEGER NOT NULL DEFAULT 0,
  detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_process_dispatches_status
  ON process_dispatches(status, created_at);
"""


class SqliteDispatchLedger:
    """Dispatch records as one more table in the existing engine database.

    ``engine.task_store`` owns the queue; this owns only the question "was this
    exact external operation already started, and how did it end". Sharing the
    database file rather than standing up a second one is deliberate: a second
    durable store would have to be kept consistent with the lease that makes
    the first one meaningful.
    """

    TABLE = "process_dispatches"

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_DISPATCH_SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _record(row) -> DispatchRecord:
        return DispatchRecord(
            dispatch_id=row["dispatch_id"],
            task_id=row["task_id"],
            operation=row["operation"],
            argv_digest=row["argv_digest"],
            status=row["status"],
            classification=row["classification"],
            exit_code=row["exit_code"],
            output_truncated=bool(row["output_truncated"]),
            detail=row["detail"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def begin(
        self,
        dispatch_id: str,
        *,
        task_id: str,
        operation: str,
        argv_digest: str,
        lease_fingerprint: str | None = None,
    ) -> DispatchRecord | None:
        """Claim the dispatch id, or return the record that already holds it.

        ``INSERT OR IGNORE`` plus a read is the atomic "first writer wins": two
        workers racing for the same id produce exactly one insert, and the
        loser learns what the winner did instead of starting a second process.
        """
        now = _utc_now()
        with self._connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO process_dispatches(dispatch_id,task_id,operation,"
                "argv_digest,lease_fingerprint,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'in_flight',?,?)",
                (dispatch_id, task_id, operation, argv_digest, lease_fingerprint, now, now),
            ).rowcount
            if inserted == 1:
                return None
            row = db.execute(
                "SELECT * FROM process_dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
        if row is None:  # pragma: no cover - the ignored insert must have a row
            raise DispatchLedgerError("dispatch row vanished after a duplicate insert")
        return self._record(row)

    def settle(
        self,
        dispatch_id: str,
        *,
        classification: str,
        exit_code: int | None,
        output_truncated: bool,
    ) -> None:
        if classification not in CLASSIFICATIONS:
            raise DispatchLedgerError(f"unknown classification {classification!r}")
        with self._connect() as db:
            changed = db.execute(
                "UPDATE process_dispatches SET status='settled',classification=?,"
                "exit_code=?,output_truncated=?,updated_at=? "
                "WHERE dispatch_id=? AND status='in_flight'",
                (classification, exit_code, int(bool(output_truncated)), _utc_now(), dispatch_id),
            ).rowcount
        if changed != 1:
            raise DispatchLedgerError(
                f"dispatch {dispatch_id} was not in flight; refusing to settle it twice"
            )

    def quarantine(self, dispatch_id: str, detail: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE process_dispatches SET status='quarantined',detail=?,updated_at=? "
                "WHERE dispatch_id=? AND status='in_flight'",
                (str(detail)[:400], _utc_now(), dispatch_id),
            )

    def get(self, dispatch_id: str) -> DispatchRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM process_dispatches WHERE dispatch_id=?", (dispatch_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def reconcile(self, stale_after_seconds: float = 900.0) -> int:
        """Quarantine in-flight records that no live supervisor can still own.

        An ``in_flight`` row means "a process was started and its outcome was
        never written". The process may have completed, half-completed, or never
        run. Replaying it would duplicate whatever external effect it had, so the
        row becomes ``quarantined`` and the work is left for a human or a
        higher-level decision. The stale window keeps a running supervisor's own
        fresh rows out of a concurrent reconciler's reach.
        """
        if isinstance(stale_after_seconds, bool) or not isinstance(
            stale_after_seconds, (int, float)
        ):
            raise ValueError("stale_after_seconds must be a number")
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must not be negative")
        cutoff = datetime.now(timezone.utc).timestamp() - float(stale_after_seconds)
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self._connect() as db:
            return db.execute(
                "UPDATE process_dispatches SET status='quarantined',"
                "detail='interrupted_before_settlement',updated_at=? "
                "WHERE status='in_flight' AND created_at < ?",
                (_utc_now(), cutoff_iso),
            ).rowcount


# ---------------------------------------------------------------------------
# Lease fencing and cancellation
# ---------------------------------------------------------------------------


def _renew_lease(store: Any, task_id: str, lease_token: str, lease_timeout_seconds: int) -> None:
    heartbeat = getattr(store, "heartbeat", None)
    if not callable(heartbeat):
        raise LeaseFenceLost("task store cannot confirm lease ownership")
    try:
        heartbeat(task_id, lease_token, lease_timeout_seconds)
    except Exception as exc:
        raise LeaseFenceLost(
            f"task lease ownership could not be confirmed: {type(exc).__name__}"
        ) from None


def make_lease_fence(
    store: Any,
    task_id: str,
    lease_token: str,
    *,
    lease_timeout_seconds: int = 900,
) -> Callable[[], None]:
    """Return a fence that renews and confirms the lease, or raises.

    Used on both sides of the external operation. Before: a fence that cannot be
    confirmed means we never start, because the task may already belong to a
    replacement worker. After: a fence that cannot be confirmed means the result
    must not be written anywhere, because writing it would overwrite the state of
    whoever owns the task now. Renewal and confirmation are the same call on
    purpose — a lease that is merely *believed* valid is the failure mode.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(lease_token, str) or not lease_token.strip():
        raise ValueError("lease_token is required")

    def fence() -> None:
        _renew_lease(store, task_id, lease_token, lease_timeout_seconds)

    return fence


class LeaseWatchdog:
    """Renews the task lease from inside the supervisor's wait loop.

    ``LeaseHeartbeat`` runs on its own thread and is right for a handler that
    blocks. A supervisor is a loop, so it can renew at a known point and learn
    the bad news immediately: when renewal fails, ``cancel_requested()`` becomes
    true and the child is stopped rather than allowed to finish work the engine
    can no longer record.
    """

    def __init__(
        self,
        store: Any,
        task_id: str,
        lease_token: str,
        *,
        renew_interval_seconds: float = 30.0,
        lease_timeout_seconds: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        if not isinstance(lease_token, str) or not lease_token.strip():
            raise ValueError("lease_token is required")
        if not 0 < float(renew_interval_seconds):
            raise ValueError("renew_interval_seconds must be positive")
        if float(renew_interval_seconds) >= int(lease_timeout_seconds):
            raise ValueError("renewal interval must be shorter than the lease timeout")
        self.store = store
        self.task_id = task_id
        self.lease_token = lease_token
        self.renew_interval_seconds = float(renew_interval_seconds)
        self.lease_timeout_seconds = int(lease_timeout_seconds)
        self._clock = clock
        self._last_renewal = clock()
        self._lost = False

    @property
    def lease_lost(self) -> bool:
        return self._lost

    def _renew(self) -> None:
        _renew_lease(self.store, self.task_id, self.lease_token, self.lease_timeout_seconds)
        self._last_renewal = self._clock()

    def confirm(self) -> None:
        """Renew now and raise :class:`LeaseFenceLost` if ownership is gone."""
        if self._lost:
            raise LeaseFenceLost("task lease was already lost")
        self._renew()

    def cancel_requested(self) -> bool:
        """True when the lease is gone, so the child must be stopped."""
        if self._lost:
            return True
        if self._clock() - self._last_renewal < self.renew_interval_seconds:
            return False
        try:
            self._renew()
        except LeaseFenceLost:
            self._lost = True
            return True
        return False


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------


@dataclass
class ProcessOutcome:
    """Everything the engine may durably record about one supervised run."""

    classification: str
    operation: str | None = None
    argv: tuple[str, ...] = ()
    exit_code: int | None = None
    signal_number: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    duration_seconds: float = 0.0
    descendants_killed: int = 0
    dispatch_id: str | None = None
    replayed: bool = False
    started: bool = False
    observed_classification: str | None = None
    reason: str | None = None
    fence: dict[str, str] = field(default_factory=dict)
    working_directory: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form. Holds no environment, no lease token, no secrets."""
        if self.classification not in CLASSIFICATIONS:
            raise SupervisorError(f"invalid classification {self.classification!r}")
        return {
            "classification": self.classification,
            "observed_classification": self.observed_classification,
            "operation": self.operation,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "signal_number": self.signal_number,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_truncated": self.output_truncated,
            "duration_seconds": round(self.duration_seconds, 3),
            "descendants_killed": self.descendants_killed,
            "dispatch_id": self.dispatch_id,
            "replayed": self.replayed,
            "started": self.started,
            "reason": self.reason,
            "fence": dict(self.fence),
            "working_directory": self.working_directory,
            "supervisor_version": SUPERVISOR_VERSION,
        }


def _decode_tail(buffer: bytearray, limit: int) -> tuple[str, bool]:
    raw = bytes(buffer[:limit])
    truncated = len(buffer) > limit
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text += "\n[output truncated]"
    return redact(text), truncated


def _drain(stream: Any, buffer: bytearray, keep_limit: int, hard_limit: int,
           state: dict[str, Any], lock: threading.Lock) -> None:
    """Read one pipe to the end, keeping only the first ``keep_limit`` bytes.

    Draining matters as much as bounding: a child blocked writing into a full
    pipe never exits, so the timeout would fire on a process that was actually
    healthy. Past ``hard_limit`` the reader closes its end instead of reading
    forever, which turns an infinite writer into a SIGPIPE rather than a
    supervisor that runs until the wall clock.
    """
    read = getattr(stream, "read", None)
    try:
        while True:
            chunk = read(65536)
            if not chunk:
                return
            with lock:
                remaining = keep_limit - len(buffer)
                if remaining > 0:
                    buffer.extend(chunk[:remaining])
                if len(chunk) > max(0, remaining) or len(buffer) >= keep_limit:
                    state["truncated"] = True
                total = state["bytes"] + len(chunk)
                state["bytes"] = total
                over = total > hard_limit
            if over:
                # Closing is the point: an unbounded writer must hit a wall that
                # is not "the supervisor reads a gigabyte into memory".
                state["closed"] = True
                return
    finally:
        try:
            stream.close()
        except Exception:  # pragma: no cover - closing a broken pipe is not news
            pass


class ProcessSupervisor:
    """Owns one child process at a time, and refuses everything else."""

    def __init__(
        self,
        operations: OperationRegistry | None = None,
        ledger: DispatchLedger | None = None,
        *,
        output_bytes_ceiling: int = CEILINGS["output_bytes"],
        wall_seconds_ceiling: float = CEILINGS["wall_seconds"],
        poll_interval_seconds: float = 0.02,
        drain_grace_seconds: float = 2.0,
        spawn: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        ancestors_scan: Callable[[int], set[int]] | None = None,
    ):
        self.operations = operations if operations is not None else default_registry()
        if not isinstance(self.operations, OperationRegistry):
            raise SupervisorConfigurationError("operations must be an OperationRegistry")
        self.ledger = ledger
        if not 1 <= int(output_bytes_ceiling) <= CEILINGS["output_bytes"]:
            raise SupervisorConfigurationError("output_bytes_ceiling is out of range")
        self.output_bytes_ceiling = int(output_bytes_ceiling)
        if not 0 < float(wall_seconds_ceiling) <= CEILINGS["wall_seconds"]:
            raise SupervisorConfigurationError("wall_seconds_ceiling is out of range")
        self.wall_seconds_ceiling = float(wall_seconds_ceiling)
        if not 0 < float(poll_interval_seconds) <= 1.0:
            raise SupervisorConfigurationError("poll_interval_seconds is out of range")
        self.poll_interval_seconds = float(poll_interval_seconds)
        if not PIPE_CLOSE_GRACE_SECONDS <= float(drain_grace_seconds) <= 30.0:
            raise SupervisorConfigurationError("drain_grace_seconds is out of range")
        self.drain_grace_seconds = float(drain_grace_seconds)
        self._spawn = spawn
        self._clock = clock
        self._group_scan = ancestors_scan or _linux_group_members

    # -- identity ---------------------------------------------------------

    def dispatch_id(
        self,
        task_id: str,
        operation: object,
        parameters: Mapping[str, Any] | None = None,
        *,
        dispatch_key: str | None = None,
    ) -> str:
        """The stable id of one logical external operation.

        It covers the task, the operation, the exact parameter values and the
        supervisor version, so a retry of the same work finds its record while a
        changed parameter or a changed supervisor cannot silently inherit one.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise SupervisorRefusal("task_id is required for a dispatch identity")
        if dispatch_key is not None:
            if not isinstance(dispatch_key, str) or not dispatch_key.strip():
                raise SupervisorRefusal("dispatch_key must be a non-empty string")
            if len(dispatch_key) > 128 or _BAD_TEXT.search(dispatch_key):
                raise SupervisorRefusal("dispatch_key is not acceptable")
            material = {"key": dispatch_key, "version": SUPERVISOR_VERSION}
        else:
            material = {
                "task_id": task_id,
                "operation": operation,
                "parameters": dict(parameters or {}),
                "version": SUPERVISOR_VERSION,
            }
        payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    # -- process group helpers -------------------------------------------

    def _group_members(self, pgid: int) -> set[int]:
        try:
            return {pid for pid in self._group_scan(pgid) if isinstance(pid, int) and pid > 0}
        except OSError:  # pragma: no cover - /proc unreadable
            return set()

    def _signal_group(self, pgid: int, signum: int) -> bool:
        try:
            os.killpg(pgid, signum)
            return True
        except ProcessLookupError:
            return False
        except PermissionError as exc:  # pragma: no cover - same-uid child
            raise SupervisorError(
                f"could not signal process group {pgid}: {type(exc).__name__}"
            ) from None

    def _terminate_group(self, pgid: int, proc: Any, grace: float) -> None:
        """SIGTERM the group, wait ``grace``, then SIGKILL whatever remains.

        Both signals go to the *group*, not to the child, because the child is
        the least interesting process here: a shell wrapper, an interpreter and
        the real worker are three pids, and killing only the first leaves two.
        """
        self._signal_group(pgid, signal.SIGTERM)
        if self._wait_for_group_exit(pgid, proc, grace):
            return
        self._signal_group(pgid, signal.SIGKILL)
        self._wait_for_group_exit(pgid, proc, grace)

    def _wait_for_group_exit(self, pgid: int, proc: Any, grace: float) -> bool:
        deadline = self._clock() + max(0.0, grace)
        # The /proc scan is the expensive half of this loop, and the answer to
        # "are we done yet" changes on the order of a schedule, not 50 times a
        # second. A tenth of a second costs at most ``grace`` extra checks.
        interval = max(self.poll_interval_seconds, 0.1)
        while True:
            exited = proc.poll() is not None
            if exited and not self._group_members(pgid):
                return True
            if self._clock() >= deadline:
                return exited and not self._group_members(pgid)
            time.sleep(interval)

    def _join_readers(self, readers: Sequence[threading.Thread], timeout: float) -> bool:
        deadline = self._clock() + max(0.0, timeout)
        for thread in readers:
            remaining = max(0.0, deadline - self._clock())
            thread.join(remaining)
        return all(not thread.is_alive() for thread in readers)

    # -- execution --------------------------------------------------------

    def _execute(
        self,
        spec: OperationSpec,
        argv: Sequence[str],
        *,
        wall_seconds: float,
        cancel_check: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        isolated_home = tempfile.mkdtemp(prefix="raawaa-supervisor-")
        working_directory = spec.working_directory or isolated_home
        started = self._clock()
        proc: Any = None
        readers: list[threading.Thread] = []
        buffers = [bytearray(), bytearray()]
        lock = threading.Lock()
        state: dict[str, Any] = {"truncated": False, "bytes": 0, "closed": False}
        keep_limit = min(int(spec.output_bytes), self.output_bytes_ceiling)
        hard_limit = keep_limit * DRAIN_OVERFLOW_MULTIPLIER
        timed_out = False
        cancelled_reason: str | None = None
        descendants_killed = 0
        try:
            environment = build_child_environment(isolated_home)
            proc = self._spawn(
                list(argv),
                cwd=working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # A new session is what makes "the whole tree" a real thing to
                # signal: the child becomes its own group leader, and every
                # descendant it creates without explicitly escaping joins it.
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
            pgid = proc.pid
            try:
                pgid = os.getpgid(proc.pid)
            except OSError:  # pragma: no cover - child already gone
                pgid = proc.pid

            for index, stream in enumerate((proc.stdout, proc.stderr)):
                thread = threading.Thread(
                    target=_drain,
                    args=(stream, buffers[index], keep_limit, hard_limit, state, lock),
                    name=f"supervisor-drain-{spec.name}-{index}",
                    daemon=True,
                )
                thread.start()
                readers.append(thread)

            deadline = started + wall_seconds
            while True:
                if proc.poll() is not None:
                    break
                now = self._clock()
                if now >= deadline:
                    timed_out = True
                    break
                if cancel_check is not None:
                    try:
                        if cancel_check():
                            cancelled_reason = "cancelled_by_caller"
                            break
                    except Exception as exc:
                        cancelled_reason = f"cancel_check_failed:{type(exc).__name__}"
                        break
                time.sleep(min(self.poll_interval_seconds, max(0.0, deadline - now)))

            # Measure the tree before touching it, so "how many descendants did
            # we actually clean up" is evidence rather than a claim.
            before = self._group_members(pgid) - {proc.pid}
            leader_exited = proc.poll() is not None
            if not leader_exited or before:
                # Only signal a group we have just seen. ``killpg`` on a
                # recycled pgid would be an unrelated kill, so a clean exit
                # with no surviving descendants is left alone.
                self._terminate_group(pgid, proc, spec.grace_seconds)
            # A descriptor that stays open after the leader is gone belongs to
            # something else in the group: kill it rather than wait out the
            # whole drain grace for a reader that has nothing left to read.
            if not self._join_readers(readers, PIPE_CLOSE_GRACE_SECONDS):
                survivors = self._group_members(pgid) - {proc.pid}
                if survivors:
                    before |= survivors
                    self._signal_group(pgid, signal.SIGKILL)
                self._join_readers(readers, self.drain_grace_seconds)
            after = self._group_members(pgid) - {proc.pid}
            descendants_killed = len(before - after)

            exit_code = proc.returncode
            if exit_code is None:  # pragma: no cover - terminate always reaps
                exit_code = -signal.SIGKILL
            signal_number = -exit_code if exit_code is not None and exit_code < 0 else None
            if cancelled_reason:
                classification = "cancelled"
            elif timed_out:
                classification = "timed_out"
            elif exit_code == 0:
                classification = "succeeded"
            else:
                classification = "failed"

            with lock:
                truncated = bool(state["truncated"])
            stdout, stdout_truncated = _decode_tail(buffers[0], keep_limit)
            stderr, stderr_truncated = _decode_tail(buffers[1], keep_limit)
            return {
                "classification": classification,
                "exit_code": int(exit_code),
                "signal_number": signal_number,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": bool(truncated or stdout_truncated or stderr_truncated),
                "duration_seconds": max(0.0, self._clock() - started),
                "descendants_killed": descendants_killed,
                "working_directory": working_directory,
                "reason": cancelled_reason,
                "started": True,
            }
        finally:
            if proc is not None and proc.poll() is None:  # pragma: no cover
                try:
                    self._signal_group(proc.pid, signal.SIGKILL)
                except SupervisorError:
                    pass
            for thread in readers:
                thread.join(0.1)
            shutil.rmtree(isolated_home, ignore_errors=True)

    # -- public entry point ------------------------------------------------

    def run_operation(
        self,
        operation: object,
        parameters: Mapping[str, Any] | None = None,
        *,
        task_id: str,
        lease_token: str | None = None,
        wall_seconds: float | None = None,
        dispatch_key: str | None = None,
        fence_before: Callable[[], None] | None = None,
        fence_after: Callable[[], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> ProcessOutcome:
        """Run one allowlisted operation, or explain precisely why not.

        A refusal before the dispatch claim (unknown operation, bad schema, lost
        lease, oversized wall clock) leaves **no** ledger record: nothing was
        attempted, so a later retry must still be able to run. A refusal after
        the claim (the environment was refused, or the spawn itself failed) is
        settled as ``refused``: no child existed, and recording the answer makes
        a retry idempotent instead of re-attempting a broken launch in a loop.

        Everything from a successful spawn onwards is recorded before the work
        and settled after it, because that interval is exactly the window in
        which a crash turns a result into a guess.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise SupervisorRefusal("task_id is required")

        try:
            spec = self.operations.get(operation)
            argv = spec.build_argv(parameters)
            resolved_wall = float(spec.wall_seconds if wall_seconds is None else wall_seconds)
            if not 0 < resolved_wall <= self.wall_seconds_ceiling:
                raise SupervisorRefusal(
                    f"wall_seconds must be between 0 and {self.wall_seconds_ceiling:.0f}"
                )
        except SupervisorRefusal as exc:
            return ProcessOutcome(
                classification="refused",
                operation=operation if isinstance(operation, str) else None,
                argv=(),
                reason=str(exc),
            )

        dispatch_id = self.dispatch_id(task_id, operation, parameters, dispatch_key=dispatch_key)
        argv_digest = hashlib.sha256("\x00".join(argv).encode("utf-8")).hexdigest()[:32]

        # Ownership first, record second. A fence checked after the ledger claim
        # would leave an ``in_flight`` row for work that never started, and every
        # later retry would then be quarantined by reconciliation instead of
        # getting a chance to run.
        fence: dict[str, str] = {}
        if fence_before is not None:
            try:
                fence_before()
                fence["before"] = "ok"
            except Exception as exc:
                return ProcessOutcome(
                    classification="refused",
                    operation=spec.name,
                    argv=argv,
                    dispatch_id=dispatch_id,
                    fence=fence,
                    reason=f"lease_fence_lost_before_execution:{type(exc).__name__}",
                )
        else:
            fence["before"] = "not_provided"

        if self.ledger is not None:
            lease_fingerprint = (
                hashlib.sha256(lease_token.encode("utf-8")).hexdigest()[:16]
                if isinstance(lease_token, str) and lease_token
                else None
            )
            try:
                existing = self.ledger.begin(
                    dispatch_id,
                    task_id=task_id,
                    operation=spec.name,
                    argv_digest=argv_digest,
                    lease_fingerprint=lease_fingerprint,
                )
            except Exception as exc:
                # Without a ledger we cannot promise "at most once", and a
                # promise we cannot keep is worse than no execution.
                return ProcessOutcome(
                    classification="refused",
                    operation=spec.name,
                    argv=argv,
                    dispatch_id=dispatch_id,
                    reason=f"dispatch_ledger_unavailable:{type(exc).__name__}",
                )
            if existing is not None:
                return self._replay(spec, argv, existing)

        try:
            executed = self._execute(
                spec, argv, wall_seconds=resolved_wall, cancel_check=cancel_check
            )
        except SupervisorRefusal as exc:
            # Refused by our own construction rules (for example an inherited
            # variable with a credential shape). No child existed, so this is a
            # refusal, and recording it as settled makes a retry return the same
            # answer instead of re-running the check in a loop.
            if self.ledger is not None:
                try:
                    self.ledger.settle(
                        dispatch_id,
                        classification="refused",
                        exit_code=None,
                        output_truncated=False,
                    )
                except Exception:  # pragma: no cover - ledger already broken
                    pass
            return ProcessOutcome(
                classification="refused",
                operation=spec.name,
                argv=argv,
                dispatch_id=dispatch_id,
                fence=fence,
                reason=f"refused_before_spawn:{exc}",
            )
        except Exception as exc:
            # The spawn itself failed, so no child ever ran and nothing outside
            # this process changed: a refusal, not an ambiguous result. It is
            # settled as a refusal so a retry returns the same answer instead of
            # re-attempting a broken spawn in a loop.
            if self.ledger is not None:
                try:
                    self.ledger.settle(
                        dispatch_id,
                        classification="refused",
                        exit_code=None,
                        output_truncated=False,
                    )
                except Exception:  # pragma: no cover - ledger already broken
                    pass
            return ProcessOutcome(
                classification="refused",
                operation=spec.name,
                argv=argv,
                dispatch_id=dispatch_id,
                fence=fence,
                reason=f"spawn_failed:{type(exc).__name__}",
            )

        observed = executed["classification"]
        fence_lost_after = False
        if fence_after is not None:
            try:
                fence_after()
                fence["after"] = "ok"
            except Exception as exc:
                fence_lost_after = True
                fence["after"] = f"lost:{type(exc).__name__}"
        else:
            fence["after"] = "not_provided"

        if fence_lost_after:
            # The result exists and is unrecordable: another worker may now own
            # the task. Say so instead of reporting the exit code as an outcome.
            if self.ledger is not None:
                try:
                    self.ledger.quarantine(dispatch_id, "lease_lost_after_execution")
                except Exception:  # pragma: no cover - ledger already broken
                    pass
            return self._outcome(spec, argv, dispatch_id, executed, fence,
                                 classification="ambiguous",
                                 reason="lease_fence_lost_after_execution")

        if self.ledger is not None:
            try:
                self.ledger.settle(
                    dispatch_id,
                    classification=observed,
                    exit_code=executed["exit_code"],
                    output_truncated=executed["output_truncated"],
                )
            except Exception as exc:
                # The process ran; we cannot prove what we recorded about it.
                return self._outcome(spec, argv, dispatch_id, executed, fence,
                                     classification="ambiguous",
                                     reason=f"dispatch_settle_failed:{type(exc).__name__}")

        return self._outcome(spec, argv, dispatch_id, executed, fence)

    @staticmethod
    def _outcome(
        spec: OperationSpec,
        argv: tuple[str, ...],
        dispatch_id: str,
        executed: dict[str, Any],
        fence: dict[str, str],
        *,
        classification: str | None = None,
        reason: str | None = None,
    ) -> ProcessOutcome:
        """Fold one execution result into the public outcome, without re-deriving it."""
        resolved = classification or executed["classification"]
        return ProcessOutcome(
            classification=resolved,
            observed_classification=(
                executed["classification"] if resolved != executed["classification"] else None
            ),
            operation=spec.name,
            argv=argv,
            exit_code=executed["exit_code"],
            signal_number=executed["signal_number"],
            stdout=executed["stdout"],
            stderr=executed["stderr"],
            output_truncated=executed["output_truncated"],
            duration_seconds=executed["duration_seconds"],
            descendants_killed=executed["descendants_killed"],
            dispatch_id=dispatch_id,
            started=True,
            fence=fence,
            working_directory=executed["working_directory"],
            reason=reason if reason is not None else executed["reason"],
        )

    def _replay(self, spec: OperationSpec, argv: tuple[str, ...], record: DispatchRecord) -> ProcessOutcome:
        """Report what the ledger already knows instead of running it again."""
        if record.status == "settled":
            return ProcessOutcome(
                classification=record.classification or "ambiguous",
                operation=spec.name,
                argv=argv,
                exit_code=record.exit_code,
                output_truncated=record.output_truncated,
                dispatch_id=record.dispatch_id,
                replayed=True,
                started=False,
                reason="replayed_from_ledger",
            )
        # Either the recorded run is in flight somewhere else right now, or it
        # was interrupted and quarantined. Both answers are "do not run it".
        reason = (
            "dispatch_already_in_flight" if record.status == "in_flight"
            else f"dispatch_quarantined:{record.detail or 'unspecified'}"
        )
        return ProcessOutcome(
            classification="ambiguous",
            operation=spec.name,
            argv=argv,
            exit_code=record.exit_code,
            dispatch_id=record.dispatch_id,
            replayed=True,
            started=False,
            reason=reason,
        )

    # -- reconciliation ---------------------------------------------------

    def reconcile(self, stale_after_seconds: float = 900.0) -> int:
        """Quarantine interrupted dispatches. Returns how many were quarantined."""
        if self.ledger is None:
            return 0
        return self.ledger.reconcile(stale_after_seconds=stale_after_seconds)


def _linux_group_members(pgid: int) -> set[int]:
    """Live (non-zombie) pids whose process group is ``pgid``, via /proc.

    Linux answers this from ``/proc/<pid>/stat``; the third field after the
    parenthesised command name is the process group. Zombies are excluded
    because a zombie is a reaping problem, not a running descendant, and
    counting it would report a process as alive forever on a host without an
    init that reaps.
    """
    members: set[int] = set()
    if not os.path.isdir("/proc"):
        return members
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - /proc unreadable
        return members
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as stream:
                raw = stream.read()
        except OSError:
            continue
        try:
            tail = raw.rsplit(b")", 1)[1].split()
            stat_state = tail[0]
            group = int(tail[2])
        except (IndexError, ValueError):  # pragma: no cover - racing exit
            continue
        if group == pgid and stat_state not in (b"Z", b"X", b"x"):
            members.add(int(entry))
    return members
