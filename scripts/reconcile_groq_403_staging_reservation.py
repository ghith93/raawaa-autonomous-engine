"""Reconcile the single known Staging Groq 403 reservation without replay.

Pinned to one exact task/model/route. Never contacts a model provider or production.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

BASE_URL = os.environ.get(
    "RAAWAA_ENGINE_API_URL",
    "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
).rstrip("/")
TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN", "") or os.environ.get(
    "RAAWAA_ENGINE_API_TOKEN", ""
)
ADMIN_TOKEN = os.environ.get("RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN", "")
TASK_ID = "16e1b4a6-a5ec-43cf-8168-f4723e1827c3"
PROVIDER = "groq"
MODEL = "openai/gpt-oss-120b"
ROUTE_ID = "groq:primary:openai/gpt-oss-120b"


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    auth_token = ADMIN_TOKEN if method == "POST" and path == "/v1/quota" else TOKEN
    if not auth_token:
        raise RuntimeError("Required Staging API token scope is unavailable")
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + auth_token,
        "User-Agent": "RaawaaAutonomousEngine/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, raw = response.status, response.read(64 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(64 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Staging API transport failed: " + type(exc).__name__) from None
    if len(raw) > 64 * 1024:
        raise RuntimeError("Staging API response exceeded the bounded size")
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Staging API returned invalid JSON") from None
    if not isinstance(body, dict):
        raise RuntimeError("Staging API returned an invalid response")
    return status, body


def api_readonly(path: str, timeout_seconds: int = 90) -> tuple[int, dict]:
    """Retry only safe GETs while a newly installed Worker secret propagates."""
    deadline = time.monotonic() + timeout_seconds
    last = (0, {})
    while True:
        last = api("GET", path)
        status, _ = last
        if status == 200 or status not in (401, 500, 502, 503, 504):
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(2)


def safe_error_code(body: dict) -> str:
    value = body.get("error")
    return value if isinstance(value, str) and re.fullmatch(r"[a-z0-9_]{1,80}", value) else "unknown"


def quarantine_route(payload: dict, timeout_seconds: int = 90) -> dict:
    """Retry the idempotent cooldown while a freshly installed admin secret propagates."""
    deadline = time.monotonic() + timeout_seconds
    retryable_statuses = {401, 403, 500, 502, 503, 504}
    while True:
        status, response = api("POST", "/v1/quota", payload)
        if status == 200 and response.get("ok") is True:
            return response
        error_code = safe_error_code(response)
        if status not in retryable_statuses:
            raise RuntimeError(
                f"Route quarantine failed (HTTP {status}; error={error_code})"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Route quarantine capability did not become ready (HTTP {status}; error={error_code})"
            )
        time.sleep(min(2, remaining))


def run() -> None:
    parsed = urlsplit(BASE_URL)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
        or parsed.username or parsed.password or parsed.port
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise RuntimeError("Refusing reconciliation outside the exact Staging Worker")
    if not TOKEN:
        raise RuntimeError("Staging API token is unavailable")

    status, ledger = api_readonly("/v1/monetary-ledger?task_id=" + quote(TASK_ID, safe=""))
    if status != 200 or not isinstance(ledger.get("snapshot"), dict):
        raise RuntimeError("Could not read the exact Staging monetary ledger record")
    matches = [
        row for row in ledger["snapshot"].get("unresolved", [])
        if row.get("task_id") == TASK_ID and row.get("provider") == PROVIDER
        and row.get("model") == MODEL and row.get("status") == "ambiguous"
    ]
    if len(matches) > 1:
        raise RuntimeError("Multiple ambiguous reservations match the exact Groq Staging task; refusing mutation")
    reservation_id = matches[0].get("reservation_id") if matches else None
    if matches and (not isinstance(reservation_id, str) or not reservation_id):
        raise RuntimeError("Matching reservation omitted its durable ID")
    if not matches and int(ledger["snapshot"].get("task_pending_count", -1)) != 0:
        raise RuntimeError("The exact task has unresolved state but no matching ambiguous Groq reservation; refusing mutation")

    status, quota = api_readonly(
        "/v1/quota?route_id=" + quote(ROUTE_ID, safe="") + "&task_id=" + quote(TASK_ID, safe=""),
    )
    if status != 200:
        raise RuntimeError("Could not verify the matching route audit")
    audit = quota.get("audit", [])
    confirmed_403 = any(
        row.get("event") == "provider_error"
        and isinstance(row.get("details"), dict)
        and row["details"].get("http_status") == 403
        for row in audit if isinstance(row, dict)
    )
    stopped = any(
        row.get("event") == "provider_failure_stopped"
        and isinstance(row.get("details"), dict)
        and row["details"].get("http_status") == 403
        for row in audit if isinstance(row, dict)
    )
    if not (confirmed_403 and stopped):
        raise RuntimeError("The exact task does not have the required recorded HTTP 403 evidence")

    if reservation_id:
        status, settled = api("POST", "/v1/monetary-ledger", {
            "action": "settle",
            "reservation_id": reservation_id,
            "actual_cost_nanos": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        })
        if status != 200 or settled.get("settled") is not True:
            raise RuntimeError("Could not reconcile the verified rejected request at zero spend")

    cooled = quarantine_route({
        "action": "cool_down",
        "route_id": ROUTE_ID,
        "seconds": 86400,
        "reason": "manual_reconcile_http_403",
    })
    status, persisted_quota = api_readonly(
        "/v1/quota?route_id=" + quote(ROUTE_ID, safe="") + "&task_id=" + quote(TASK_ID, safe=""),
    )
    persisted_cooldown = persisted_quota.get("cooldown") if status == 200 else None
    if (
        not isinstance(persisted_cooldown, dict)
        or not isinstance(persisted_cooldown.get("until_at"), str)
        or persisted_cooldown.get("until_at") != cooled.get("until_at")
    ):
        raise RuntimeError("Route quarantine was not durably confirmed in Staging D1")

    status, after = api_readonly("/v1/monetary-ledger?task_id=" + quote(TASK_ID, safe=""))
    if status != 200 or int(after.get("snapshot", {}).get("task_pending_count", -1)) != 0:
        raise RuntimeError("Reconciliation did not clear the exact task's unresolved reservation")
    print(json.dumps({
        "status": "reconciled" if reservation_id else "already_settled_route_quarantined",
        "provider": PROVIDER,
        "model": MODEL,
        "http_status": 403,
        "actual_cost_usd": 0,
        "route_quarantined_seconds": 86400,
        "provider_calls": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        raise SystemExit(str(exc)) from None
