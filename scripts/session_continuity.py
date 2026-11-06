"""Deterministic session-start preflight and Issue #464 watermark continuity tool.

This script intentionally avoids creating a second memory database or agent runtime.
Instead, it validates the existing repository policy/register files, reconstructs
live capability counts directly from the checked-out codebase, and computes the
unprocessed GitHub Issue #464 comment inbox above the recorded Comment ID watermark.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.operating_policy import load_operating_policy

DEFAULT_MANIFEST_REL = "config/session_continuity.json"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ARENA_HEADER_RE = re.compile(r"^\s*(?:#+\s*)?ARENA(?:\s+REPORT|\s+follow-up|\b)", re.IGNORECASE)
COMMENT_ID_NUM_RE = re.compile(r"\b(6[0-9]{9,11})\b")


class SessionContinuityError(RuntimeError):
    """Raised when the continuity manifest or required bootstrap files fail validation."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest(root: Path = ROOT, manifest_rel: str = DEFAULT_MANIFEST_REL) -> dict[str, Any]:
    path = root / manifest_rel
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionContinuityError(f"manifest_unreadable:{manifest_rel}:{type(exc).__name__}") from exc
    validate_manifest(data, root=root)
    return data


def validate_manifest(data: Any, *, root: Path = ROOT) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SessionContinuityError("manifest_invalid_schema_version")

    channel = data.get("channel")
    if not isinstance(channel, dict):
        raise SessionContinuityError("manifest_missing_channel")
    if channel.get("repository") != "ghith93/raawaa-autonomous-engine":
        raise SessionContinuityError("manifest_unexpected_repository")
    if channel.get("issue_number") != 464:
        raise SessionContinuityError("manifest_unexpected_issue_number")
    if channel.get("report_header_prefix") != "ARENA REPORT":
        raise SessionContinuityError("manifest_invalid_report_header_prefix")
    if channel.get("automatic_session_wake_supported") is not False:
        raise SessionContinuityError("manifest_must_declare_no_automatic_wake")
    if channel.get("unofficial_wake_interfaces_prohibited") is not True:
        raise SessionContinuityError("manifest_must_prohibit_unofficial_wake")

    required_files = data.get("startup_required_files")
    if not isinstance(required_files, list) or not required_files:
        raise SessionContinuityError("manifest_missing_startup_required_files")
    for rel in required_files:
        if not isinstance(rel, str) or not rel.strip():
            raise SessionContinuityError("manifest_invalid_required_file_entry")
        fpath = root / rel
        if not fpath.is_file():
            raise SessionContinuityError(f"required_bootstrap_file_missing:{rel}")
        if not fpath.read_text(encoding="utf-8").strip():
            raise SessionContinuityError(f"required_bootstrap_file_empty:{rel}")

    watermark = data.get("watermark")
    if not isinstance(watermark, dict):
        raise SessionContinuityError("manifest_missing_watermark")
    cid = watermark.get("last_processed_comment_id")
    if type(cid) is not int or cid <= 0:
        raise SessionContinuityError("watermark_invalid_comment_id")
    sha = watermark.get("last_verified_main_sha")
    if not isinstance(sha, str) or not SHA40_RE.fullmatch(sha):
        raise SessionContinuityError("watermark_invalid_main_sha")
    created_at = watermark.get("last_processed_comment_created_at")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise SessionContinuityError("watermark_invalid_created_at")


def reconstruct_repo_capabilities(root: Path = ROOT) -> dict[str, Any]:
    """Derive live capability, workflow, integration, and budget facts from the repo tree."""
    policy_check = load_operating_policy(root)
    policy = json.loads((root / "config" / "policy.json").read_text(encoding="utf-8"))
    agents = json.loads((root / "config" / "agents.json").read_text(encoding="utf-8"))
    profiles = json.loads((root / "config" / "provider_profiles.json").read_text(encoding="utf-8"))
    integrations = json.loads((root / "config" / "integrations.json").read_text(encoding="utf-8"))
    tool_perms = json.loads((root / "config" / "tool_permissions.json").read_text(encoding="utf-8"))

    workflow_dir = root / ".github" / "workflows"
    workflow_files = (
        sorted(p.name for p in workflow_dir.iterdir() if p.is_file() and p.suffix in {".yml", ".yaml"})
        if workflow_dir.is_dir()
        else []
    )

    raw_profiles = profiles.get("routes", profiles.get("profiles", []))
    enabled_profiles = [
        p.get("id") for p in raw_profiles if isinstance(p, dict) and p.get("enabled") is True
    ]
    integration_map = integrations.get("integrations", {})
    integration_statuses = {
        key: val.get("status", "unknown")
        for key, val in sorted(integration_map.items())
        if isinstance(val, dict)
    }

    return {
        "policy_documents_verified": len(policy_check["documents_loaded"]),
        "policy_mode": policy.get("mode"),
        "monthly_budget_usd": policy.get("monthly_budget_usd"),
        "monthly_total_hard_cap_usd": policy.get("budget_envelope", {}).get("monthly_total_hard_cap_usd"),
        "paid_model_calls_allowed": policy.get("allow_paid_model_calls"),
        "external_writes_allowed": policy.get("allow_external_writes"),
        "agent_roles_count": len(agents.get("agents", [])),
        "provider_profiles_count": len(raw_profiles),
        "enabled_provider_profiles": enabled_profiles,
        "tool_permissions_count": len(tool_perms.get("tools", {})),
        "workflows_count": len(workflow_files),
        "workflow_files": workflow_files,
        "integrations": integration_statuses,
        "local_memory_note": "engine/memory.py uses local SQLite (ephemeral per runner/sandbox); durable cross-session continuity uses GitHub Issue #464 + config/session_continuity.json.",
    }


def compute_inbox_state(
    manifest: dict[str, Any],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute effective watermark and unprocessed comments from Issue #464 comment records."""
    repo_watermark = int(manifest["watermark"]["last_processed_comment_id"])
    latest_arena_report_id = 0
    arena_referenced_watermark = 0

    normalized: list[dict[str, Any]] = []
    for item in comments:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if type(cid) is not int:
            continue
        body = str(item.get("body") or "")
        first_line = body.lstrip().splitlines()[0] if body.strip() else ""
        if ARENA_HEADER_RE.match(first_line):
            latest_arena_report_id = max(latest_arena_report_id, cid)
            for match in COMMENT_ID_NUM_RE.findall(body[:1500]):
                val = int(match)
                if val <= cid:
                    arena_referenced_watermark = max(arena_referenced_watermark, val)
        normalized.append(
            {
                "id": cid,
                "created_at": str(item.get("created_at") or ""),
                "user": (item.get("user") or {}).get("login", "") if isinstance(item.get("user"), dict) else "",
                "first_line": first_line[:140],
                "html_url": str(item.get("html_url") or ""),
            }
        )

    normalized.sort(key=lambda row: row["id"])
    effective_watermark = max(repo_watermark, latest_arena_report_id, arena_referenced_watermark)
    unprocessed = [row for row in normalized if row["id"] > effective_watermark]

    return {
        "repo_watermark_comment_id": repo_watermark,
        "latest_arena_report_comment_id": latest_arena_report_id or None,
        "effective_watermark_comment_id": effective_watermark,
        "unprocessed_count": len(unprocessed),
        "unprocessed_comments": unprocessed,
        "highest_seen_comment_id": normalized[-1]["id"] if normalized else effective_watermark,
    }


def _gh_api_json(endpoint: str) -> Any:
    proc = subprocess.run(
        ["gh", "api", endpoint],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if proc.returncode != 0:
        raise SessionContinuityError(f"gh_api_failed:{endpoint}:{proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def fetch_live_github_state(manifest: dict[str, Any]) -> dict[str, Any]:
    repo = manifest["channel"]["repository"]
    issue_num = manifest["channel"]["issue_number"]
    since = manifest["watermark"]["last_processed_comment_created_at"]

    main_ref = _gh_api_json(f"repos/{repo}/commits/main")
    pulls = _gh_api_json(f"repos/{repo}/pulls?state=open&per_page=30")
    comments = _gh_api_json(f"repos/{repo}/issues/{issue_num}/comments?since={since}&per_page=100")

    inbox = compute_inbox_state(manifest, comments if isinstance(comments, list) else [])
    open_prs = [
        {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "draft": bool(pr.get("draft")),
            "head_ref": (pr.get("head") or {}).get("ref"),
            "head_sha": ((pr.get("head") or {}).get("sha") or "")[:12],
        }
        for pr in (pulls if isinstance(pulls, list) else [])
        if isinstance(pr, dict)
    ]
    return {
        "live_main_sha": main_ref.get("sha") if isinstance(main_ref, dict) else None,
        "open_pull_requests_count": len(open_prs),
        "open_pull_requests": open_prs,
        "issue_464_inbox": inbox,
    }


def update_watermark(
    *,
    root: Path = ROOT,
    manifest_rel: str = DEFAULT_MANIFEST_REL,
    comment_id: int,
    comment_created_at: str,
    main_sha: str,
    summary: str,
    reviewed_task_comment_id: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(root=root, manifest_rel=manifest_rel)
    current_id = int(manifest["watermark"]["last_processed_comment_id"])
    if comment_id < current_id and not force:
        raise SessionContinuityError(
            f"watermark_regression_refused:{comment_id}<{current_id}"
        )
    if not SHA40_RE.fullmatch(main_sha):
        raise SessionContinuityError("watermark_invalid_main_sha")
    if not summary.strip():
        raise SessionContinuityError("watermark_empty_summary")

    issue_url = manifest["channel"]["issue_url"]
    manifest["watermark"]["last_processed_comment_id"] = int(comment_id)
    manifest["watermark"]["last_processed_comment_created_at"] = comment_created_at
    manifest["watermark"]["last_processed_comment_url"] = f"{issue_url}#issuecomment-{comment_id}"
    if reviewed_task_comment_id is not None:
        manifest["watermark"]["last_reviewed_task_comment_id"] = int(reviewed_task_comment_id)
    manifest["watermark"]["last_verified_main_sha"] = main_sha
    manifest["watermark"]["updated_at_utc"] = _utc_now_iso()
    manifest["watermark"]["summary"] = summary.strip()

    validate_manifest(manifest, root=root)
    target = root / manifest_rel
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify session continuity and Issue #464 watermark state.")
    sub = parser.add_subparsers(dest="command")

    status_p = sub.add_parser("status", help="Validate bootstrap files, capabilities, and optional live #464 inbox.")
    status_p.add_argument("--fetch-live", action="store_true", help="Query GitHub API for live main SHA, PRs, and unprocessed #464 comments.")

    rec_p = sub.add_parser("record", help="Update config/session_continuity.json with a verified session-end watermark.")
    rec_p.add_argument("--comment-id", type=int, required=True)
    rec_p.add_argument("--comment-created-at", required=True)
    rec_p.add_argument("--main-sha", required=True)
    rec_p.add_argument("--summary", required=True)
    rec_p.add_argument("--reviewed-task-comment-id", type=int, default=None)
    rec_p.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    cmd = args.command or "status"

    try:
        if cmd == "record":
            updated = update_watermark(
                comment_id=args.comment_id,
                comment_created_at=args.comment_created_at,
                main_sha=args.main_sha,
                summary=args.summary,
                reviewed_task_comment_id=args.reviewed_task_comment_id,
                force=args.force,
            )
            print(json.dumps({"status": "updated", "watermark": updated["watermark"]}, ensure_ascii=False, indent=2))
            return 0

        manifest = load_manifest(ROOT)
        capabilities = reconstruct_repo_capabilities(ROOT)
        report: dict[str, Any] = {
            "status": "ok",
            "channel": manifest["channel"],
            "watermark": manifest["watermark"],
            "capabilities": capabilities,
            "open_engineering_priorities": manifest.get("open_engineering_priorities", []),
        }
        if getattr(args, "fetch_live", False):
            report["live_github"] = fetch_live_github_state(manifest)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except SessionContinuityError as exc:
        print(f"Session continuity check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
