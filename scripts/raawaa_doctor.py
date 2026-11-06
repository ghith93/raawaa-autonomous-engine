#!/usr/bin/env python3
"""One command that answers: what do I have, what does it do, and what is next?

Replaces a pile of separate probes. Each section reports facts and stops; nothing
here changes a setting. Where several names mean the same thing, the role is
printed alongside the name that satisfied it, so the naming drift in the
repository becomes visible instead of confusing.

Usage:
    python scripts/raawaa_doctor.py
    python scripts/raawaa_doctor.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from engine.credentials import load_config, resolve, resolve_cloudflare  # noqa: E402


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raawaa environment doctor")
    parser.add_argument("--json", help="write the report here")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    config = load_config()
    report: dict = {"roles": {}, "cloudflare": None, "image_routes": {}, "next_steps": []}

    section("ROLES (any alias satisfies the role)")
    for role in config["roles"]:
        found = resolve(role, env, config)
        if found.present:
            print(f"  OK      {role:<20} via {found.name:<32} {found.fingerprint}")
        else:
            print(f"  MISSING {role:<20} tried: {', '.join(config['roles'][role])}")
        report["roles"][role] = {"name": found.name, "present": found.present,
                                 "fingerprint": found.fingerprint}

    section("CLOUDFLARE ACCOUNT RESOLUTION")
    cf = resolve_cloudflare(env, config) if report["roles"]["cloudflare_token"]["present"] else None
    if cf is None:
        print("  skipped: no Cloudflare token present")
    else:
        summary = cf.summary()
        print(f"  token      {summary['token']['name']} {summary['token']['fingerprint']}")
        print(f"  account    stored={summary['account_id']['stored']} "
              f"used={summary['account_id']['used']} "
              f"match={summary['account_id']['matches_stored']}")
        for account in summary["accounts_seen"]:
            print(f"  visible    {account['name']} ({account['id']})")
        print(f"  evidence   {summary['evidence']}")
        report["cloudflare"] = summary
        if summary["account_id"]["matches_stored"] is False:
            report["next_steps"].append(
                "update CLOUDFLARE_ACCOUNT_ID to the 'used' value above")

    section("D1 DATABASES ON THE RESOLVED ACCOUNT")
    d1 = resolve("d1_database_id", env, config)
    if cf and cf.chosen_account_id:
        import json as _json
        import urllib.request as _url
        request = _url.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{cf.chosen_account_id}/d1/database",
            headers={"Authorization": f"Bearer {cf.token.value}"})
        try:
            with _url.urlopen(request, timeout=20) as response:
                payload = _json.loads(response.read() or b"{}")
        except Exception as exc:  # noqa: BLE001
            payload = {}
            print(f"  could not list databases ({type(exc).__name__})")
        rows = payload.get("result") or []
        target = d1.value.strip()
        found = False
        for database in rows:
            uuid = str(database.get("uuid", ""))
            mark = ""
            if target and uuid == target:
                mark = "  <- matches D1_DATABASE_ID"
                found = True
            print(f"  {database.get('name'):<32} {uuid[:6]}...{uuid[-4:]}{mark}")
            report.setdefault("d1_databases", []).append(
                {"name": database.get("name"), "uuid_prefix": uuid[:6],
                 "uuid_suffix": uuid[-4:], "matches_secret": uuid == target})
        if target and not found:
            print(f"  the id in {d1.name} is not on this account")
            report["next_steps"].append(
                "the D1 database in the repo belongs to a different account; "
                "pick one of the databases above and update D1_DATABASE_ID, or move the worker")
        if not target:
            print("  no D1 database id secret found; choose one of the databases above")
    else:
        print("  skipped: no resolved account")

    section("IMAGE ROUTES")
    from engine.image_providers import CloudflareFlux, LocalQwenImage
    for provider in (CloudflareFlux(env=env), LocalQwenImage(env=env)):
        ok, reason = provider.available()
        print(f"  {'OK  ' if ok else 'GAP '} {provider.name:<12} {reason}")
        report["image_routes"][provider.name] = {"available": ok, "reason": reason}

    section("NEXT")
    if not report["next_steps"]:
        report["next_steps"].append("nothing blocking: run the image check workflow")
    for index, step in enumerate(report["next_steps"], 1):
        print(f"  {index}. {step}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
