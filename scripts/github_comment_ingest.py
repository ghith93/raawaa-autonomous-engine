#!/usr/bin/env python3
"""Turn an issue comment into an engine task — or refuse to.

This is the receiving edge that `ARENA-COLLAB-003` identified as the only
missing piece: GitHub Actions supplies its own token, so an ``issue_comment``
event can start a headless run on a public repository at zero cost, without
touching the GitHub App or its event subscriptions.

The decision logic lives here rather than in the workflow so that it can be
tested without running Actions, and so the YAML stays thin enough to audit.

Four outcomes, and only one of them touches the network:

* ``skip``   — bot author, unlisted actor, or an issue we do not track
* ``ignore`` — a human comment with no task envelope (normal conversation)
* ``reject`` — an envelope that fails validation (reported, never enqueued)
* ``enqueue`` — a valid envelope becomes one idempotent engine task

Safety properties, each covered by a test:

1. The workflow granted itself ``contents: read`` only, so this path cannot
   comment back and therefore cannot loop.
2. A comment that validates is still untrusted input: validation proves form,
   never intent. The task is queued for review, not executed here.
3. Idempotency is keyed on the comment id, so a redelivery or a re-run of the
   same event cannot enqueue twice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:  # imported as a package (tests)
    from scripts.task_envelope import (
        EnvelopeError,
        envelope_hash,
        parse_envelope,
        validate,
    )
except ImportError:  # executed as a script (workflow)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from task_envelope import (  # type: ignore[no-redef]
        EnvelopeError,
        envelope_hash,
        parse_envelope,
        validate,
    )

TASK_TYPE = "issue_comment_task"
IDEMPOTENCY_PREFIX = "github-comment-"


def plan(
    comment: dict,
    issue_number: int,
    allowed_actor: str,
    allowed_issue: int,
    today: date | None = None,
) -> dict:
    """Decide what an incoming comment should do. Pure: no network, no clock beyond the date."""
    author = (comment.get("author") or "").strip()
    author_type = (comment.get("author_type") or "User").strip()
    comment_id = comment.get("id")
    body = comment.get("body") or ""
    today = today or datetime.now(timezone.utc).date()

    def decision(action: str, reason: str, **extra) -> dict:
        payload = {"action": action, "reason": reason, "comment_id": comment_id}
        payload.update(extra)
        return payload

    if not isinstance(comment_id, int):
        return decision("skip", "comment_id_missing")
    if author_type.lower() == "bot":
        return decision("skip", "bot_author")
    if author != allowed_actor:
        return decision("skip", "actor_not_allowed")
    if issue_number != allowed_issue:
        return decision("skip", "issue_not_tracked")

    try:
        envelope = parse_envelope(body)
    except EnvelopeError:
        return decision("ignore", "no_envelope")

    errors, warnings = validate(envelope, today=today)
    if errors:
        return decision("reject", "invalid_envelope", errors=errors, warnings=warnings)

    task = {
        "task_type": TASK_TYPE,
        "title": f"{envelope['id']}: issue comment {comment_id}",
        "payload": {
            "source": "github_issue_comment",
            "issue_number": issue_number,
            "comment_id": comment_id,
            "comment_author": author,
            "envelope": envelope,
            "envelope_fingerprint": envelope_hash(envelope),
            "warnings": warnings,
        },
        "idempotency_key": f"{IDEMPOTENCY_PREFIX}{comment_id}",
        "max_attempts": 2,
    }
    return decision("enqueue", "valid_envelope", task=task, warnings=warnings)


def _post(task: dict, base_url: str, token: str, timeout: int = 15) -> dict:
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)",
    }
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret

    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/tasks/enqueue",
        data=json.dumps(task).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return {"status": response.status, "body": json.loads(response.read().decode("utf-8"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--post",
        action="store_true",
        help="enqueue the task; without this flag the plan is printed only",
    )
    parser.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    comment = {
        "id": int(os.environ["COMMENT_ID"]) if os.environ.get("COMMENT_ID", "").isdigit() else None,
        "author": os.environ.get("COMMENT_AUTHOR", ""),
        "author_type": os.environ.get("COMMENT_AUTHOR_TYPE", "User"),
        "body": os.environ.get("COMMENT_BODY", ""),
    }
    issue_number = int(os.environ.get("ISSUE_NUMBER", "0") or 0)
    allowed_actor = os.environ.get("ALLOWED_ACTOR", "ghith93")
    allowed_issue = int(os.environ.get("ALLOWED_ISSUE", "464"))

    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else None
    result = plan(comment, issue_number, allowed_actor, allowed_issue, today=today)

    printable = {key: value for key, value in result.items() if key != "task"}
    if "task" in result:
        printable["task"] = result["task"]
    print(json.dumps(printable, indent=2, ensure_ascii=False))

    if result["action"] == "reject":
        for error in result.get("errors", []):
            print(f"ENVELOPE ERROR: {error}", file=sys.stderr)
        return 2

    if result["action"] != "enqueue":
        return 0

    if not args.post:
        print("dry run: pass --post to enqueue", file=sys.stderr)
        return 0

    base_url = os.environ.get("ENGINE_API_URL", "").strip()
    token = os.environ.get("ENGINE_API_TOKEN", "").strip()
    if not base_url or not token:
        # Missing configuration must not paint every comment red.
        print("SKIP: ENGINE_API_URL/ENGINE_API_TOKEN not configured for this repository", file=sys.stderr)
        return 0

    try:
        outcome = _post(result["task"], base_url, token)
    except Exception as error:  # noqa: BLE001 - surfaced verbatim in the run log
        print(f"ENQUEUE FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    created = outcome["body"].get("created")
    print(f"enqueued: HTTP {outcome['status']} created={created}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
