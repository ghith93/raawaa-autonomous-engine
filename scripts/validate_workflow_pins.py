"""Fail CI if any external GitHub Action uses a mutable tag or branch."""
from __future__ import annotations

import re
import sys
from pathlib import Path

USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
FULL_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def find_unpinned_actions(root: Path) -> list[tuple[str, int, str]]:
    workflow_dir = root / ".github" / "workflows"
    problems: list[tuple[str, int, str]] = []
    if not workflow_dir.is_dir():
        return [(".github/workflows", 0, "workflow directory missing")]
    paths = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for match in USES_LINE.finditer(source):
            target = match.group(1).strip().strip('"').strip("'")
            if target.startswith("./") or target.startswith("docker://"):
                continue
            if "@" not in target:
                ref = ""
            else:
                ref = target.rsplit("@", 1)[1]
            if not FULL_COMMIT_SHA.fullmatch(ref):
                line = source.count("\n", 0, match.start()) + 1
                problems.append((str(path.relative_to(root)), line, target))
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    problems = find_unpinned_actions(root)
    if problems:
        for path, line, target in problems:
            print(f"{path}:{line}: external action is not pinned to a full commit SHA: {target}")
        return 1
    count = len(list((root / ".github" / "workflows").glob("*.yml"))) + len(
        list((root / ".github" / "workflows").glob("*.yaml"))
    )
    print(f"All external GitHub Actions are pinned to full commit SHAs across {count} workflows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
