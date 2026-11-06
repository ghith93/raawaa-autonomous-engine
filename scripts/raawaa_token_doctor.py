#!/usr/bin/env python3
"""Diagnose a stored Cloudflare token without printing it.

A token that "works" in the dashboard can still fail everywhere else. The usual
causes are mechanical, not mysterious, and each has a distinct signature:

    pasted with a newline or quotes        -> length and character checks
    truncated during copy                  -> length far below 40
    expired or rolled                      -> 1000 Invalid API Token
    deleted but still in a secret store    -> 1000 Invalid API Token
    valid but missing one permission       -> 9109 / 10000-class errors per call
    valid, wrong account id                -> 7003 Could not route request

This runs the checks in that order and stops at the first real answer, because
telling someone "invalid" without saying which of five things to change is the
same as telling them nothing.

Usage:
    python scripts/raawaa_token_doctor.py                 # reads the environment
    python scripts/raawaa_token_doctor.py --file .env     # reads a KEY=VALUE file

Never prints a token, never writes one to disk.
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

API = "https://api.cloudflare.com/client/v4"

# Substrings that can survive a copy by accident.
SUSPICIOUS = {
    '"': "straight double quote",
    "'": "single quote",
    " ": "space",
    "\t": "tab",
    "\n": "newline",
    "…": "ellipsis (the copy was truncated with a display character)",
    "***": "asterisks (a masked value was copied instead of the real one)",
    "•": "bullet character",
}


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip()
    return values


def shape_report(token: str) -> list[str]:
    """Mechanical problems visible without contacting Cloudflare."""
    problems: list[str] = []
    if not token:
        return ["token is empty"]
    for char, description in SUSPICIOUS.items():
        if char in token:
            problems.append(f"contains a {description}")
    if token.lower().startswith("bearer "):
        problems.append("contains the word 'Bearer'; only the token itself is needed")
    if len(token) < 40:
        problems.append(
            f"length is {len(token)}; a Cloudflare API token is 40 characters, so this is truncated")
    elif len(token) > 45:
        # Not automatically wrong, but it is the shape of a different vendor's key.
        problems.append(
            f"length is {len(token)}; Cloudflare API tokens are 40 characters, so this may be "
            "a key from another service or a value with something appended")
    return problems


def _get(path: str, token: str, timeout: int = 15) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET",
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


def interpret(status: int, payload: dict) -> str:
    """Map Cloudflare's error to the single action that fixes it."""
    errors = payload.get("errors") or []
    codes = {e.get("code") for e in errors}
    messages = " ".join(str(e.get("message", "")) for e in errors).lower()

    if status == 0:
        return ("network", "this machine cannot reach api.cloudflare.com; the token may be fine")
    if 1000 in codes or "invalid api token" in messages:
        return ("invalid",
                "Cloudflare does not recognise this value. A token's secret is shown once and "
                "cannot be recovered, so it must be replaced: create a token at "
                "https://dash.cloudflare.com/profile/api-tokens and update the secret")
    if 7003 in codes or "could not route" in messages:
        return ("account", "the token is valid but the Account ID belongs to a different account")
    if 9109 in codes or "unauthorized to access requested resource" in messages:
        return ("permission", "the token is valid but lacks this resource's permission; "
                              "edit the token and add it")
    if 10000 in codes or "authentication error" in messages:
        return ("invalid", "authentication failed; treat the token as invalid and reissue")
    return ("unknown", f"HTTP {status}: {messages[:120] or 'no message'}")


def diagnose(env: dict[str, str], skip_ai: bool = False) -> dict:
    token = env.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()

    report: dict = {
        "token_present": bool(token),
        "token_length": len(token),
        "account_present": bool(account),
        "shape_problems": shape_report(token),
        "verified": False,
        "verdict": None,
        "action": None,
        "account_visible": None,
        "d1_ok": None,
        "ai_ok": None,
    }

    if not token:
        report["verdict"] = "missing"
        report["action"] = "no CLOUDFLARE_API_TOKEN in this environment"
        return report

    status, payload = _get("/user/tokens/verify", token)
    verdict, action = interpret(status, payload)
    report["verdict"] = verdict
    report["action"] = action
    if verdict != "ok":
        # tokens/verify returns success with status=active when it is good
        if payload.get("success") and (payload.get("result") or {}).get("status") == "active":
            report["verified"] = True
        else:
            return report

    report["verified"] = True

    if account:
        s2, p2 = _get(f"/accounts/{account}", token)
        v2, a2 = interpret(s2, p2)
        report["account_visible"] = v2 == "ok" and bool(p2.get("success"))
        if not report["account_visible"] and a2:
            report["action"] = a2

        s3, p3 = _get(f"/accounts/{account}/d1/database", token)
        report["d1_ok"] = bool(p3.get("success")) and s3 != 0

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose a Cloudflare token safely")
    parser.add_argument("--file", help="read KEY=VALUE pairs from this file")
    parser.add_argument("--skip-ai", action="store_true", help="accepted for symmetry; no AI call is made")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.file:
        env.update(load_env_file(Path(args.file)))

    report = diagnose(env, skip_ai=args.skip_ai)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report["token_present"]:
        print("\nno token found in the environment or the given file")
        return 2
    if report["shape_problems"] and not report["verified"]:
        print("\nmechanical problems:")
        for problem in report["shape_problems"]:
            print(f"  - {problem}")
    print(f"\nverdict: {report['verdict']}")
    if report["action"]:
        print(f"action:  {report['action']}")
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
