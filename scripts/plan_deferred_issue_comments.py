"""Offline-only candidate inventory for reconciling Issue #464 comments.

Input is a locally supplied, complete JSON list of GitHub issue-comment records.
This tool does not fetch, enqueue, attest cost, or claim that a task is absent.
Comments with unequal edit timestamps require evidence review; equal timestamps
do not prove the snapshot matches the original event body. Output never contains comment bodies or credentials.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if __package__:
    from scripts.github_comment_ingest import _comment_from_event_payload, plan
else:
    from github_comment_ingest import _comment_from_event_payload, plan

MAX_COMMENTS = 10_000
MAX_FILE_BYTES = 10 * 1024 * 1024


def inventory(records: object, *, issue_number: int = 464, today: date | None = None) -> list[dict]:
    if not isinstance(records, list) or len(records) > MAX_COMMENTS:
        raise ValueError("expected a bounded list of comments")
    if type(issue_number) is not int or issue_number != 464:
        raise ValueError("only coordination issue 464 is supported")
    seen: set[int] = set()
    results = []
    for record in records:
        comment, _ = _comment_from_event_payload({"comment": record, "issue": {"number": issue_number}})
        comment_id = comment["id"]
        if comment_id < 1 or comment_id in seen:
            raise ValueError("invalid or duplicate comment id")
        seen.add(comment_id)
        created = record.get("created_at")
        updated = record.get("updated_at")
        if not isinstance(created, str) or not isinstance(updated, str) or not created or not updated:
            raise ValueError("missing comment timestamps")
        decision = plan(comment, issue_number, "ghith93", 464, today=today)
        # An edited snapshot is not equivalent to the original event payload.
        # Never suggest replay of edited task envelopes without evidence review.
        if created != updated and decision["action"] in {"enqueue", "reject"}:
            state = "edited_requires_review"
        elif decision["action"] == "enqueue":
            state = "candidate_enqueue_status_unknown"
        elif decision["action"] == "reject":
            state = "invalid_or_expired"
        else:
            state = decision["action"]
        item = {"comment_id": comment_id, "state": state, "reason": decision["reason"]}
        if state == "candidate_enqueue_status_unknown":
            item["idempotency_key"] = decision["task"]["idempotency_key"]
        results.append(item)
    return sorted(results, key=lambda item: item["comment_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="local JSON array of issue 464 comments")
    parser.add_argument("--today", help="date for deterministic offline validation, YYYY-MM-DD")
    args = parser.parse_args(argv)
    try:
        # Bound the read itself, including files that grow while being read.
        with args.input.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("input too large")
        records = json.loads(raw)
        today = date.fromisoformat(args.today) if args.today else None
        result = inventory(records, today=today)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        print("Invalid or unavailable comment inventory; no action taken.", file=sys.stderr)
        return 2
    print(json.dumps({"mode": "offline_inventory_only", "enqueue_status": "unknown",
                      "results": result}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
