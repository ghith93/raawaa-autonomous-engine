"""Remote integration verification for the isolated Staging monetary ledger.

This script never calls a model provider. It uses only the staging control
Worker's authenticated ledger and mobile-status endpoints.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
import time
from urllib.parse import quote
from typing import Any


BASE_URL = os.environ.get(
    "RAAWAA_ENGINE_STAGING_URL",
    "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
).rstrip("/")
TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN", "")
PREFIX = "staging-verification-"


def api(method: str, path: str, payload: dict[str, Any] | None = None,
        authenticated: bool = True) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "User-Agent": "RaawaaStagingLedgerVerification/1.0"}
    if authenticated:
        if not TOKEN:
            raise RuntimeError("Staging API token is unavailable")
        headers["Authorization"] = "Bearer " + TOKEN
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Staging Worker transport failed: " + type(exc).__name__) from None
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(f"Staging Worker returned non-JSON HTTP {status}") from None
    if not isinstance(body, dict):
        raise RuntimeError(f"Staging Worker returned an invalid JSON object (HTTP {status})")
    return status, body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def wait_for_worker(timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        try:
            last_status, _ = api("GET", "/health", authenticated=False)
            if last_status == 200:
                return
        except RuntimeError:
            pass
        time.sleep(2)
    raise RuntimeError(f"Staging Worker did not become healthy (last HTTP status: {last_status})")


def snapshot(task_id: str | None = None) -> dict[str, Any]:
    suffix = "" if task_id is None else "?task_id=" + quote(task_id, safe="")
    status, body = api("GET", "/v1/monetary-ledger" + suffix)
    require(status == 200 and isinstance(body.get("snapshot"), dict),
            f"Monetary status failed (HTTP {status}, error={body.get('error')})")
    return body["snapshot"]


def reserve(provider: str, model: str, task_id: str, estimate: int,
            daily: int, monthly: int, per_task: int,
            idempotency_key: str | None = None,
            reservation_id: str | None = None) -> tuple[int, dict[str, Any], str]:
    rid = reservation_id or str(uuid.uuid4())
    body = {
        "action": "reserve",
        "reservation_id": rid,
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "provider": provider,
        "model": model,
        "task_kind": "staging_verification",
        "task_id": task_id,
        "estimated_cost_nanos": estimate,
        "daily_budget_nanos": daily,
        "monthly_budget_nanos": monthly,
        "per_task_budget_nanos": per_task,
    }
    status, response = api("POST", "/v1/monetary-ledger", body)
    return status, response, rid


def action(name: str, reservation_id: str, **extra) -> tuple[int, dict[str, Any]]:
    return api("POST", "/v1/monetary-ledger", {
        "action": name, "reservation_id": reservation_id, **extra
    })


def cleanup_test_reservations() -> None:
    current = snapshot()
    for row in current.get("unresolved", []):
        provider = row.get("provider", "")
        if not isinstance(provider, str) or not provider.startswith(PREFIX):
            continue
        rid = row.get("reservation_id")
        state = row.get("status")
        if state == "reserved":
            status, _ = action("release", rid)
            require(status == 200, "Could not release a pre-I/O staging test reservation")
        elif state in ("in_flight", "ambiguous"):
            # This verification harness never calls a provider; zero-cost
            # settlement safely clears its own test-only ambiguous reservations.
            status, _ = action("settle", rid, actual_cost_nanos=0, input_tokens=0, output_tokens=0)
            require(status == 200, "Could not settle a test-only ambiguous reservation")


def cap_test(label: str, run_tag: str, cap_kind: str) -> None:
    estimate = 1_000_000  # $0.001 reservation; no provider request is made.
    current = snapshot()
    daily = int(current["daily_total_nanos"]) + 100 * estimate
    monthly = int(current["monthly_total_nanos"]) + 100 * estimate
    task_cap = estimate
    if cap_kind == "daily":
        daily = int(current["daily_total_nanos"]) + 2 * estimate
        monthly = int(current["monthly_total_nanos"]) + 100 * estimate
    elif cap_kind == "monthly":
        daily = int(current["daily_total_nanos"]) + 100 * estimate
        monthly = int(current["monthly_total_nanos"]) + 2 * estimate
    elif cap_kind == "task":
        task_cap = 2 * estimate
    else:
        raise ValueError("unknown cap kind")

    def attempt(index: int):
        task_id = f"{PREFIX}{run_tag}-{label}-task" if cap_kind == "task" else f"{PREFIX}{run_tag}-{label}-{index}"
        return reserve(
            f"{PREFIX}{run_tag}-{label}-{index}",
            f"model-{index}",
            task_id,
            estimate, daily, monthly, task_cap,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, range(3)))
    statuses = sorted(item[0] for item in results)
    require(statuses == [201, 201, 409],
            f"{label} cap did not admit exactly two of three concurrent reservations: {statuses}")
    for status, body, rid in results:
        if status == 201:
            released, _ = action("release", rid)
            require(released == 200, f"{label} test reservation was not releasable before provider I/O")
        else:
            require(body.get("error") == "budget_exceeded",
                    f"{label} cap returned an unexpected rejection")


def run() -> None:
    require(BASE_URL.startswith("https://"), "Staging Worker URL must use HTTPS")
    require(bool(TOKEN), "RAAWAA_ENGINE_STAGING_API_TOKEN is required")
    wait_for_worker()
    health, _ = api("GET", "/health", authenticated=False)
    require(health == 200, f"Staging Worker health returned HTTP {health}")
    unauth, _ = api("GET", "/v1/monetary-ledger", authenticated=False)
    require(unauth == 401, f"Monetary ledger must require authentication (HTTP {unauth})")

    initial = snapshot()
    require(isinstance(initial.get("daily_total_nanos"), int), "D1 monetary totals are missing")
    run_tag = str(uuid.uuid4())
    cleanup_test_reservations()

    cap_test("daily", run_tag, "daily")
    cap_test("monthly", run_tag, "monthly")
    cap_test("task", run_tag, "task")

    # Idempotent reservation replay returns the first durable reservation.
    current = snapshot()
    estimate = 1_000
    provider = PREFIX + run_tag + "-idempotency"
    task = PREFIX + run_tag + "-idempotency-task"
    idem = str(uuid.uuid4())
    first_id = str(uuid.uuid4())
    first_status, first_body, _ = reserve(
        provider, "model", task, estimate,
        int(current["daily_total_nanos"]) + 1_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000,
        1_000_000, idem, first_id,
    )
    require(first_status == 201, "Initial idempotency reservation failed")
    replay_status, replay_body, _ = reserve(
        provider, "model", task, estimate,
        int(current["daily_total_nanos"]) + 1_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000,
        1_000_000, idem, str(uuid.uuid4()),
    )
    require(replay_status == 200 and replay_body.get("replay") is True and
            replay_body.get("reservation_id") == first_id,
            "Duplicate idempotency key did not replay the original reservation")
    released, _ = action("release", first_id)
    require(released == 200, "Idempotency test reservation could not be released")

    # Once provider I/O is marked started, release is forbidden. An ambiguous
    # outcome remains pending until an explicit zero-cost settlement.
    current = snapshot()
    lifecycle_provider = PREFIX + run_tag + "-lifecycle"
    lifecycle_task = PREFIX + run_tag + "-lifecycle-task"
    lifecycle_status, _, lifecycle_id = reserve(
        lifecycle_provider, "model", lifecycle_task, 1,
        int(current["daily_total_nanos"]) + 1_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000, 1_000_000,
    )
    require(lifecycle_status == 201, "Lifecycle reservation failed")
    started, _ = action("start", lifecycle_id)
    require(started == 200, "Could not mark reservation in flight")
    forbidden, _ = action("release", lifecycle_id)
    require(forbidden == 409, "Release after provider I/O was not rejected")
    ambiguous, _ = action("ambiguous", lifecycle_id)
    require(ambiguous == 200, "Ambiguous provider state was not persisted")
    settled, _ = action("settle", lifecycle_id, actual_cost_nanos=0, input_tokens=0, output_tokens=0)
    require(settled == 200, "Ambiguous reservation could not be settled")
    replay, replay_body = action("settle", lifecycle_id, actual_cost_nanos=0, input_tokens=0, output_tokens=0)
    require(replay == 200 and replay_body.get("replay") is True,
            "Duplicate settlement was not idempotent")

    # Actual spend above estimate is retained and quarantines the route.
    current = snapshot()
    quarantine_provider = PREFIX + run_tag + "-quarantine"
    quarantine_model = "model"
    quarantine_task = PREFIX + run_tag + "-quarantine-task"
    q_status, _, q_id = reserve(
        quarantine_provider, quarantine_model, quarantine_task, 1,
        int(current["daily_total_nanos"]) + 1_000_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000_000,
        1_000_000_000,
    )
    require(q_status == 201, "Over-estimate quarantine reservation failed")
    started, _ = action("start", q_id)
    require(started == 200, "Could not start quarantine test reservation")
    settled, body = action("settle", q_id, actual_cost_nanos=2, input_tokens=1, output_tokens=1)
    require(settled == 200 and body.get("over_budget") is True,
            "Actual cost above estimate was not recorded and flagged")
    blocked, body, _ = reserve(
        quarantine_provider, quarantine_model, quarantine_task, 1,
        int(current["daily_total_nanos"]) + 1_000_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000_000,
        1_000_000_000,
    )
    require(blocked == 409 and body.get("error") == "budget_exceeded",
            "Quarantined provider/model route accepted another reservation")

    # Leave one ambiguous reservation, redeploy the Worker, and verify that
    # the durable D1 record survives the Worker restart/deployment.
    current = snapshot()
    crash_provider = PREFIX + run_tag + "-restart"
    crash_task = PREFIX + run_tag + "-restart-task"
    crash_status, _, crash_id = reserve(
        crash_provider, "model", crash_task, 3,
        int(current["daily_total_nanos"]) + 1_000_000_000,
        int(current["monthly_total_nanos"]) + 1_000_000_000,
        1_000_000_000,
    )
    require(crash_status == 201, "Restart persistence reservation failed")
    started, _ = action("start", crash_id)
    require(started == 200, "Could not start restart persistence reservation")
    ambiguous, _ = action("ambiguous", crash_id)
    require(ambiguous == 200, "Could not persist restart-test ambiguous state")

    deploy = subprocess.run(
        ["npx", "--yes", "wrangler@4.156.1", "deploy", "--config", "wrangler.staging.toml"],
        check=False, capture_output=True, text=True, timeout=180,
    )
    if deploy.returncode != 0:
        raise RuntimeError("Staging Worker redeploy failed: " + (deploy.stderr or deploy.stdout)[-1200:])
    wait_for_worker()

    current = snapshot(crash_task)
    unresolved_ids = {item.get("reservation_id") for item in current.get("unresolved", [])}
    require(crash_id in unresolved_ids, "Ambiguous reservation did not survive Staging Worker redeploy")
    settled, _ = action("settle", crash_id, actual_cost_nanos=0, input_tokens=0, output_tokens=0)
    require(settled == 200, "Could not clean up restart-test reservation")

    mobile_status, mobile = api("GET", "/v1/mobile/status")
    require(mobile_status == 200, f"Authenticated mobile status returned HTTP {mobile_status}")
    require(mobile.get("monetary_ledger", {}).get("available") is True,
            "Mobile status does not report the D1 monetary ledger as available")
    require("model_costs_usd" in mobile, "Mobile status omitted the model cost breakdown")

    cleanup_test_reservations()
    print("Staging monetary ledger verification passed: global daily/monthly/task concurrency, idempotency, pre-I/O release, ambiguous settlement, over-estimate quarantine, Worker redeploy persistence, and authenticated mobile status. No model provider was called.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        try:
            cleanup_test_reservations()
        except Exception:
            pass
        raise SystemExit(str(exc)) from None
