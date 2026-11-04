#!/usr/bin/env python3
"""Write a task envelope that the receiving edge will actually accept.

Hand-written envelopes fail for reasons that are invisible while you are writing
them: an acceptance criterion that is not falsifiable, evidence that names no
openable artefact, a stale date, an id that does not match the required shape.
The first live envelope written by hand for this project was rejected exactly
that way, one wasted workflow run later.

So the envelope is generated here and checked against the real validator before
anything is posted. Nothing is posted unless ``validate`` returns no errors.

    python scripts/owner_task.py \
        --to chatgpt --scope research-only \
        --acceptance "The runner reports 1 task processed and pending 0" \
        --evidence "pr-470" --post

The generator refuses to decide policy for you: scope, forbidden actions and the
cost cap are inputs, never defaults chosen on your behalf.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # imported as a package (tests)
    from scripts.task_envelope import CANONICAL_ORDER, envelope_hash, validate
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from task_envelope import CANONICAL_ORDER, envelope_hash, validate  # type: ignore[no-redef]

MARKER = "TASK v1"
DEFAULT_ISSUE = 464
DEFAULT_FORBIDDEN = "repo-write"
DEFAULT_EXPIRY_DAYS = 14
_ID_RE = re.compile(r"^(ARENA|CHATGPT)-[A-Z0-9]+(?:-[A-Z0-9]+)*-(\d{3})$")


def render(envelope: dict) -> str:
    """Render the envelope block in the order the parser expects."""
    lines = [MARKER]
    for field in CANONICAL_ORDER:
        if field in envelope:
            lines.append(f"{field}: {envelope[field]}")
    return "\n".join(lines) + "\n"


def build(
    *,
    acceptance: str,
    evidence: str,
    base: str,
    sender: str = "arena",
    receiver: str = "chatgpt",
    scope: str = "research-only",
    forbidden: str = DEFAULT_FORBIDDEN,
    cost_cap_usd: float = 0.0,
    expires: datetime.date | None = None,
    today: datetime.date | None = None,
    task_id: str | None = None,
    existing_ids: tuple[str, ...] = (),
) -> dict:
    """Assemble an envelope. Raises ValueError when an id cannot be chosen."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    expires = expires or (today + datetime.timedelta(days=DEFAULT_EXPIRY_DAYS))
    return {
        "id": task_id or next_id(sender=sender, existing_ids=existing_ids),
        "from": sender,
        "to": receiver,
        "scope": scope,
        "base": base,
        "forbidden": forbidden,
        "cost_cap_usd": _format_cap(cost_cap_usd),
        "expires": expires.isoformat(),
        "acceptance": " ".join(acceptance.split()),
        "evidence": evidence.strip(),
    }


def _format_cap(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def next_id(*, sender: str = "arena", existing_ids: tuple[str, ...] = (), prefix: str = "OP") -> str:
    """Return the next free ``<SENDER>-<PREFIX>-NNN`` id, never reusing a taken one."""
    high = 0
    pattern = re.compile(rf"^{re.escape(sender.upper())}-{prefix}-(\d{{3}})$")
    for candidate in existing_ids:
        match = pattern.match(candidate.strip().upper())
        if match:
            high = max(high, int(match.group(1)))
    return f"{sender.upper()}-{prefix}-{high + 1:03d}"


def check(envelope: dict, today: datetime.date | None = None) -> tuple[list[str], list[str], str]:
    """Validate against the real validator and return ``(errors, warnings, fingerprint)``."""
    errors, warnings = validate(envelope, today=today)
    return errors, warnings, envelope_hash(envelope)


def comment_body(envelope: dict, title: str | None = None) -> str:
    heading = title or f"Task {envelope['id']} → {envelope['to']}"
    return (
        f"## {heading}\n\n"
        f"Generated and validated locally before posting.\n\n"
        f"{render(envelope)}\n"
        f"<!-- envelope-fingerprint: {envelope_hash(envelope)} -->\n"
    )


def hints(errors: list[str]) -> list[str]:
    """Say what the two most common rejections actually want, instead of naming a regex."""
    joined = " ".join(errors)
    hints: list[str] = []
    if "not falsifiable" in joined:
        hints.append(
            "acceptance needs something checkable: wrap a command in backticks "
            "(`python -m unittest`), quote a count (658 tests), an exit code, an HTTP code, or #470"
        )
    if "openable artefact" in joined:
        hints.append(
            "evidence must be openable: pr-470, issue-comment-6622318134, a URL, "
            "or a repository path such as engine/worker.py; separate several with ';'"
        )
    return hints


def _git_base() -> str:
    for ref in ("origin/main", "main", "HEAD"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short=7", ref],
                cwd=str(ROOT), capture_output=True, text=True, check=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        sha = result.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{7,40}", sha):
            return sha
    raise SystemExit("could not determine a base commit; pass --base")


def _existing_ids(issue: int) -> tuple[str, ...]:
    """Best-effort scan of the handoff issue so ids are not reused."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{issue}/comments?per_page=100",
             "--paginate", "--jq", ".[].body"],
            cwd=str(ROOT), capture_output=True, text=True, check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    found = re.findall(r"^id:\s*((?:ARENA|CHATGPT)-[A-Z0-9-]+)$", result.stdout, re.MULTILINE)
    return tuple(found)


def post(body: str, issue: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "comment", str(issue), "--body-file", "-"],
        cwd=str(ROOT), input=body, capture_output=True, text=True, check=False, timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit(f"posting failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "posted"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate a task envelope.")
    parser.add_argument("--acceptance", required=True,
                        help="criterion that names a command, count, exit code or artefact")
    parser.add_argument("--evidence", required=True,
                        help="openable artefacts, comma or semicolon separated (pr-<n>, issue-comment-<id>, a path)")
    parser.add_argument("--id", dest="task_id", help="envelope id; generated when omitted")
    parser.add_argument("--from", dest="sender", default="arena", choices=["arena", "chatgpt", "owner"])
    parser.add_argument("--to", dest="receiver", default="chatgpt", choices=["arena", "chatgpt", "owner"])
    parser.add_argument("--scope", default="research-only",
                        choices=["research-only", "report-only", "repo-change"])
    parser.add_argument("--forbidden", default=DEFAULT_FORBIDDEN,
                        help="comma or plus separated prohibited actions")
    parser.add_argument("--cost-cap", type=float, default=0.0)
    parser.add_argument("--expires", help="YYYY-MM-DD; defaults to 14 days out")
    parser.add_argument("--base", help="7-40 char commit sha; defaults to origin/main")
    parser.add_argument("--issue", type=int, default=DEFAULT_ISSUE)
    parser.add_argument("--title", help="heading text for the posted comment")
    parser.add_argument("--post", action="store_true", help="post the comment; otherwise print only")
    args = parser.parse_args(argv)

    expires = None
    if args.expires:
        try:
            expires = datetime.date.fromisoformat(args.expires)
        except ValueError:
            raise SystemExit("--expires must be YYYY-MM-DD")

    envelope = build(
        acceptance=args.acceptance, evidence=args.evidence,
        base=args.base or _git_base(),
        sender=args.sender, receiver=args.receiver, scope=args.scope,
        forbidden=args.forbidden, cost_cap_usd=args.cost_cap, expires=expires,
        task_id=args.task_id, existing_ids=() if args.task_id else _existing_ids(args.issue),
    )
    errors, warnings, fingerprint = check(envelope)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        print("rejected by the validator — nothing was posted:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        for hint in hints(errors):
            print(f"hint: {hint}", file=sys.stderr)
        return 2

    body = comment_body(envelope, args.title)
    print(render(envelope), end="")
    print(f"fingerprint: {fingerprint}", file=sys.stderr)
    if args.post:
        print(post(body, args.issue))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
