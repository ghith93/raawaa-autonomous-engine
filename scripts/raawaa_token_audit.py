#!/usr/bin/env python3
"""Sustainable token management: audit, classify, and plan the cleanup.

The problem this solves is not "which token works" but "ten tokens exist, nobody
knows which is which, and every new workflow adds another". Left alone that ends
in one of two ways: a token with too much power stays alive because nobody dares
delete it, or a working system breaks because someone deleted the wrong one.

The resolution is a single designated token per purpose, with an audit that can be
re-run. This script reads the account's token list (metadata only) and produces:

* an inventory with last-used dates, so dead tokens are identifiable
* a risk flag for any token that has been unused for a long time but still exists
* a classification by name, so the "Worker edit" token the founder trusts is not
  confused with the eleven others
* a delete plan that never touches the designated token

It deliberately does not delete anything. Deletion is irreversible and cannot be
undone by retrying, so it stays a human action with a printed list.

Usage:
    python scripts/raawaa_token_audit.py                 # inventory + plan
    python scripts/raawaa_token_audit.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.cloudflare.com/client/v4"


def _get(path: str, token: str, timeout: int = 20) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (json.JSONDecodeError, ValueError):
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}


def age_days(timestamp: str | None, now: datetime | None = None) -> int | None:
    if not timestamp:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (now - then).days)


def classify(name: str) -> str:
    lowered = (name or "").lower()
    if "audit" in lowered or "inventory" in lowered:
        return "read-only-inventory"
    if "workers ai" in lowered:
        return "workers-ai"
    if "edit cloudflare workers" in lowered or "worker" in lowered:
        return "workers-edit"
    if "page" in lowered:
        return "pages-deploy"
    if "deploy" in lowered:
        return "deploy"
    return "other"


def plan(tokens: list[dict], *, keep: str | None = None,
         stale_days: int = 90) -> dict:
    """Classify tokens and propose a keep/delete split."""
    rows = []
    for token in tokens:
        name = token.get("name", "")
        used = age_days(token.get("last_used_on") or token.get("modified_on"))
        rows.append({
            "id": token.get("id"),
            "name": name,
            "kind": classify(name),
            "status": token.get("status"),
            "days_since_use": used,
            "stale": used is not None and used > stale_days,
            "keep": (name == keep) if keep else None,
        })

    if keep:
        for row in rows:
            row["keep"] = row["name"] == keep
    # A credential that has never been used and is old is the safest deletion.
    candidates = [r for r in rows if not r["keep"] and r["stale"]]
    return {
        "total": len(rows),
        "rows": rows,
        "keep": keep,
        "delete_candidates": [r["name"] for r in candidates],
        "risk": [
            r["name"] for r in rows
            if r["stale"] and r["kind"] in {"workers-edit", "deploy"} and not r["keep"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloudflare token inventory and cleanup plan")
    parser.add_argument("--keep", help="the token name to designate as the project token")
    parser.add_argument("--stale-days", type=int, default=90)
    parser.add_argument("--json", help="write the report here")
    args = parser.parse_args(argv)

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        print("CLOUDFLARE_API_TOKEN is required", file=sys.stderr)
        return 2

    status, payload = _get("/user/tokens", token)
    if status == 0:
        print("cannot reach api.cloudflare.com from here", file=sys.stderr)
        return 1
    if not payload.get("success"):
        code = (payload.get("errors") or [{}])[0].get("code")
        print(f"listing tokens needs a user-scoped token (error {code}). "
              "A token limited to one account cannot list its siblings.", file=sys.stderr)
        return 1

    result = plan(payload.get("result") or [], keep=args.keep, stale_days=args.stale_days)

    print(f"{'name':<34}{'kind':<22}{'status':<10}{'idle days':>10}  action")
    print("-" * 92)
    for row in result["rows"]:
        idle = "-" if row["days_since_use"] is None else str(row["days_since_use"])
        if row["keep"]:
            action = "KEEP (project token)"
        elif row["stale"]:
            action = "delete candidate"
        else:
            action = "keep for now"
        print(f"{row['name'][:33]:<34}{row['kind']:<22}{str(row['status']):<10}{idle:>10}  {action}")

    print()
    print(f"total tokens: {result['total']}")
    if result["delete_candidates"]:
        print("delete candidates: " + ", ".join(result["delete_candidates"]))
    if result["risk"]:
        print("risk (powerful and idle): " + ", ".join(result["risk"]))
    print("\nnothing was deleted; deletion is irreversible and stays a human action")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
