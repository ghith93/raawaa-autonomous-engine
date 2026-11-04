"""Refuse to deploy Worker code that needs a schema the database does not have.

On 2026-11-04 the control Worker went live with claim/enqueue SQL that reads
``task_dependencies``, while migration ``0010`` had never been applied to the
production D1 database. Every write then failed with an HTTP 503 whose cause the
Worker discarded, so the queue was dead from 11:06 until the migration was
applied at 12:08 and the failure read ``D1_ERROR: no such table:
task_dependencies``.

Deploy order is the defect: code can reach production ahead of the schema it
depends on, and nothing says so. This module compares the reviewed migration
files in the repository with the migrations the database has recorded, so the
deploy job can fail closed instead of discovering the gap from a silent queue.

No network calls and no writes: it reads two lists and compares them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable


def available_migrations(directory: str | Path) -> list[str]:
    """Return the reviewed migration stems in the repository, oldest first."""
    return sorted(path.stem for path in Path(directory).glob("*.sql"))


def applied_migrations(payload: Any) -> set[str]:
    """Collect every ``name`` recorded by the database, at any nesting depth.

    Wrangler returns ``[{"results": [{"name": "0001_initial"}, ...]}]`` but the
    shape has moved between versions, so this walks the document instead of
    assuming a position. ``.sql`` suffixes are ignored: the repository uses
    stems, the database may keep either.
    """
    names: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "name" and isinstance(item, str) and item.strip():
                    names.add(item.strip().removesuffix(".sql"))
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return names


def pending(directory: str | Path, payload: Any) -> list[str]:
    """Return reviewed migrations the database has not recorded."""
    applied = applied_migrations(payload)
    return [name for name in available_migrations(directory) if name not in applied]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = argv[0] if argv else "migrations"
    source = argv[1] if len(argv) > 1 else None

    available = available_migrations(directory)
    if not available:
        print(f"::error title=D1 migrations::no reviewed migrations found in {directory}")
        return 1

    payload: Any = None
    if source:
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("::error title=D1 migrations::applied-migration list was unreadable")
            return 1
    if payload is None:
        print("::error title=D1 migrations::no applied-migration list was provided")
        return 1

    outstanding = pending(directory, payload)
    if outstanding:
        print(
            "::error title=D1 migrations::refusing to deploy with unapplied migrations: "
            + ", ".join(outstanding)
        )
        return 1

    print(
        f"::notice title=D1 migrations::all {len(available)} reviewed migrations are applied"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
