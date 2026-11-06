#!/usr/bin/env python3
"""Write a verified status report of what is actually live, and what is missing.

This exists because "is it working?" has one honest answer: a probe. Anything
else is a claim. The report separates three states that are usually conflated:

    deployed    a URL exists and answers
    activated   a credential or binding is present
    verified    someone fetched it and got the expected shape back

A route can be deployed and unactivated (a Worker running without its D1 binding)
or activated and unverified (credentials present, never exercised). Collapsing
these into "done" is how a project believes it is live when it is not.

Usage:
    python scripts/raawaa_activate.py                 # report only, no network
    python scripts/raawaa_activate.py --probe         # also fetch the public URLs
    python scripts/raawaa_activate.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.image_providers import CloudflareFlux, LocalQwenImage  # noqa: E402

# Routes the project believes it has. Probed, never assumed.
PUBLIC_ROUTES = [
    ("control", "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev"),
    ("staging", "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"),
    ("storefront", "https://raawaa-store.pages.dev"),
]

CREDENTIALS = [
    ("CLOUDFLARE_ACCOUNT_ID", "deploy and the free image route"),
    ("CLOUDFLARE_API_TOKEN", "deploy and the free image route"),
    ("ENGINE_API_TOKEN", "worker API authentication"),
    ("CORS_ALLOWED_ORIGINS", "browser calls from the storefront"),
    ("IG_USER_ID", "Instagram publishing"),
    ("IG_ACCESS_TOKEN", "Instagram publishing"),
    ("GITHUB_TOKEN", "repository automation"),
]


def credential_report(env: dict[str, str]) -> list[dict]:
    rows = []
    for name, purpose in CREDENTIALS:
        present = bool(env.get(name, "").strip())
        rows.append({
            "name": name,
            "present": present,
            "purpose": purpose,
            # The value is never read into the report.
            "action": None if present else f"add {name} to the environment",
        })
    return rows


def probe(url: str, timeout: int = 12) -> dict:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "raawaa-activate/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2048)
            return {
                "reachable": True,
                "status": response.status,
                "bytes_sampled": len(body),
                "looks_like_html": body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")),
            }
    except urllib.error.HTTPError as exc:
        # An HTTP error still proves something is deployed and answering.
        return {"reachable": True, "status": exc.code, "error": "http_error"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"reachable": False, "error": type(exc).__name__}


def build_report(env: dict[str, str], do_probe: bool = False) -> dict:
    cf_ok, cf_reason = CloudflareFlux(env=env).available()
    q_ok, q_reason = LocalQwenImage(env=env).available()

    d1_meta = {}
    try:
        import tomllib
        cfg = tomllib.loads((ROOT / "wrangler.toml").read_text(encoding="utf-8"))
        bindings = cfg.get("d1_databases", [])
        d1_meta = {
            "declared_bindings": len(bindings),
            "database_name": bindings[0].get("database_name") if bindings else None,
            "database_id_bound": bool(bindings and bindings[0].get("database_id")),
            "account_pinned_in_repo": bool(cfg.get("account_id")),
        }
    except Exception as exc:  # noqa: BLE001 - report, never crash a status tool
        d1_meta = {"error": f"{type(exc).__name__}: {exc}"}

    routes = []
    for name, url in PUBLIC_ROUTES:
        entry = {"name": name, "url": url}
        if do_probe:
            entry.update(probe(url))
        else:
            entry["probed"] = False
        routes.append(entry)

    credentials = credential_report(env)
    missing = [c["name"] for c in credentials if not c["present"]]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "probed": do_probe,
        "routes": routes,
        "d1": d1_meta,
        "image_routes": {
            "cloudflare": {"available": cf_ok, "reason": cf_reason},
            "local_qwen": {"available": q_ok, "reason": q_reason},
            "offline": {"available": True, "reason": "always available"},
        },
        "credentials": credentials,
        "missing_credentials": missing,
        "verdict": _verdict(routes, missing, cf_ok, do_probe),
    }


def _verdict(routes: list[dict], missing: list[str], cf_ok: bool, probed: bool) -> str:
    if not probed:
        return "report only: routes not fetched, so 'deployed' is unproven"
    down = [r["name"] for r in routes if r.get("reachable") is False]
    if down:
        return f"not reachable: {', '.join(down)}"
    if missing:
        return f"live but not activated: missing {', '.join(missing)}"
    if not cf_ok:
        return "live, credentials present, but the free image route is not usable"
    return "live and activated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What is actually live")
    parser.add_argument("--probe", action="store_true", help="fetch the public routes")
    parser.add_argument("--json", help="write the report to this path")
    args = parser.parse_args(argv)

    report = build_report(dict(os.environ), do_probe=args.probe)

    print("ROUTES")
    for route in report["routes"]:
        state = "no probe" if not report["probed"] else (
            f"HTTP {route.get('status')}" if route.get("reachable") else
            f"UNREACHABLE ({route.get('error')})"
        )
        print(f"  {route['name']:<11} {state:<24} {route['url']}")

    print("\nD1")
    for key, value in report["d1"].items():
        print(f"  {key}: {value}")

    print("\nIMAGE ROUTES")
    for name, data in report["image_routes"].items():
        print(f"  {'OK  ' if data['available'] else 'GAP '} {name:<10} {data['reason']}")

    print("\nCREDENTIALS (names only, values never read)")
    for cred in report["credentials"]:
        print(f"  {'present' if cred['present'] else 'MISSING':<8} {cred['name']:<26} {cred['purpose']}")

    print(f"\nVERDICT: {report['verdict']}")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
