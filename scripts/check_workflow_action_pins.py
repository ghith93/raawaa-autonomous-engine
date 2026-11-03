"""Fail CI when a remote GitHub Actions dependency is not pinned to a full SHA."""
from __future__ import annotations

import re
from pathlib import Path

USES = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def find_unpinned_actions(root: Path) -> list[str]:
    workflows = root / ".github" / "workflows"
    violations: list[str] = []
    for path in sorted(workflows.rglob("*")):
        if path.suffix not in {".yml", ".yaml"} or not path.is_file():
            continue
        for reference in USES.findall(path.read_text(encoding="utf-8")):
            if reference.startswith(("./", "$/")):
                continue
            if "@" not in reference:
                violations.append(f"{path.relative_to(root)}: {reference} (missing ref)")
                continue
            ref = reference.rsplit("@", 1)[1]
            if not FULL_SHA.fullmatch(ref):
                violations.append(f"{path.relative_to(root)}: {reference} (not a full 40-character SHA)")
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = find_unpinned_actions(root)
    if violations:
        print("Unpinned GitHub Actions dependencies:")
        for violation in violations:
            print("- " + violation)
        return 1
    print("All remote GitHub Actions dependencies are pinned to full commit SHAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
