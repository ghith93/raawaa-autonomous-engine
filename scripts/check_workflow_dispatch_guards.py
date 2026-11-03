"""Fail CI if a secret-bearing manually dispatched workflow can run off main."""
from __future__ import annotations

import re
from pathlib import Path

MAIN_REF_GUARD = "github.ref == 'refs/heads/main'"
DISPATCH = re.compile(r"(?m)^  workflow_dispatch\\s*:")
SECRET = re.compile(r"\\$\\{\\{\\s*secrets\\.")
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\\s*$")


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
                blocks.append((name, "\\n".join(current)))
            name = match.group(1)
            current = []
        elif name is not None:
            current.append(line)
    if name is not None:
        blocks.append((name, "\\n".join(current)))
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


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    workflow_dir = root / ".github" / "workflows"
    violations = []
    for path in sorted(workflow_dir.glob("*.yml")):
        violations.extend(workflow_violations(str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    if violations:
        print("\\n".join(violations))
        return 1
    print("All secret-bearing workflow_dispatch jobs are restricted to refs/heads/main.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
