"""Fail CI if a secret-bearing manually dispatched workflow can run off main.

Also enforces the production-class rule: a workflow that can change production
state must not be dispatchable at all. Authorization for those changes must be
derived from a reviewed merge to main, never from a manual dispatch, because a
dispatch can be produced by any credential that holds ``actions: write``.

Triggers are read by **parsing** the top-level ``on`` mapping rather than by
scanning text. An earlier version used the regular expression
``^  workflow_dispatch\\s*:``, which the reviewer correctly showed to be
bypassable by cosmetic edits:

* ``on:\\n    workflow_dispatch:``      (four-space indent)  — missed
* ``on:\\n  'workflow_dispatch':``      (quoted key)         — missed
* ``on: {workflow_dispatch: {}}``       (flow mapping)       — missed

Each of those is valid YAML and each re-enables the manual path, so the guard
was describing a spelling rather than a behaviour. Parsing removes the class of
error instead of adding another fixture to it.

``d1-migrate.yml`` is intentionally not yet in PRODUCTION_CLASS: removing its
dispatch path requires an owner-approved alternative that does not exist today.
Add it in the same change that introduces that alternative.
"""
from __future__ import annotations

import re
from pathlib import Path

MAIN_REF_GUARD = "github.ref == 'refs/heads/main'"
SECRET = re.compile(r"\$\{\{\s*secrets\.")
JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
TOP_LEVEL_ON = re.compile(r"^(['\"]?)on\1\s*:\s*(.*)$")

# Production-effecting workflows that must never expose a manual dispatch trigger.
PRODUCTION_CLASS: frozenset[str] = frozenset({"deploy-worker.yml"})


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment without touching quoted content."""
    out: list[str] = []
    quote: str | None = None
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out)


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _split_flow(body: str) -> list[str]:
    """Split a YAML flow collection body on top-level commas."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    tail = "".join(current)
    if tail.strip():
        parts.append(tail)
    return parts


def parse_triggers(content: str) -> set[str]:
    """Return the trigger names declared under the workflow's top-level ``on``.

    Handles block mappings, block sequences, flow sequences, flow mappings,
    quoted keys, arbitrary indentation, and inline scalars. Returns an empty set
    for content without an ``on`` key, which callers treat as "no triggers".
    """
    lines = content.splitlines()
    start: int | None = None
    inline = ""

    for index, raw in enumerate(lines):
        if not raw.strip() or raw[:1].isspace():
            continue
        match = TOP_LEVEL_ON.match(_strip_comment(raw))
        if match:
            start = index
            inline = match.group(2).strip()
            break

    if start is None:
        return set()

    if inline:
        if inline.startswith("["):
            body = inline[1 : inline.rfind("]")] if "]" in inline else inline[1:]
            return {name for name in (_unquote(part) for part in _split_flow(body)) if name}
        if inline.startswith("{"):
            body = inline[1 : inline.rfind("}")] if "}" in inline else inline[1:]
            names = set()
            for part in _split_flow(body):
                key = _unquote(part.split(":", 1)[0])
                if key:
                    names.add(key)
            return names
        return {_unquote(inline)}

    triggers: set[str] = set()
    base_indent: int | None = None
    for raw in lines[start + 1 :]:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        if base_indent is None:
            base_indent = indent
        if indent < base_indent:
            break
        body = line.strip()
        if indent != base_indent:
            # Only the first level below ``on`` names triggers; deeper keys are
            # configuration such as ``branches:`` or ``types:``.
            continue
        if body.startswith("- "):
            triggers.add(_unquote(body[2:].split(":", 1)[0]))
            continue
        key = _unquote(body.split(":", 1)[0])
        if key:
            triggers.add(key)
    return triggers


def has_manual_dispatch(content: str) -> bool:
    return "workflow_dispatch" in parse_triggers(content)


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
    if not has_manual_dispatch(content):
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
    if has_manual_dispatch(content):
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
