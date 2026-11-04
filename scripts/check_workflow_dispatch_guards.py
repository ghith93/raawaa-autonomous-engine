"""Fail CI if a secret-bearing manually dispatched workflow can run off main.

Also enforces the production-class rule: a workflow that can change production
state must not be dispatchable at all. Authorization for those changes must be
derived from a reviewed merge to main, never from a manual dispatch, because a
dispatch can be produced by any credential that holds ``actions: write``.

``d1-migrate.yml`` is intentionally not yet in PRODUCTION_CLASS: removing its
dispatch path requires an owner-approved alternative that does not exist today.
Add it in the same change that introduces that alternative.
"""
from __future__ import annotations

import re
from pathlib import Path

MAIN_REF_GUARD = "github.ref == 'refs/heads/main'"
DISPATCH = re.compile(r"(?m)^  workflow_dispatch\s*:")
SECRET = re.compile(r"\$\{\{\s*secrets\.")
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")

# Production-effecting workflows that must never expose a manual dispatch trigger.
PRODUCTION_CLASS: frozenset[str] = frozenset({"deploy-worker.yml"})


def job_blocks(content: str) -> list[tuple[str, str]]:
    lines = content.splitlines()
    jobs_index = next((i for i, line in enumerate(lines) if line == "jobs:"), None)
    if jobs_index is None:
        return []
    blocks: list[tuple[str, str]] = []
    name: str | None = None
    current: list[str] = []
    for line in lines[jobs_index + 1 :]:
        match = JOB_HEADER.match(line)
        if match:
            if name is not None:
                blocks.append((name, "\n".join(current)))
            name = match.group(1)
            current = []
        elif name is not None:
            current.append(line)
    if name is not None:
        blocks.append((name, "\n".join(current)))
    return blocks


def workflow_violations(path: str, content: str) -> list[str]:
    if not DISPATCH.search(content):
        return []
    violations = []
    for job_name, block in job_blocks(content):
        if SECRET.search(block) and MAIN_REF_GUARD not in block:
            violations.append(
                f"{path}: job '{job_name}' uses repository secrets with workflow_dispatch "
                "but lacks an explicit main-ref guard"
            )
    return violations


def production_dispatch_violations(path: str, content: str) -> list[str]:
    """Reject a manual dispatch trigger on any production-effecting workflow."""
    name = Path(path).name
    if name not in PRODUCTION_CLASS:
        return []
    if DISPATCH.search(content):
        return [
            f"{path}: production-effecting workflow must not expose workflow_dispatch; "
            "authorize it from a reviewed main merge instead"
        ]
    return []


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    workflow_dir = root / ".github" / "workflows"
    violations = []
    for path in sorted(workflow_dir.glob("*.yml")):
        relative = str(path.relative_to(root))
        content = path.read_text(encoding="utf-8")
        violations.extend(workflow_violations(relative, content))
        violations.extend(production_dispatch_violations(relative, content))
    if violations:
        print("\n".join(violations))
        return 1
    print("All secret-bearing workflow_dispatch jobs are restricted to refs/heads/main.")
    print("No production-class workflow exposes a manual dispatch trigger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
