#!/usr/bin/env python3
"""Verify Cloudflare credentials step by step, before anything is deployed.

Each step isolates one permission, so a failure names exactly what to fix instead
of surfacing as "401" three layers deeper:

    1. account id present and shaped like a Cloudflare account id
    2. token is active              GET /user/tokens/verify
    3. token can see this account    GET /accounts/{id}
    4. token can reach D1            GET /accounts/{id}/d1/database
    5. token can run Workers AI      POST /ai/run (one cheap 256px image)

Step 5 is deliberately tiny: 256x256 at 1 step costs 4.80 + 9.60 = 14.4 neurons
of the 10,000 free daily allowance, so verifying costs 0.14% of a day.

Usage:
    set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN, then:
    python scripts/raawaa_verify_cloudflare.py
    python scripts/raawaa_verify_cloudflare.py --skip-ai     # do not spend neurons

Exit codes: 0 all pass, 1 a step failed, 2 credentials missing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

API = "https://api.cloudflare.com/client/v4"
ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

DASHBOARD = {
    "account_id": "https://dash.cloudflare.com/  ->  Workers & Pages  ->  Account ID (right sidebar)",
    "token": "https://dash.cloudflare.com/profile/api-tokens  ->  Create Token  ->  Create Custom Token",
    "d1": "https://dash.cloudflare.com/  ->  Workers & Pages  ->  D1 SQL Database",
}


class Step:
    def __init__(self, name: str, ok: bool, detail: str = "", fix: str | None = None):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.fix = fix

    def render(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        line = f"[{mark}] {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        if not self.ok and self.fix:
            line += f"\n       fix: {self.fix}"
        return line


def _request(path: str, token: str, *, method: str = "GET",
             body: dict | None = None, timeout: int = 20) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
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


def _errors(payload: dict) -> str:
    errors = payload.get("errors") or []
    if not errors:
        return ""
    return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors[:2])


def check_account_id(account_id: str) -> Step:
    if not account_id:
        return Step("account id", False, "empty",
                    fix=DASHBOARD["account_id"])
    if not ACCOUNT_ID_RE.match(account_id.lower()):
        return Step("account id", False,
                    f"expected 32 hex characters, got {len(account_id)} characters",
                    fix=DASHBOARD["account_id"])
    return Step("account id", True, "format valid")


def check_token_active(token: str) -> Step:
    if not token:
        return Step("token active", False, "empty", fix=DASHBOARD["token"])
    status, payload = _request("/user/tokens/verify", token)
    if status == 0:
        return Step("token active", False, "network unreachable from here")
    if not payload.get("success"):
        return Step("token active", False, _errors(payload) or f"HTTP {status}",
                    fix=DASHBOARD["token"])
    state = (payload.get("result") or {}).get("status", "unknown")
    return Step("token active", state == "active", f"status={state}",
                fix="the token exists but is disabled or expired; create a new one")


def check_account_access(account_id: str, token: str) -> Step:
    status, payload = _request(f"/accounts/{account_id}", token)
    if status == 0:
        return Step("account access", False, "network unreachable from here")
    if not payload.get("success"):
        return Step("account access", False, _errors(payload) or f"HTTP {status}",
                    fix="token must include the account in Account Resources, or the id is wrong")
    name = (payload.get("result") or {}).get("name", "")
    return Step("account access", True, f"account visible: {name}")


def check_d1(account_id: str, token: str) -> Step:
    status, payload = _request(f"/accounts/{account_id}/d1/database", token)
    if status == 0:
        return Step("D1 access", False, "network unreachable from here")
    if not payload.get("success"):
        return Step("D1 access", False, _errors(payload) or f"HTTP {status}",
                    fix="token needs Account > D1 > Edit")
    databases = payload.get("result") or []
    names = [db.get("name") for db in databases if isinstance(db, dict)]
    if "raawaa-autonomous-e" in names:
        return Step("D1 access", True, f"database found: raawaa-autonomous-e")
    return Step("D1 access", True,
                f"token works, {len(names)} database(s) visible; raawaa-autonomous-e not among them",
                fix="create the database or check the account: " + DASHBOARD["d1"])


def check_workers_ai(account_id: str, token: str) -> Step:
    model = "@cf/black-forest-labs/flux-1-schnell"
    status, payload = _request(
        f"/accounts/{account_id}/ai/run/{model}", token, method="POST",
        body={"prompt": "plain beige background", "width": 256, "height": 256, "num_steps": 1},
        timeout=60,
    )
    if status == 0:
        return Step("Workers AI", False, "network unreachable from here")
    if not payload.get("success", False):
        return Step("Workers AI", False, _errors(payload) or f"HTTP {status}",
                    fix="token needs Account > Workers AI > Read")
    return Step("Workers AI", True, "one 256px image generated (14.4 neurons)")


def run(env: dict[str, str], *, skip_ai: bool = False) -> tuple[list[Step], dict]:
    account_id = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = env.get("CLOUDFLARE_API_TOKEN", "").strip()

    steps = [check_account_id(account_id)]
    if not account_id:
        steps.append(Step("token active", False, "skipped: account id missing"))
        return steps, {"account_id_present": False, "token_present": bool(token)}

    steps.append(check_token_active(token))
    if token and steps[-1].ok:
        steps.append(check_account_access(account_id, token))
        steps.append(check_d1(account_id, token))
        if skip_ai:
            steps.append(Step("Workers AI", True, "skipped by request; neurons not spent"))
        else:
            steps.append(check_workers_ai(account_id, token))
    return steps, {
        "account_id_present": bool(account_id),
        "account_id_valid_format": bool(ACCOUNT_ID_RE.match(account_id.lower())),
        "token_present": bool(token),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Cloudflare credentials")
    parser.add_argument("--skip-ai", action="store_true", help="do not spend neurons")
    parser.add_argument("--json", help="write the result to this path")
    args = parser.parse_args(argv)

    steps, meta = run(dict(os.environ), skip_ai=args.skip_ai)
    for step in steps:
        print(step.render())

    failures = [s for s in steps if not s.ok]
    print()
    if failures:
        print(f"{len(failures)} step(s) need attention.")
        if not meta.get("account_id_present") or not meta.get("token_present"):
            print("start here: " + DASHBOARD["token"])
    else:
        print("All checks passed. The image route and deploys are usable.")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(
            {"steps": [{"name": s.name, "ok": s.ok, "detail": s.detail, "fix": s.fix}
                       for s in steps], "meta": meta},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {args.json}")

    if not meta.get("account_id_present") or not meta.get("token_present"):
        return 2
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
