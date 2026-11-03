"""Read back Staging mobile status after CI/kill-switch telemetry publication."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

ALLOWED_HOST = "raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
MAX_RESPONSE_BYTES = 128 * 1024


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid recorded timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def verify(environ: dict[str, str] | None = None, *, opener=None, readiness_timeout_seconds: int = 90) -> dict:
    env = os.environ if environ is None else environ
    base = env.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    token = env.get("RAAWAA_ENGINE_STAGING_API_TOKEN", "")
    expected_run_id = env.get("GITHUB_RUN_ID", "")
    expected_run_number = env.get("GITHUB_RUN_NUMBER", "")
    expected_sha = env.get("GITHUB_SHA", "")
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST
        or parsed.username or parsed.password or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment
    ):
        raise ValueError("refusing mobile status verification outside the exact Staging Worker")
    if not token or not expected_run_id.isdigit() or not expected_run_number.isdigit():
        raise ValueError("missing Staging token or GitHub run identity")
    if not re.fullmatch(r"[0-9a-f]{40,64}", expected_sha):
        raise ValueError("invalid expected source SHA")

    request = urllib.request.Request(
        base + "/v1/mobile/status",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        },
        method="GET",
    )
    open_url = opener or urllib.request.build_opener(RejectRedirects()).open
    if type(readiness_timeout_seconds) is not int or not 1 <= readiness_timeout_seconds <= 180:
        raise ValueError("readiness timeout must be an integer between 1 and 180 seconds")
    deadline = time.monotonic() + readiness_timeout_seconds
    last_status = None
    while True:
        try:
            with open_url(request, timeout=20) as response:
                last_status = int(response.status)
                if last_status != 200:
                    raise RuntimeError("Staging mobile status returned HTTP " + str(last_status))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                break
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            # Wrangler confirms the secret upload before Cloudflare's Worker
            # isolates consistently observe the new value. Retry only the
            # authenticated, read-only status GET during this bounded window.
            if exc.code not in (401, 500, 502, 503, 504) or time.monotonic() >= deadline:
                raise RuntimeError("Staging mobile status returned HTTP " + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Staging mobile status request failed: " + type(exc).__name__) from None
        time.sleep(2)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Staging mobile status response exceeded its bound")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Staging mobile status returned invalid JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("Staging mobile status returned an invalid shape")

    ci = data.get("ci_health")
    if not isinstance(ci, dict):
        raise RuntimeError("CI health snapshot is not available through the mobile API")
    if (
        ci.get("available") is not True or ci.get("status") != "success"
        or ci.get("conclusion") != "success" or ci.get("freshness") != "fresh"
        or ci.get("workflow") != "Engine CI" or ci.get("run_id") != int(expected_run_id)
        or ci.get("run_number") != int(expected_run_number) or ci.get("head_sha") != expected_sha
        or not isinstance(ci.get("jobs"), dict)
        or any(ci["jobs"].get(key) != "success" for key in (
            "test", "staging_migrate", "staging_worker_verify"
        ))
    ):
        raise RuntimeError("CI health snapshot does not match the successful current Staging run")
    _timestamp(ci.get("recorded_at"))

    gate = data.get("kill_switch")
    if not isinstance(gate, dict):
        raise RuntimeError("Kill-switch snapshot is not available through the mobile API")
    if (
        gate.get("available") is not True or gate.get("status") != "engaged"
        or gate.get("freshness") != "fresh"
        or gate.get("scope") != "scheduled_task_runner_default"
        or gate.get("policy_mode") != "safe_dry_run"
        or gate.get("live_inference_default") != "disabled"
        or gate.get("allow_paid_model_calls") is not False
        or gate.get("paid_fallback_enabled") is not False
        or gate.get("allow_external_writes") is not False
        or gate.get("source_sha") != expected_sha
    ):
        raise RuntimeError("Kill-switch snapshot does not confirm the safe default policy")
    _timestamp(gate.get("recorded_at"))

    unavailable = data.get("unavailable_metrics")
    if isinstance(unavailable, dict) and ("ci_health" in unavailable or "kill_switch" in unavailable):
        raise RuntimeError("Mobile API still marks a verified snapshot unavailable")

    execution_modes = data.get("execution_modes")
    if not isinstance(execution_modes, dict) or execution_modes.get("available") is not True:
        raise RuntimeError("Task execution mode migration is not available through the Staging mobile API")
    totals = execution_modes.get("counts")
    status_counts = execution_modes.get("status_counts")
    expected_modes = ("not_started", "dry_run", "live_inference", "unknown")
    expected_statuses = ("pending", "running", "succeeded", "failed", "blocked")
    if not isinstance(totals, dict) or not isinstance(status_counts, dict):
        raise RuntimeError("Task execution mode aggregate is invalid")
    for mode in expected_modes:
        if type(totals.get(mode)) is not int or totals[mode] < 0:
            raise RuntimeError("Task execution mode totals are incomplete or invalid")
        per_status = status_counts.get(mode)
        if not isinstance(per_status, dict) or any(
            type(per_status.get(status)) is not int or per_status[status] < 0
            for status in expected_statuses
        ):
            raise RuntimeError("Task execution mode status aggregate is incomplete or invalid")
        if sum(per_status[status] for status in expected_statuses) != totals[mode]:
            raise RuntimeError("Task execution mode total does not match status counts")

    return {
        "status": "verified",
        "run_id": int(expected_run_id),
        "head_sha": expected_sha,
        "ci_health": ci.get("status"),
        "kill_switch": gate.get("status"),
        "paid_model_calls": False,
        "paid_fallback": False,
        "external_writes": False,
        "task_execution_modes": "available",
        "dry_run_succeeded": status_counts["dry_run"]["succeeded"],
        "live_inference_succeeded": status_counts["live_inference"]["succeeded"],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True))
    except Exception as exc:
        raise SystemExit(str(exc)) from None
