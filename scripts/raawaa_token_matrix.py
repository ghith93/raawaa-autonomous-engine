#!/usr/bin/env python3
"""Probe what a token can actually do, and print only capabilities.

Built because "I have many tokens and workers" turns into guesswork. This answers
the question directly: for each candidate credential, which of the operations the
project needs does it actually authorise?

It never prints a token. It prints a matrix.

Usage:
    python scripts/raawaa_token_matrix.py                     # probes CLOUDFLARE_API_TOKEN
    python scripts/raawaa_token_matrix.py --label workers_edit --token-var TOKEN_B
    python scripts/raawaa_token_matrix.py --all               # every TOKEN_* variable

Exit code 0 when at least one candidate can do everything the project needs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"

# The operations this project performs, in dependency order.
CAPABILITIES = [
    ("token_active", "GET", "/user/tokens/verify", "the value is a live token"),
    ("account", "GET", "/accounts/{account}", "can see the account"),
    ("d1", "GET", "/accounts/{account}/d1/database", "can reach D1"),
    ("ai_models", "GET", "/accounts/{account}/ai/models/search?per_page=1", "can list Workers AI models"),
]

# Workers AI inference is the one that costs neurons, so it is opt-in.
AI_RUN = ("ai_run", "POST", "/accounts/{account}/ai/run/@cf/black-forest-labs/flux-1-schnell",
          "can generate an image")


def _call(method: str, path: str, token: str, body: dict | None = None,
          timeout: int = 30) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
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


def _code(payload: dict) -> str:
    errors = payload.get("errors") or []
    if not errors:
        return ""
    return str(errors[0].get("code", ""))


def probe(token: str, account: str, *, spend_neurons: bool = False) -> dict:
    """Return a capability map for one token. The token value is never returned."""
    result = {
        "length": len(token),
        "capabilities": {},
        "missing": [],
        "usable": False,
    }
    if not token:
        result["missing"] = ["no value"]
        return result

    caps = list(CAPABILITIES)
    if spend_neurons:
        caps.append(AI_RUN)

    for name, method, path, _description in caps:
        body = None
        if name == "ai_run":
            body = {"prompt": "beige texture", "width": 256, "height": 256, "num_steps": 1}
        status, payload = _call(method, path.replace("{account}", account), token, body=body)
        ok = status != 0 and bool(payload.get("success", status == 200))
        if name == "token_active":
            ok = bool(payload.get("success")) and (payload.get("result") or {}).get("status") == "active"
        result["capabilities"][name] = "ok" if ok else f"fail:{_code(payload) or status}"
        if not ok:
            result["missing"].append(name)

    required = [name for name, *_ in CAPABILITIES]
    result["usable"] = all(result["capabilities"].get(r, "").startswith("ok") for r in required)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Token capability matrix")
    parser.add_argument("--account", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    parser.add_argument("--token-var", default="CLOUDFLARE_API_TOKEN")
    parser.add_argument("--label", default=None)
    parser.add_argument("--all", action="store_true",
                        help="probe every environment variable whose name looks like a token")
    parser.add_argument("--spend-neurons", action="store_true",
                        help="also test image generation (costs 14.4 neurons)")
    parser.add_argument("--json", help="append results to this file")
    args = parser.parse_args(argv)

    account = args.account.strip()
    if not account:
        print("CLOUDFLARE_ACCOUNT_ID is required", file=sys.stderr)
        return 2

    candidates: list[tuple[str, str]] = []
    if args.all:
        for name, value in sorted(os.environ.items()):
            upper = name.upper()
            if ("TOKEN" in upper or "KEY" in upper) and len(value.strip()) >= 20:
                candidates.append((name, value.strip()))
    else:
        candidates.append((args.label or args.token_var, os.environ.get(args.token_var, "").strip()))

    rows = []
    for name, value in candidates:
        probed = probe(value, account, spend_neurons=args.spend_neurons)
        rows.append({"name": name, **probed})

    print(f"{'credential':<32}{'len':>4}  capabilities")
    print("-" * 86)
    for row in rows:
        caps = " ".join(
            f"{cap}={'Y' if state.startswith('ok') else 'n'}"
            for cap, state in row["capabilities"].items()
        )
        verdict = "USABLE" if row["usable"] else row["missing"][0] if row["missing"] else "-"
        print(f"{row['name']:<32}{row['length']:>4}  {caps}  -> {verdict}")

    usable = [r for r in rows if r["usable"]]
    print()
    if usable:
        print("pick one of: " + ", ".join(r["name"] for r in usable))
    else:
        print("no credential covers every required operation")

    if args.json:
        with open(args.json, "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
