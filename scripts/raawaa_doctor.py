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
