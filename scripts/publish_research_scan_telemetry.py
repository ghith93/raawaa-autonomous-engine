"""Publish sanitized GitHub/Crossref scan telemetry directly to isolated Staging D1."""
from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.d1_client import D1Client, D1APIError, D1ConfigurationError

STAGING_DATABASE_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
PRODUCTION_DATABASE_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"


class ScanTelemetryError(RuntimeError):
    pass


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ScanTelemetryError("invalid scan timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScanTelemetryError("scan timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reason_code(reason: object) -> str:
    text = reason if isinstance(reason, str) else ""
    match = re.search(r"HTTP (\d{3})", text)
    if match:
        return "http_" + match.group(1)
    if "rate_limit" in text or "rate limit" in text:
        return "rate_limited"
    if "Timeout" in text:
        return "timeout"
    return "request_failed" if text else "source_unavailable"


def _validate_staging_target(root: Path) -> None:
    with (root / "wrangler.staging.toml").open("rb") as stream:
        staging = tomllib.load(stream)
    with (root / "wrangler.toml").open("rb") as stream:
        production = tomllib.load(stream)
    staging_bindings = staging.get("d1_databases", [])
    production_bindings = production.get("d1_databases", [])
    if len(staging_bindings) != 1 or len(production_bindings) != 1:
        raise ScanTelemetryError("expected exactly one D1 binding in each Wrangler config")
    stage, prod = staging_bindings[0], production_bindings[0]
    if (staging.get("name") != "raawaa-autonomous-engine-staging"
        or stage.get("database_name") != "raawaa-engine-staging"
        or stage.get("database_id") != STAGING_DATABASE_ID
        or prod.get("database_id") != PRODUCTION_DATABASE_ID
        or stage.get("database_id") == prod.get("database_id")):
        raise ScanTelemetryError("refusing scan telemetry write: Staging/production identity mismatch")


def build_payload(snapshot: dict, environ: dict[str, str], now: datetime | None = None) -> dict:
    if not isinstance(snapshot, dict):
        raise ScanTelemetryError("research snapshot must be an object")
    required = ("GITHUB_RUN_ID", "GITHUB_RUN_NUMBER", "GITHUB_RUN_ATTEMPT",
                "GITHUB_SHA", "GITHUB_WORKFLOW", "RESEARCH_SCAN_STARTED_AT")
    missing = [key for key in required if not environ.get(key, "").strip()]
    if missing:
        raise ScanTelemetryError("missing scan telemetry settings: " + ",".join(missing))
    if environ["GITHUB_WORKFLOW"] != "Scheduled Open-Source Research Discovery":
        raise ScanTelemetryError("unexpected workflow")
    if not all(environ[key].isdigit() for key in ("GITHUB_RUN_ID", "GITHUB_RUN_NUMBER", "GITHUB_RUN_ATTEMPT")):
        raise ScanTelemetryError("invalid workflow run identifiers")
    sha = environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ScanTelemetryError("invalid workflow commit SHA")
    started = _timestamp(environ["RESEARCH_SCAN_STARTED_AT"])
    completed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if datetime.fromisoformat(completed.replace("Z", "+00:00")) < datetime.fromisoformat(started.replace("Z", "+00:00")):
        raise ScanTelemetryError("scan completion precedes start")

    source_specs = (
        ("github", "requests_made", "candidates", "unavailable", True),
        ("crossref", "academic_requests_made", "academic_candidates", "academic_unavailable", None),
        ("arxiv", "arxiv_requests_made", "arxiv_candidates", "arxiv_unavailable", None),
        ("searxng", "searxng_requests_made", "searxng_candidates", "searxng_unavailable",
         snapshot.get("searxng_endpoint_configured") is True),
    )
    normalized = []
    for source, request_key, item_key, unavailable_key, configured_override in source_specs:
        requests = snapshot.get(request_key, 0)
        items = snapshot.get(item_key, [])
        unavailable = snapshot.get(unavailable_key)
        if type(requests) is not int or requests < 0 or not isinstance(items, list):
            raise ScanTelemetryError("invalid " + source + " source request count or candidate collection")
        if source == "github" and not isinstance(unavailable, dict):
            raise ScanTelemetryError("invalid GitHub source result metadata")
        if source != "github" and unavailable is not None and not isinstance(unavailable, str):
            raise ScanTelemetryError("invalid " + source + " source result metadata")
        configured = (
            configured_override if configured_override is not None
            else requests > 0 or unavailable is not None
        )
        # A missing optional endpoint is explicitly not configured, not a failed scan.
        if source == "searxng" and unavailable == "endpoint_not_configured":
            configured = False
        failed = configured and (requests == 0 or bool(unavailable))
        state = "not_configured" if not configured else "unavailable" if failed else "fresh"
        reason = (
            next(iter(unavailable.values()), None) if source == "github" and isinstance(unavailable, dict)
            else unavailable if isinstance(unavailable, str) else None
        )
        normalized.append({
            "source": source, "requests": requests, "items": items,
            "unavailable": unavailable, "configured": configured,
            "failed": failed, "status": state, "reason": reason,
        })

    configured_sources = [item for item in normalized if item["configured"]]
    failed_sources = [item for item in configured_sources if item["failed"]]
    successful_sources = [item for item in configured_sources if not item["failed"]]
    status = (
        "failure" if not successful_sources
        else "partial" if failed_sources
        else "success"
    )
    github_unavailable = snapshot.get("unavailable", {})
    crossref_unavailable = snapshot.get("academic_unavailable")
    arxiv_unavailable = snapshot.get("arxiv_unavailable")
    searxng_unavailable = snapshot.get("searxng_unavailable")
    unavailable_count = (
        len(github_unavailable) if isinstance(github_unavailable, dict) else 1
    ) + sum(bool(value) and value != "endpoint_not_configured"
            for value in (crossref_unavailable, arxiv_unavailable, searxng_unavailable))
    scan_id = f"{environ['GITHUB_RUN_ID']}:{environ['GITHUB_RUN_ATTEMPT']}"
    counts = {item["source"]: len(item["items"]) for item in normalized}
    scan = {
        "scan_id": scan_id, "scan_type": "multi_source_public_research",
        "workflow": "research-discovery.yml",
        "run_id": int(environ["GITHUB_RUN_ID"]),
        "run_number": int(environ["GITHUB_RUN_NUMBER"]),
        "run_attempt": int(environ["GITHUB_RUN_ATTEMPT"]),
        "head_sha": sha, "started_at": started, "completed_at": completed,
        "status": status,
        "requests_made": snapshot.get("requests_made", 0),
        "academic_requests_made": snapshot.get("academic_requests_made", 0),
        "arxiv_requests_made": snapshot.get("arxiv_requests_made", 0),
        "searxng_requests_made": snapshot.get("searxng_requests_made", 0),
        "searxng_endpoint_configured": snapshot.get("searxng_endpoint_configured") is True,
        "candidate_counts": counts,
        "total_candidate_count": sum(counts.values()),
        "configured_source_count": len(configured_sources),
        "unavailable_source_count": len(failed_sources),
        "unavailable_query_count": unavailable_count,
    }
    observations = []
    for item in normalized:
        state = item["status"]
        observations.append({
            "scan_id": scan_id, "source": item["source"], "status": state,
            "observed_at": completed, "freshness_hours": 24,
            "request_count": item["requests"], "item_count": len(item["items"]),
            "reason_code": _reason_code(item["reason"]) if state == "unavailable" else None,
        })
    return {"scan": scan, "source_observations": observations}


def build_statements(payload: dict) -> list[dict]:
    scan = payload["scan"]
    scan_id = scan["scan_id"]
    statements = [{
        "sql": "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT NULL,'research_scan_completed',?,? WHERE NOT EXISTS (SELECT 1 FROM audit_log WHERE event='research_scan_completed' AND json_extract(details_json,'$.scan_id')=?)",
        "params": [json.dumps(scan, separators=(",", ":")), scan["completed_at"], scan_id],
    }]
    for item in payload["source_observations"]:
        statements.append({
            "sql": "INSERT INTO audit_log(task_id,event,details_json,created_at) SELECT NULL,'research_source_observation',?,? WHERE NOT EXISTS (SELECT 1 FROM audit_log WHERE event='research_source_observation' AND json_extract(details_json,'$.scan_id')=? AND json_extract(details_json,'$.source')=?)",
            "params": [json.dumps(item, separators=(",", ":")), item["observed_at"], scan_id, item["source"]],
        })
    return statements


def publish(payload: dict, environ: dict[str, str], *, root: Path = ROOT, client_factory=None) -> dict:
    _validate_staging_target(root)
    account = environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise ScanTelemetryError("Cloudflare Staging D1 credentials are required")
    client = (client_factory or D1Client)(
        account_id=account, database_id=STAGING_DATABASE_ID, api_token=token
    )
    try:
        health = client.query("SELECT 1 AS ok")
        if not health.get("results") or health["results"][0].get("ok") != 1:
            raise ScanTelemetryError("Staging D1 healthcheck failed")
        results = client.batch(build_statements(payload))
        inserted = sum(int(item.get("meta", {}).get("changes", 0)) for item in results)
        rows = client.query(
            "SELECT event,details_json FROM audit_log WHERE (event='research_scan_completed' OR event='research_source_observation') AND json_extract(details_json,'$.scan_id')=? ORDER BY event",
            [payload["scan"]["scan_id"]],
        ).get("results", [])
        parsed = []
        for row in rows:
            try:
                parsed.append((row.get("event"), json.loads(row.get("details_json", "{}"))))
            except (TypeError, json.JSONDecodeError):
                continue
        scan_rows = [item for event, item in parsed if event == "research_scan_completed"]
        source_rows = [item for event, item in parsed if event == "research_source_observation"]
        if len(scan_rows) != 1 or len(source_rows) != 4 or {item.get("source") for item in source_rows} != {"github", "crossref", "arxiv", "searxng"}:
            raise ScanTelemetryError("Staging D1 readback did not confirm one scan and two source observations")
        return {"status": "recorded", "scan_id": payload["scan"]["scan_id"],
                "inserted_events": inserted, "verified_events": 5,
                "database_id": STAGING_DATABASE_ID}
    except (D1APIError, D1ConfigurationError) as exc:
        raise ScanTelemetryError("Staging D1 telemetry write/readback failed: " + type(exc).__name__) from None


def main() -> int:
    snapshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research-candidates.json")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload = build_payload(snapshot, dict(os.environ))
        result = publish(payload, dict(os.environ))
        print(json.dumps({key: result[key] for key in ("status","scan_id","inserted_events","verified_events","database_id")}, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, ScanTelemetryError, ValueError) as exc:
        print(f"Research scan telemetry failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
