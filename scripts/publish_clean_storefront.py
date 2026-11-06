#!/usr/bin/env python3
"""Publish `storefront/` to a clean brand-only Cloudflare Pages (`*.pages.dev`) URL.

Why Cloudflare Pages instead of `*.workers.dev`:
- `*.workers.dev` appends the account-wide subdomain (`<worker>.<owner-name>.workers.dev`),
  exposing the account holder's personal name in public links.
- Cloudflare Pages (`https://<brand>.pages.dev/`) uses a clean, standalone project
  subdomain with zero personal account name in the URL, and supports 1-click custom
  domains later without changing code.

Once the clean `*.pages.dev` URL responds HTTP 200, this script also retires the
legacy `raawaa-storefront` Worker (`workers.dev`) so the personal-name URL goes offline,
and records the verified public URL in a GitHub Deployment status (readable via `gh api`).

Secrets are never printed or written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CF_API_BASE = "https://api.cloudflare.com/client/v4"
PAGES_COMPAT_DATE = "2026-10-28"

# Priority order of clean, zero-personal-name brand slugs on `.pages.dev`.
PREFERRED_BRAND_SLUGS: tuple[str, ...] = (
    "raawaa",
    "rawaa",
    "raawaa-official",
    "rawaa-official",
    "raawaa-beauty",
    "raawaa-cosmetics",
)

LEGACY_WORKER_TO_RETIRE = "raawaa-storefront"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SECRET_LIKE_RE = re.compile(r"[A-Za-z0-9_-]{30,}")


def _sanitize_error_snippet(raw: str) -> str:
    """Extract a short, secret-free diagnostic line from Wrangler output."""
    cleaned = _ANSI_RE.sub("", raw or "")
    cleaned = _SECRET_LIKE_RE.sub("<redacted>", cleaned)
    interesting: list[str] = []
    for line in cleaned.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if any(k in low for k in ("error", "code:", "typeerror", "invalid", "failed", "authentication", "permission")):
            interesting.append(s)
    joined = " | ".join(interesting[:2]) if interesting else " ".join(cleaned.split())
    return joined[:110]


def resolve_cloudflare_credentials(env: dict[str, str] | None = None) -> tuple[str, str]:
    """Resolve Cloudflare token & account ID across common secret alias names."""
    src = env if env is not None else os.environ
    token = ""
    for key in (
        "CLOUDFLARE_PAGES_API_TOKEN",
        "CF_PAGES_API_TOKEN",
        "CLOUDFLARE_API_TOKEN",
        "CF_API_TOKEN",
    ):
        val = src.get(key, "").strip()
        if val:
            token = val
            break

    account_id = ""
    for key in ("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID"):
        val = src.get(key, "").strip()
        if val:
            account_id = val
            break

    return token, account_id


def _json_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: int = 25,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = {"Accept": "application/json", **headers}
    if data is not None:
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            return exc.code, json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return exc.code, {}
    except Exception:
        return 0, {}


def _cf_error_code(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") or []
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("code") or "")
    return ""


def _is_pages_subdomain_globally_free(slug: str, timeout: int = 5) -> bool:
    """Return True if `https://<slug>.pages.dev` is not already serving another account's site."""
    req = urllib.request.Request(
        f"https://{slug}.pages.dev/",
        headers={"User-Agent": "RaawaaSlugCheck/1.0"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status >= 500
    except urllib.error.HTTPError as exc:
        return exc.code in (404, 522, 523, 530)
    except Exception:
        return True


def _default_deployment_configs() -> dict[str, Any]:
    return {
        "production": {"compatibility_date": PAGES_COMPAT_DATE},
        "preview": {"compatibility_date": PAGES_COMPAT_DATE},
    }


def select_or_create_pages_project(
    account_id: str,
    api_token: str,
    candidates: tuple[str, ...] = PREFERRED_BRAND_SLUGS,
    request_fn=_json_request,
    slug_free_fn=_is_pages_subdomain_globally_free,
) -> dict[str, Any]:
    """Find an existing owned clean brand Pages project or create the highest-priority free one."""
    auth_headers = {"Authorization": f"Bearer {api_token}"}
    list_url = f"{CF_API_BASE}/accounts/{account_id}/pages/projects"
    status, payload = request_fn("GET", list_url, auth_headers, None)

    if status in (401, 403):
        code = _cf_error_code(payload) or str(status)
        raise RuntimeError(f"cf_pages_permission_denied:{code}")
    if status == 0 or not payload.get("success"):
        code = _cf_error_code(payload) or str(status)
        raise RuntimeError(f"cf_pages_list_failed:{code}")

    existing_by_name: dict[str, dict[str, Any]] = {}
    for proj in payload.get("result") or []:
        if isinstance(proj, dict) and proj.get("name"):
            existing_by_name[str(proj["name"]).lower()] = proj

    # 1) Reuse highest-priority clean brand project already owned by this account
    #    (preferring unsuffixed `<slug>.pages.dev` if multiple exist)
    owned_candidates = [slug for slug in candidates if slug in existing_by_name]
    exact_owned = [
        slug
        for slug in owned_candidates
        if str(existing_by_name[slug].get("subdomain") or "") == f"{slug}.pages.dev"
    ]
    chosen_owned = exact_owned[0] if exact_owned else (owned_candidates[0] if owned_candidates else None)

    if chosen_owned:
        proj = existing_by_name[chosen_owned]
        # Ensure `deployment_configs` has `compatibility_date` initialized for Wrangler 4
        request_fn(
            "PATCH",
            f"{list_url}/{chosen_owned}",
            auth_headers,
            {
                "production_branch": "main",
                "deployment_configs": _default_deployment_configs(),
            },
        )
        subdomain = str(proj.get("subdomain") or f"{chosen_owned}.pages.dev").strip()
        domains = [
            str(d).strip()
            for d in (proj.get("domains") or [])
            if d and not str(d).strip().endswith(".workers.dev")
        ]
        return {
            "project_name": chosen_owned,
            "subdomain": subdomain,
            "domains": domains,
            "created": False,
        }

    # 2) Filter candidates to globally unclaimed exact `<slug>.pages.dev` first, then rest
    free_first = [s for s in candidates if slug_free_fn(s)]
    ordered = free_first + [s for s in candidates if s not in free_first]

    last_code = "none"
    for slug in ordered:
        c_status, c_payload = request_fn(
            "POST",
            list_url,
            auth_headers,
            {
                "name": slug,
                "production_branch": "main",
                "deployment_configs": _default_deployment_configs(),
            },
        )
        if c_status in (200, 201) and c_payload.get("success"):
            proj = c_payload.get("result") or {}
            subdomain = str(proj.get("subdomain") or f"{slug}.pages.dev").strip()
            return {
                "project_name": slug,
                "subdomain": subdomain,
                "domains": [subdomain],
                "created": True,
            }
        last_code = _cf_error_code(c_payload) or str(c_status)
        if c_status in (401, 403):
            raise RuntimeError(f"cf_pages_create_permission_denied:{last_code}")

    raise RuntimeError(f"cf_pages_no_slug_available:{last_code}")


def deploy_pages_directory(
    project_name: str,
    storefront_dir: Path,
    account_id: str,
    api_token: str,
) -> None:
    """Deploy static `storefront/` from an isolated temp directory via Wrangler 4.156.1."""
    with tempfile.TemporaryDirectory(prefix="raawaa-pages-") as tmp:
        tmp_path = Path(tmp)
        site_dir = tmp_path / "site"
        shutil.copytree(storefront_dir, site_dir)
        (tmp_path / "wrangler.toml").write_text(
            f'name = "{project_name}"\n'
            f'compatibility_date = "{PAGES_COMPAT_DATE}"\n'
            f'pages_build_output_dir = "./site"\n',
            encoding="utf-8",
        )
        child_env = {
            **os.environ,
            "CLOUDFLARE_API_TOKEN": api_token,
            "CLOUDFLARE_ACCOUNT_ID": account_id,
        }
        cmd = [
            "npx",
            "--yes",
            "wrangler@4.156.1",
            "pages",
            "deploy",
            "./site",
            f"--project-name={project_name}",
            "--branch=main",
            "--commit-message=release",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(tmp_path),
            env=child_env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            diag = _sanitize_error_snippet((proc.stderr or "") + "\n" + (proc.stdout or ""))
            raise RuntimeError(f"pages_deploy({project_name}):{diag}")


def verify_public_storefront(base_url: str, attempts: int = 15, delay_sec: float = 4.0) -> bool:
    """Confirm both `/` and `/owner/` return HTTP 200 on the public clean URL."""
    clean_base = base_url.rstrip("/")
    for _ in range(attempts):
        ok_both = True
        for suffix in ("/", "/owner/"):
            req = urllib.request.Request(
                f"{clean_base}{suffix}",
                headers={"User-Agent": "RaawaaStorefrontVerifier/1.0"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                    if resp.status != 200 or "رواء" not in body:
                        ok_both = False
                        break
            except Exception:
                ok_both = False
                break
        if ok_both:
            return True
        time.sleep(delay_sec)
    return False


def retire_legacy_personal_name_worker(
    account_id: str,
    api_token: str,
    request_fn=_json_request,
) -> bool:
    """Disable and delete ONLY `raawaa-storefront` so `<name>.workers.dev` goes offline."""
    worker = LEGACY_WORKER_TO_RETIRE
    assert worker == "raawaa-storefront", "Safety guard: never touch control/staging workers"
    auth_headers = {"Authorization": f"Bearer {api_token}"}
    sub_url = f"{CF_API_BASE}/accounts/{account_id}/workers/scripts/{worker}/subdomain"
    script_url = f"{CF_API_BASE}/accounts/{account_id}/workers/scripts/{worker}"
    request_fn("POST", sub_url, auth_headers, {"enabled": False})
    status, _ = request_fn("DELETE", script_url, auth_headers, None)
    return status in (200, 204, 404)


def publish_github_deployment_status(
    state: str,
    environment_url: str,
    description: str,
    env: dict[str, str] | None = None,
    request_fn=_json_request,
) -> None:
    """Record non-secret deployment result & clean URL in GitHub Deployments API."""
    src = env if env is not None else os.environ
    gh_token = src.get("GITHUB_TOKEN", "").strip()
    repo = src.get("GITHUB_REPOSITORY", "").strip()
    sha = src.get("GITHUB_SHA", "").strip()
    if not (gh_token and repo and sha):
        return

    headers = {
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    dep_status, dep_payload = request_fn(
        "POST",
        f"https://api.github.com/repos/{repo}/deployments",
        headers,
        {
            "ref": sha,
            "environment": "storefront-production",
            "auto_merge": False,
            "required_contexts": [],
            "production_environment": True,
            "description": description[:140],
        },
    )
    dep_id = dep_payload.get("id")
    if dep_status in (200, 201) and dep_id:
        status_body: dict[str, Any] = {
            "state": state,
            "description": description[:140],
        }
        if environment_url.startswith("https://"):
            status_body["environment_url"] = environment_url
        request_fn(
            "POST",
            f"https://api.github.com/repos/{repo}/deployments/{dep_id}/statuses",
            headers,
            status_body,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="storefront", help="Static storefront directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate local bundle only")
    args = parser.parse_args(argv)

    storefront_dir = Path(args.dir).resolve()
    if not (storefront_dir / "index.html").is_file() or not (storefront_dir / "owner" / "index.html").is_file():
        print("Missing storefront/index.html or storefront/owner/index.html", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Dry-run OK: {storefront_dir} ready for clean Pages deployment.")
        return 0

    token, account_id = resolve_cloudflare_credentials()
    if not token or not account_id:
        publish_github_deployment_status(
            "failure", "", "missing_cloudflare_credentials"
        )
        print("Missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID.", file=sys.stderr)
        return 1

    try:
        selected = select_or_create_pages_project(account_id, token)
        project_name = selected["project_name"]
        subdomain = selected["subdomain"]
        custom_domains = [
            d for d in selected.get("domains", []) if not d.endswith(".pages.dev")
        ]
        public_host = custom_domains[0] if custom_domains else subdomain
        public_url = f"https://{public_host}/"
        owner_url = f"https://{public_host}/owner/"

        deploy_pages_directory(project_name, storefront_dir, account_id, token)
        live_ok = verify_public_storefront(public_url)
        if not live_ok:
            raise RuntimeError(f"live_verification_timeout:{public_host}")

        retired = retire_legacy_personal_name_worker(account_id, token)

        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"### Clean Brand Storefront Published (`{project_name}`)\n"
                    f"- Storefront: {public_url}\n"
                    f"- Owner Dashboard: {owner_url}\n"
                    f"- Legacy personal-name worker retired: `{retired}`\n"
                )

        publish_github_deployment_status(
            "success",
            public_url,
            f"Live at {public_url} (owner: {owner_url})",
        )
        print(f"Published clean storefront: {public_url}")
        print(f"Published clean owner dashboard: {owner_url}")
        return 0
    except Exception as exc:
        reason = str(exc)[:135]
        publish_github_deployment_status("failure", "", reason)
        print(f"Clean storefront publish failed: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
