#!/usr/bin/env python3
"""Task envelope parser, validator and ledger checker for the ARENA <-> ChatGPT channel.

Design constraints that shaped this file:

* **No committed state.** The single source of truth for task state is the GitHub
  issue thread (issue #464), not a JSON file in this repository. A committed
  ledger would duplicate that thread and drift from it. ``ledger`` therefore
  reads a snapshot (produced by ``gh api .../comments``) and validates it in
  memory.
* **No credentials, no network.** Every subcommand works on text handed to it.
  Nothing here can spend money, call a provider, or touch a workflow.
* **Enforcement, not paperwork.** The reason comment-thread protocols decay is
  that no machine ever checks them. ``validate`` is that machine: required
  fields, a falsifiable acceptance criterion, a real forbidden-action list, a
  live expiry, and state transitions that must be legal.

The hash is a *consistency fingerprint* (SHA-256 over a canonical rendering of
the envelope), not an authentication primitive. It detects that the text a
receiver claimed differs from the text the sender posted. It does not prove who
sent it. Actor provenance stays UNKNOWN and is never inferred here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone

ENVELOPE_MARKER = "TASK v1"

REQUIRED_FIELDS = (
    "id",
    "from",
    "to",
    "scope",
    "base",
    "forbidden",
    "cost_cap_usd",
    "expires",
    "acceptance",
    "evidence",
)

# Field order for canonical rendering. Fixed order is what makes the hash
# stable regardless of how the sender ordered the block.
CANONICAL_ORDER = REQUIRED_FIELDS

ACTORS = frozenset({"chatgpt", "arena", "owner"})
SCOPES = frozenset({"research-only", "report-only", "repo-change"})

# Vocabulary for the negative list. Unknown tokens are reported as warnings
# rather than errors: a sender may legitimately invent a prohibition ("dns-change")
# and blocking on that would punish caution.
FORBIDDEN_VOCABULARY = frozenset(
    {
        "repo-write",
        "pr-create",
        "pr-merge",
        "webhook-registration",
        "permission-change",
        "secret-change",
        "workflow-dispatch",
        "deployment",
        "d1-write",
        "paid-inference",
        "provider-call",
        "external-message",
        "account-creation",
        "spend",
        "secrets",
    }
)

TASK_ID_RE = re.compile(r"^(ARENA|CHATGPT)-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}$")
HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
STATE_MARKER_RE = re.compile(r"^\[TASK\s+([A-Z0-9-]+)\]\s+([A-Z_]+)(.*)$")

# A criterion is falsifiable when it names something a machine can check:
# a command, an artefact path, an exit code, a numeric threshold, an HTTP status.
MEASURABLE_RE = re.compile(
    r"""
      `[^`]+`                                   # inline code / command
    | \bexit(?:\s+(?:code|status))?\s*(?:of|=|:)?\s*0\b
    | \b0\s*$                                   # "returns 0"
    | \.(?:py|md|json|ya?ml|ts|js|toml)\b       # file artefact
    | \b\d+\s+(?:tests?|lines?|chars?|characters?|files?|requests?|sections?|items?)\b
    | \btests?\b
    | \bHTTP\s*\d{3}\b
    | \#\d+\b                                    # target issue/PR a third party can open
    """,
    re.VERBOSE,
)

VAGUE_ONLY_RE = re.compile(
    r"^\s*(?:please\s+)?(?:review|improve|analyse|analyze|improve|explore|investigate|consider|look into|assess|evaluate|check)\b",
    re.IGNORECASE,
)

# Evidence must be an artefact a third party can open.
EVIDENCE_RE = re.compile(
    r"^(?:issue-comment-\d+|comment-in-\d+|pr-\d+|https://\S+|[\w./-]+\.(?:py|md|json|ya?ml|ts|js|toml))$"
)

STATES = ("POSTED", "CLAIMED", "IN_PROGRESS", "DELIVERED", "VERIFIED", "REJECTED", "EXPIRED")

# Legal transitions. Terminal states map to an empty set.
TRANSITIONS = {
    "POSTED": {"CLAIMED", "REJECTED", "EXPIRED"},
    "CLAIMED": {"IN_PROGRESS", "DELIVERED", "REJECTED", "EXPIRED"},
    "IN_PROGRESS": {"DELIVERED", "REJECTED", "EXPIRED"},
    "DELIVERED": {"VERIFIED", "REJECTED"},
    "VERIFIED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
}

DELIVERY_PROOF_RE = re.compile(r"`[^`]+`")
DELIVERY_RESULT_RE = re.compile(r"\b(?:exit(?:ed)?\s*(?:code\s*)?0|pass(?:ed)?|ok|0\s+failed|\d+/\d+)\b", re.IGNORECASE)


class EnvelopeError(Exception):
    """Raised when text does not contain a parseable task envelope."""


def _normalise(value: str) -> str:
    return " ".join(value.split())


def strip_example_fences(text: str) -> str:
    """Remove fenced code blocks that are *examples*, not live contracts.

    Documentation that explains this protocol necessarily contains envelope and
    state-marker samples. Validating the live thread without this filter treats
    those samples as real tasks — a defect this validator hit on its first run
    against issue #464. The rule is deterministic:

    * unfenced text is live;
    * a fence tagged ``task`` is live;
    * any other fence is an example and is dropped.

    A sender therefore opts a block into the ledger by leaving it unfenced or by
    tagging the fence ``task``, and opts it out with any other tag.
    """
    output: list[str] = []
    fence: str | None = None
    live = False

    for line in text.splitlines():
        stripped = line.strip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                live = stripped[3:].strip().lower() == "task"
                output.append(line if live else "")
                continue
            output.append(line)
            continue

        if stripped.startswith(fence) and stripped[len(fence):].strip() == "":
            output.append(line if live else "")
            fence = None
            live = False
            continue

        output.append(line if live else "")

    return "\n".join(output)


def parse_envelope(text: str) -> dict:
    """Extract the first ``TASK v1`` envelope from ``text``.

    The block starts at a line equal to ``TASK v1`` and continues while lines
    look like ``key: value``. Everything after the first non-matching line is
    ignored, so an envelope may be pasted above free prose (which is exactly how
    it arrives from the channel).
    """
    text = strip_example_fences(text)
    if not text or ENVELOPE_MARKER not in text:
        raise EnvelopeError(f"no '{ENVELOPE_MARKER}' marker found")

    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == ENVELOPE_MARKER), None)
    if start is None:
        raise EnvelopeError(f"'{ENVELOPE_MARKER}' must appear on its own line")

    envelope: dict = {}
    for raw in lines[start + 1 :]:
        stripped = raw.strip()
        if not stripped:
            break
        if ":" not in stripped:
            break
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        if key not in REQUIRED_FIELDS:
            break
        envelope[key] = value.strip()

    if not envelope:
        raise EnvelopeError("envelope block is empty")
    return envelope


def canonical_text(envelope: dict) -> str:
    """Deterministic rendering used for the fingerprint.

    Only known fields participate, in fixed order, whitespace-collapsed and
    lowercased for the keys. Two senders rendering the same content therefore
    produce the same digest no matter how they formatted the block.
    """
    parts = [ENVELOPE_MARKER]
    for field in CANONICAL_ORDER:
        if field in envelope:
            parts.append(f"{field}: {_normalise(str(envelope[field]))}")
    return "\n".join(parts)


def envelope_hash(envelope: dict) -> str:
    return hashlib.sha256(canonical_text(envelope).encode("utf-8")).hexdigest()


def short_hash(envelope: dict, length: int = 8) -> str:
    return envelope_hash(envelope)[:length]


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def validate(envelope: dict, today: date | None = None) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for one envelope. Empty errors means valid."""
    errors: list[str] = []
    warnings: list[str] = []
    today = today or datetime.now(timezone.utc).date()

    missing = [field for field in REQUIRED_FIELDS if not envelope.get(field)]
    for field in missing:
        errors.append(f"missing required field: {field}")
    if missing:
        # Structural failures make the semantic checks meaningless.
        return errors, warnings

    task_id = envelope["id"].strip()
    if not TASK_ID_RE.match(task_id):
        errors.append(
            f"id '{task_id}' does not match <ARENA|CHATGPT>-<DOMAIN>-<NNN>"
        )

    sender = envelope["from"].strip().lower()
    receiver = envelope["to"].strip().lower()
    if sender not in ACTORS:
        errors.append(f"from '{sender}' is not one of {sorted(ACTORS)}")
    if receiver not in ACTORS:
        errors.append(f"to '{receiver}' is not one of {sorted(ACTORS)}")
    if sender == receiver:
        errors.append("from and to must differ")

    scope = envelope["scope"].strip().lower()
    if scope not in SCOPES:
        errors.append(f"scope '{scope}' is not one of {sorted(SCOPES)}")

    base = envelope["base"].strip()
    if not HEX_RE.match(base):
        errors.append(f"base '{base}' must be a 7-40 char lowercase hex commit SHA")

    forbidden = [token.strip() for token in re.split(r"[,+]", envelope["forbidden"]) if token.strip()]
    if not forbidden:
        errors.append("forbidden must list at least one prohibited action")
    elif len(forbidden) == 1 and forbidden[0].lower() in {"none", "n/a", "-"}:
        # Deliberately rejected: an empty negative list is how scope creep starts.
        errors.append(
            "forbidden may not be 'none'; name at least one action that is not allowed"
        )
    else:
        unknown = [token for token in forbidden if token.lower() not in FORBIDDEN_VOCABULARY]
        if unknown:
            warnings.append(f"forbidden token(s) outside known vocabulary: {', '.join(unknown)}")

    cap_raw = envelope["cost_cap_usd"].strip()
    try:
        cap = float(cap_raw)
    except ValueError:
        errors.append(f"cost_cap_usd '{cap_raw}' is not a number")
    else:
        if cap < 0:
            errors.append("cost_cap_usd may not be negative")

    expires = _parse_date(envelope["expires"])
    if expires is None:
        errors.append(f"expires '{envelope['expires']}' is not YYYY-MM-DD")
    elif expires < today:
        errors.append(f"expires {expires.isoformat()} is in the past (task is stale)")

    acceptance = _normalise(envelope["acceptance"])
    if len(acceptance) < 20:
        errors.append("acceptance is too short to be a verifiable criterion")
    elif not MEASURABLE_RE.search(acceptance):
        errors.append(
            "acceptance is not falsifiable: name a command, artefact, exit code, "
            "count or threshold that can be checked"
        )
    elif VAGUE_ONLY_RE.match(acceptance) and len(acceptance) < 120:
        warnings.append(
            "acceptance opens with a vague verb; make sure the measurable part is the actual criterion"
        )

    # Evidence may list several artefacts separated by ';' or ','.
    artefacts = [item.strip() for item in re.split(r"[;,]", envelope["evidence"]) if item.strip()]
    if not artefacts:
        errors.append("evidence must name at least one artefact")
    for artefact in artefacts:
        if not EVIDENCE_RE.match(artefact):
            errors.append(
                f"evidence '{artefact}' is not an openable artefact "
                "(issue-comment-<id>, pr-<n>, a URL, or a repository file path)"
            )

    return errors, warnings


def parse_state_markers(body: str) -> list[dict]:
    """Extract ``[TASK <id>] <STATE> hash=<hex>`` markers from a comment body."""
    markers = []
    for line in strip_example_fences(body).splitlines():
        match = STATE_MARKER_RE.match(line.strip())
        if not match:
            continue
        task_id, state, trailer = match.groups()
        if state not in STATES:
            continue
        hash_match = re.search(r"hash=([0-9a-f]{6,64})", trailer)
        markers.append(
            {
                "id": task_id,
                "state": state,
                "hash": hash_match.group(1) if hash_match else None,
                "body": body,
            }
        )
    return markers


def build_ledger(comments: list[dict]) -> dict:
    """Fold a comment snapshot into ``{task_id: {...}}`` ledger entries.

    ``comments`` is the shape returned by the GitHub comments API:
    ``[{"id": int, "body": str, "created_at": str}, ...]``.
    """
    ordered = sorted(comments, key=lambda c: (c.get("created_at") or "", c.get("id") or 0))
    ledger: dict[str, dict] = {}

    for comment in ordered:
        body = comment.get("body") or ""
        created = comment.get("created_at") or ""

        try:
            envelope = parse_envelope(body)
        except EnvelopeError:
            envelope = None

        if envelope and envelope.get("id"):
            task_id = envelope["id"].strip()
            entry = ledger.setdefault(
                task_id,
                {"id": task_id, "envelopes": [], "envelope": envelope, "states": []},
            )
            entry["envelopes"].append(
                {"comment_id": comment.get("id"), "created_at": created, "hash": envelope_hash(envelope)}
            )
            entry.setdefault("first_seen", created)

        for marker in parse_state_markers(body):
            task_id = marker["id"]
            entry = ledger.setdefault(
                task_id,
                {"id": task_id, "envelopes": [], "envelope": None, "states": []},
            )
            entry["states"].append(
                {
                    "state": marker["state"],
                    "hash": marker["hash"],
                    "comment_id": comment.get("id"),
                    "created_at": created,
                    "body": marker["body"],
                }
            )

    for entry in ledger.values():
        entry["current"] = entry["states"][-1]["state"] if entry["states"] else "POSTED"
    return ledger


def validate_ledger(ledger: dict, today: date | None = None) -> tuple[list[str], list[str]]:
    """Check structural and procedural invariants across the whole thread."""
    errors: list[str] = []
    warnings: list[str] = []
    today = today or datetime.now(timezone.utc).date()

    for task_id, entry in ledger.items():
        envelopes = entry["envelopes"]

        if not envelopes:
            errors.append(f"{task_id}: state marker present but no task envelope in the thread")
            continue

        if len(envelopes) > 1:
            hashes = {e["hash"] for e in envelopes}
            if len(hashes) > 1:
                errors.append(
                    f"{task_id}: posted more than once with different content "
                    f"({len(hashes)} distinct envelopes); amend by issuing a new task id"
                )
            else:
                warnings.append(f"{task_id}: envelope reposted identically {len(envelopes)} times")

        envelope = entry["envelope"]
        task_errors, task_warnings = validate(envelope, today=today)
        errors.extend(f"{task_id}: {message}" for message in task_errors)
        warnings.extend(f"{task_id}: {message}" for message in task_warnings)

        expected_hash = envelope_hash(envelope)
        previous = "POSTED"
        for marker in entry["states"]:
            state = marker["state"]
            if state not in TRANSITIONS.get(previous, set()):
                errors.append(f"{task_id}: illegal transition {previous} -> {state}")
            if state == "CLAIMED":
                if not marker["hash"]:
                    errors.append(f"{task_id}: CLAIMED without hash=<fingerprint>")
                elif not expected_hash.startswith(marker["hash"]):
                    errors.append(
                        f"{task_id}: CLAIMED hash {marker['hash']} does not match envelope "
                        f"fingerprint {expected_hash[:8]} (receiver parsed a different contract)"
                    )
            if state == "DELIVERED":
                if not DELIVERY_PROOF_RE.search(marker["body"]):
                    errors.append(f"{task_id}: DELIVERED without a backticked command")
                elif not DELIVERY_RESULT_RE.search(marker["body"]):
                    errors.append(f"{task_id}: DELIVERED without a result (exit code/pass count)")
            previous = state

        if previous in {"POSTED", "CLAIMED", "IN_PROGRESS"}:
            expires = _parse_date(envelope.get("expires", ""))
            if expires and expires < today:
                warnings.append(f"{task_id}: still {previous} past its expiry ({expires.isoformat()})")

    return errors, warnings


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="validate one task envelope")
    lint_parser.add_argument("path", help="file containing the envelope, or - for stdin")
    lint_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    lint_parser.add_argument(
        "--today", help="override today's date (YYYY-MM-DD) for deterministic checks"
    )

    hash_parser = subparsers.add_parser("hash", help="print the envelope fingerprint")
    hash_parser.add_argument("path", help="file containing the envelope, or - for stdin")

    ledger_parser = subparsers.add_parser(
        "ledger", help="validate a comments snapshot (gh api .../comments)"
    )
    ledger_parser.add_argument("path", help="JSON array of comments, or - for stdin")
    ledger_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ledger_parser.add_argument("--today", help="override today's date (YYYY-MM-DD)")

    args = parser.parse_args(argv)
    today = _parse_date(args.today) if getattr(args, "today", None) else None

    if args.command in {"lint", "hash"}:
        try:
            envelope = parse_envelope(_read(args.path))
        except EnvelopeError as exc:
            payload = {"ok": False, "errors": [str(exc)], "warnings": []}
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2))
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        if args.command == "hash":
            print(envelope_hash(envelope))
            return 0

        errors, warnings = validate(envelope, today=today)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": not errors,
                        "id": envelope.get("id"),
                        "hash": envelope_hash(envelope),
                        "errors": errors,
                        "warnings": warnings,
                    },
                    indent=2,
                )
            )
        else:
            for message in errors:
                print(f"ERROR: {message}", file=sys.stderr)
            for message in warnings:
                print(f"WARN:  {message}", file=sys.stderr)
            if not errors:
                print(f"OK {envelope['id']} hash={short_hash(envelope)}")
        return 1 if errors else 0

    # ledger
    try:
        comments = json.loads(_read(args.path))
    except json.JSONDecodeError as exc:
        print(f"ERROR: snapshot is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if isinstance(comments, dict):
        comments = [comments]

    ledger = build_ledger(comments)
    errors, warnings = validate_ledger(ledger, today=today)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "tasks": {
                        task_id: {
                            "current": entry["current"],
                            "hash": envelope_hash(entry["envelope"]) if entry["envelope"] else None,
                        }
                        for task_id, entry in sorted(ledger.items())
                    },
                    "errors": errors,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        for task_id, entry in sorted(ledger.items()):
            fingerprint = envelope_hash(entry["envelope"])[:8] if entry["envelope"] else "-"
            print(f"{task_id:<38} {entry['current']:<12} hash={fingerprint}")
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        for message in warnings:
            print(f"WARN:  {message}", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
