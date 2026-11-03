"""Publish explicit research scan/source observations to isolated Staging D1."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STAGING_DATABASE_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
PRODUCTION_DATABASE_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"
MAX_INPUT_BYTES = 1024 * 1024
SOURCE_THRESHOLDS = {
    "github_public_repository_search": 24 * 60 * 60,
    "crossref_public_works_search": 48 * 60 * 60,
}


def _aware_timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(f"{name} must be a timezone-aware ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    normalized = parsed.astimezone(timezone.utc).isoformat()
    if parsed.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
        raise ValueError(f"{name} is unexpectedly in the future")
    return normalized


def _nonnegative_int(value: Any, name: str, maximum: int = 1_000_000) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f"{name} must be a bounded non-negative integer")
    return value


def build_events(snapshot: dict, metadata: dict) -> list[dict]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("unsupported research discovery snapshot")
    completed_at = _aware_timestamp(snapshot.get("retrieved_at"), "retrieved_at")
    started_at = _aware_timestamp(metadata.get("started_at"), "started_at")
    if datetime.fromisoformat(started_at) > datetime.fromisoformat(completed_at):
        raise ValueError("scan start timestamp must not follow completion")

    run_id = _nonnegative_int(metadata.get("run_id"), "run_id")
    run_number = _nonnegative_int(metadata.get("run_number"), "run_number")
    run_attempt = _nonnegative_int(metadata.get("run_attempt"), "run_attempt", 100)
    if run_id < 1 or run_number < 1 or run_attempt < 1:
        raise ValueError("GitHub run identity values must be positive")
    head_sha = metadata.get("head_sha")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", head_sha):
        raise ValueError("head_sha must be a full Git commit SHA")
    scan_id = f"{run_id}:{run_attempt}"

    queries_attempted = _nonnegative_int(snapshot.get("queries_attempted"), "queries_attempted", 10)
    github_requests = _nonnegative_int(snapshot.get("requests_made"), "requests_made", 10)
    candidate_count = _nonnegative_int(snapshot.get("candidate_count"), "candidate_count", 100)
    academic_requests = _nonnegative_int(snapshot.get("academic_requests_made", 0), "academic_requests_made", 1)
    academic_count = len(snapshot.get("academic_candidates", [])) if isinstance(snapshot.get("academic_candidates", []), list) else -1
    academic_count = _nonnegative_int(academic_count, "academic_candidate_count", 10)
    unavailable = snapshot.get("unavailable", {})
    if not isinstance(unavailable, dict):
        raise ValueError("unavailable must be an object")
    if len(unavailable) > queries_attempted:
        raise ValueError("unavailable query count exceeds attempted queries")
    academic_unavailable = snapshot.get("academic_unavailable")
    if academic_unavailable is not None and not isinstance(academic_unavailable, str):
        raise ValueError("academic_unavailable must be a string or null")
    if github_requests != queries_attempted:
        raise ValueError("GitHub request count must match attempted queries")

    github_failures = len(unavailable)
    github_status = (
        "unavailable" if queries_attempted == 0 or github_failures == queries_attempted
        else "partial" if github_failures else "fresh"
    )
    source_events = [{
        "event": "source_observation",
        "created_at": completed_at,
        "details": {
            "schema_version": 1, "scan_id": scan_id,
            "source_key": "github_public_repository_search",
            "observed_at": completed_at,
            "freshness_threshold_seconds": SOURCE_THRESHOLDS["github_public_repository_search"],
            "status": github_status,
            "requests_made": github_requests,
            "item_count": candidate_count,
            "unavailable_count": github_failures,
            "reason_code": "partial_failure" if github_failures and github_status == "partial"
                else "request_failed" if github_status == "unavailable" else None,
        },
    }]

    if academic_requests:
        crossref_status = "unavailable" if academic_unavailable else "fresh"
        source_events.append({
            "event": "source_observation",
            "created_at": completed_at,
            "details": {
                "schema_version": 1, "scan_id": scan_id,
                "source_key": "crossref_public_works_search",
                "observed_at": completed_at,
                "freshness_threshold_seconds": SOURCE_THRESHOLDS["crossref_public_works_search"],
                "status": crossref_status,
                "requests_made": academic_requests,
                "item_count": academic_count,
                "unavailable_count": 1 if academic_unavailable else 0,
                "reason_code": "request_failed" if academic_unavailable else None,
            },
        })

    unavailable_sources = sum(event["details"]["status"] == "unavailable" for event in source_events)
    partial_sources = sum(event["details"]["status"] == "partial" for event in source_events)
    scan_status = (
        "failed" if unavailable_sources == len(source_events)
        else "partial" if unavailable_sources or partial_sources
        else "success"
    )
    total_requests = _nonnegative_int(snapshot.get("total_requests_made", github_requests + academic_requests), "total_requests_made", 11)
    if total_requests != github_requests + academic_requests:
        raise ValueError("total request count does not match source request counts")
    scan_details = {
        "schema_version": 1,
        "scan_id": scan_id,
        "scan_type": "open_source_research_discovery",
        "workflow": "Scheduled Open-Source Research Discovery",
        "run_id": run_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": scan_status,
        "requests_made": total_requests,
        "candidate_count": candidate_count,
        "academic_candidate_count": academic_count,
        "source_count": len(source_events),
        "unavailable_source_count": unavailable_sources + partial_sources,
    }
    return [{
        "event": "scan_completed",
        "created_at": completed_at,
        "details": scan_details,
    }, *source_events]


def publish(snapshot_path: Path, environ: dict[str, str] | None = None, client=None) -> dict:
    env = os.environ if environ is None else environ
    raw = snapshot_path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("research snapshot exceeds the 1 MiB safety limit")
    snapshot = json.loads(raw.decode("utf-8"))
    metadata = {
        "run_id": int(env.get("GITHUB_RUN_ID", "0")),
        "run_number": int(env.get("GITHUB_RUN_NUMBER", "0")),
        "run_attempt": int(env.get("GITHUB_RUN_ATTEMPT", "0")),
        "head_sha": env.get("GITHUB_SHA", ""),
        "started_at": env.get("RAAWAA_SCAN_STARTED_AT", ""),
    }
    events = build_events(snapshot, metadata)
    configured_database = env.get("D1_DATABASE_ID", "")
    if configured_database and configured_database not in {STAGING_DATABASE_ID}:
        raise ValueError("refusing scan telemetry write: D1_DATABASE_ID is not the exact Staging database")
    if STAGING_DATABASE_ID == PRODUCTION_DATABASE_ID:
        raise ValueError("Staging and production database IDs must differ")
    if client is None:
        from engine.d1_client import D1Client
        client = D1Client(database_id=STAGING_DATABASE_ID)
    statements = []
    for event in events:
        if event["event"] == "scan_completed":
            statements.append({
                "sql": """
                    INSERT INTO audit_log(task_id,event,details_json,created_at)
                    SELECT NULL,?,?,?
                    WHERE NOT EXISTS (
                      SELECT 1 FROM audit_log
                      WHERE event='scan_completed'
                        AND json_extract(details_json,'$.scan_id')=?
                    )
                """,
                "params": [
                    event["event"], json.dumps(event["details"], separators=(",", ":"), sort_keys=True),
                    event["created_at"], event["details"]["scan_id"],
                ],
            })
        else:
            statements.append({
                "sql": """
                    INSERT INTO audit_log(task_id,event,details_json,created_at)
                    SELECT NULL,?,?,?
                    WHERE NOT EXISTS (
                      SELECT 1 FROM audit_log
                      WHERE event='source_observation'
                        AND json_extract(details_json,'$.scan_id')=?
                        AND json_extract(details_json,'$.source_key')=?
                    )
                """,
                "params": [
                    event["event"], json.dumps(event["details"], separators=(",", ":"), sort_keys=True),
                    event["created_at"], event["details"]["scan_id"], event["details"]["source_key"],
                ],
            })
    client.batch(statements)
    return {
        "status": "published",
        "database": "raawaa-engine-staging",
        "scan_id": events[0]["details"]["scan_id"],
        "scan_status": events[0]["details"]["status"],
        "source_events": len(source_events := events[1:]),
        "requests_made": events[0]["details"]["requests_made"],
        "candidate_count": events[0]["details"]["candidate_count"],
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    print(json.dumps(publish(args.snapshot), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(str(exc)) from None
