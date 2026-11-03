"""Publish explicit, sanitized GitHub/Crossref scan-run telemetry."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API_HOST = "raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev"
USER_AGENT = "RaawaaAutonomousEngine-Research/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"


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
    github_requests = snapshot.get("requests_made")
    crossref_requests = snapshot.get("academic_requests_made")
    github_unavailable = snapshot.get("unavailable")
    crossref_unavailable = snapshot.get("academic_unavailable")
    if not isinstance(github_requests, int) or isinstance(github_requests, bool) or github_requests < 0:
        raise ScanTelemetryError("invalid GitHub request count")
    if not isinstance(crossref_requests, int) or isinstance(crossref_requests, bool) or crossref_requests < 0:
        raise ScanTelemetryError("invalid Crossref request count")
    if not isinstance(github_unavailable, dict):
        raise ScanTelemetryError("invalid GitHub source result")
    if crossref_unavailable is not None and not isinstance(crossref_unavailable, str):
        raise ScanTelemetryError("invalid Crossref source result")
    candidates = snapshot.get("candidates", [])
    academic = snapshot.get("academic_candidates", [])
    if not isinstance(candidates, list) or not isinstance(academic, list):
        raise ScanTelemetryError("invalid candidate collections")
    unavailable_count = len(github_unavailable) + int(bool(crossref_unavailable))
    requested_sources = int(github_requests > 0) + int(crossref_requests > 0)
    if requested_sources != 2:
        status = "failure"
    elif unavailable_count:
        status = "partial" if unavailable_count < 2 else "failure"
    else:
        status = "success"
    scan_id = f"{environ['GITHUB_RUN_ID']}:{environ['GITHUB_RUN_ATTEMPT']}"
    scan = {
        "scan_id": scan_id, "scan_type": "github_crossref",
        "workflow": "research-discovery.yml",
        "run_id": int(environ["GITHUB_RUN_ID"]),
        "run_number": int(environ["GITHUB_RUN_NUMBER"]),
        "run_attempt": int(environ["GITHUB_RUN_ATTEMPT"]),
        "head_sha": sha, "started_at": started, "completed_at": completed,
        "status": status, "requests_made": github_requests,
        "academic_requests_made": crossref_requests,
        "candidate_count": len(candidates), "academic_candidate_count": len(academic),
        "unavailable_query_count": unavailable_count,
    }
    observations = []
    for source, requests, items, unavailable in (
        ("github", github_requests, candidates, bool(github_unavailable)),
        ("crossref", crossref_requests, academic, bool(crossref_unavailable)),
    ):
        state = "unavailable" if unavailable or requests == 0 else "fresh"
        observation = {
            "scan_id": scan_id, "source": source, "status": state,
            "observed_at": completed, "freshness_hours": 24,
            "request_count": requests, "item_count": len(items),
            "reason_code": _reason_code(crossref_unavailable if source == "crossref" else next(iter(github_unavailable.values()), None)) if state == "unavailable" else None,
        }
        observations.append(observation)
    return {"scan": scan, "source_observations": observations}


def publish(payload: dict, environ: dict[str, str], opener=None) -> dict:
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").strip()
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "").strip()
    if api_url != "https://" + API_HOST or not token:
        raise ScanTelemetryError("exact approved Engine Worker origin and API token are required")
    headers = {
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": USER_AGENT,
    }
    access_id = environ.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "").strip()
    access_secret = environ.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "").strip()
    if bool(access_id) != bool(access_secret):
        raise ScanTelemetryError("Cloudflare Access credentials must be configured as a pair")
    if access_id:
        headers["CF-Access-Client-Id"] = access_id
        headers["CF-Access-Client-Secret"] = access_secret
    request = Request(api_url + "/v1/research/scan-runs",
                      data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        with (opener or urlopen)(request, timeout=20) as response:
            body = response.read(16 * 1024 + 1)
            if len(body) > 16 * 1024 or response.status not in (200, 201):
                raise ScanTelemetryError("scan telemetry endpoint returned an invalid response")
            result = json.loads(body.decode("utf-8"))
            if not isinstance(result, dict) or result.get("ok") is not True or result.get("scan_id") != payload["scan"]["scan_id"]:
                raise ScanTelemetryError("scan telemetry readback acknowledgement is invalid")
            return result
    except HTTPError as exc:
        raise ScanTelemetryError(f"scan telemetry endpoint returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise ScanTelemetryError("scan telemetry endpoint unavailable or returned invalid JSON") from None


def main() -> int:
    snapshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research-candidates.json")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload = build_payload(snapshot, dict(os.environ))
        result = publish(payload, dict(os.environ))
        print(json.dumps({"status": "recorded", "scan_id": result["scan_id"],
                          "recorded_events": result["recorded_events"],
                          "scan_status": payload["scan"]["status"]}, separators=(",", ":")))
        return 0
    except (OSError, json.JSONDecodeError, ScanTelemetryError, ValueError) as exc:
        print(f"Research scan telemetry failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
