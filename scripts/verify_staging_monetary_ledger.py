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
import tomllib
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from typing import Any

from engine.process_supervisor import ProcessSupervisor, default_registry
from engine.remote_process_dispatch_ledger import RemoteProcessDispatchLedger
from engine.remote_task_store import RemoteTaskStore


BASE_URL = os.environ.get(
    "RAAWAA_ENGINE_STAGING_URL",
    "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
).rstrip("/")
TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN", "")
QUOTA_ADMIN_TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_QUOTA_ADMIN_TOKEN", "")
PREFIX = "staging-verification-"

STAGING_D1_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
PRODUCTION_D1_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"


def verify_staging_target() -> None:
    with Path("wrangler.staging.toml").open("rb") as stream:
        staging = tomllib.load(stream)
    with Path("wrangler.toml").open("rb") as stream:
        production = tomllib.load(stream)
    staging_bindings = staging.get("d1_databases", [])
    production_bindings = production.get("d1_databases", [])
    require(staging.get("name") == "raawaa-autonomous-engine-staging",
            "Refusing Staging policy update: Worker config name mismatch")
    require(len(staging_bindings) == 1 and len(production_bindings) == 1,
            "Refusing Staging policy update: expected exactly one D1 binding per config")
    require(staging_bindings[0].get("database_name") == "raawaa-engine-staging" and
            staging_bindings[0].get("database_id") == STAGING_D1_ID,
            "Refusing Staging policy update: staging D1 identity mismatch")
    require(production_bindings[0].get("database_id") == PRODUCTION_D1_ID and
            staging_bindings[0].get("database_id") != production_bindings[0].get("database_id"),
            "Refusing Staging policy update: production/staging separation mismatch")


def default_policy_nanos() -> tuple[int, int, int]:
    policy = json.loads(Path("config/policy.json").read_text(encoding="utf-8"))
    def nanos(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError("Configured monetary budget is not numeric")
        amount = Decimal(str(value)) * Decimal(1_000_000_000)
        if amount != amount.to_integral_value() or amount < 0:
            raise RuntimeError("Configured monetary budget cannot be represented as integer nano-USD")
        return int(amount)
    return (
        nanos(policy["daily_budget_usd"]),
        nanos(policy["monthly_budget_usd"]),
        nanos(policy["per_task_budget_usd"]),
    )



def execute_staging_sql(sql: str, *, retry_safe: bool = False) -> None:
    """Execute a Staging-only statement; retry 7429 only when the caller proves idempotency."""
    verify_staging_target()
    if not os.environ.get("CLOUDFLARE_API_TOKEN") or not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        raise RuntimeError("Cloudflare credentials are required for Staging-only test cleanup")
    for attempt in range(3):
        result = subprocess.run(
            [
                "npx", "--yes", "wrangler@4.156.1", "d1", "execute",
                "raawaa-engine-staging", "--remote", "--command", sql,
                "--config", "wrangler.staging.toml", "--yes",
            ],
            check=False, capture_output=True, text=True, timeout=60,
            env=os.environ.copy(),
        )
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "unknown Wrangler error")[-1200:]
        transient_api_error = "[code: 7429]" in detail.lower()
        if not (retry_safe and transient_api_error and attempt < 2):
            raise RuntimeError("Staging-only test cleanup failed: " + detail)
        # D1 write retries are opt-in at each call site and limited to
        # idempotent fixture INSERT OR IGNORE / DELETE statements only.
        time.sleep(2 ** (attempt + 1))



def set_staging_policy(daily: int, monthly: int, per_task: int) -> None:
    verify_staging_target()
    if any(type(value) is not int or value < 0 for value in (daily, monthly, per_task)):
        raise RuntimeError("Refusing invalid Staging monetary policy values")
    if not os.environ.get("CLOUDFLARE_API_TOKEN") or not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        raise RuntimeError("Cloudflare credentials are required for Staging-only policy setup")
    sql = (
        "UPDATE monetary_budget_policy SET "
        f"daily_budget_nanos={daily},monthly_budget_nanos={monthly},"
        f"per_task_budget_nanos={per_task},updated_at=CURRENT_TIMESTAMP "
        "WHERE policy_key='default'"
    )
    result = subprocess.run(
        [
            "npx", "--yes", "wrangler@4.156.1", "d1", "execute",
            "raawaa-engine-staging", "--remote", "--command", sql,
            "--config", "wrangler.staging.toml", "--yes",
        ],
        check=False, capture_output=True, text=True, timeout=60,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown Wrangler error")[-1200:]
        raise RuntimeError("Staging-only monetary policy update failed: " + detail)



def api(method: str, path: str, payload: dict[str, Any] | None = None,
        authenticated: bool = True, readiness_timeout_seconds: int = 90,
        admin: bool = False) -> tuple[int, dict[str, Any]]:
    """Call the Staging Worker, tolerating bounded propagation of its fresh secret.

    The Worker checks bearer authentication before dispatching any authenticated
    route. Therefore a 401 response proves that no GET or POST handler ran, and
    retrying that exact request is safe while Cloudflare propagates a new secret
    across edge isolates. No other 4xx response is retried.
    """
    if type(readiness_timeout_seconds) is not int or not 1 <= readiness_timeout_seconds <= 180:
        raise ValueError("Staging readiness timeout must be between 1 and 180 seconds")
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "User-Agent": "RaawaaStagingLedgerVerification/1.0"}
    if authenticated:
        selected_token = QUOTA_ADMIN_TOKEN if admin else TOKEN
        if not selected_token:
            raise RuntimeError("Staging API token is unavailable")
        headers["Authorization"] = "Bearer " + selected_token
    if data is not None:
        headers["Content-Type"] = "application/json"

    deadline = time.monotonic() + readiness_timeout_seconds if authenticated else None
    while True:
        request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            if authenticated and status == 401 and time.monotonic() < deadline:
                time.sleep(2)
                continue
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


def wait_for_worker(timeout_seconds: int = 90) -> None:
    """Wait for both Worker readiness and propagation of its ephemeral API secret.

    A healthy public /health response does not prove that the just-installed
    ENGINE_API_TOKEN is already visible to authenticated routes. Retry only the
    read-only monetary status request; never mutate D1 while auth is unsettled.
    """
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    last_error = "worker_not_ready"
    while time.monotonic() < deadline:
        try:
            health_status, _ = api("GET", "/health", authenticated=False)
            last_status = health_status
            if health_status == 200:
                auth_status, body = api("GET", "/v1/monetary-ledger", authenticated=True)
                last_status = auth_status
                last_error = str(body.get("error", ""))[:80]
                if (auth_status == 200 and isinstance(body.get("snapshot"), dict)
                        and isinstance(body.get("budget_policy"), dict)
                        and body["budget_policy"].get("enabled") is True):
                    return
                # 401 can be a short secret-propagation window immediately after
                # Wrangler reports a successful secret upload. 5xx may be startup
                # propagation. Other 4xx responses indicate a configuration error.
                if auth_status not in (401, 500, 502, 503, 504):
                    raise RuntimeError(
                        f"Staging Worker authenticated readiness failed (HTTP {auth_status}, error={last_error})"
                    )
        except RuntimeError as exc:
            message = str(exc)
            if message.startswith("Staging Worker authenticated readiness failed"):
                raise
            last_error = type(exc).__name__
        time.sleep(2)
    raise RuntimeError(
        f"Staging Worker did not become authenticated within {timeout_seconds}s "
        f"(last HTTP status: {last_status}, error={last_error})"
    )


def monetary_status(task_id: str | None = None) -> dict[str, Any]:
    suffix = "" if task_id is None else "?task_id=" + quote(task_id, safe="")
    status, body = api("GET", "/v1/monetary-ledger" + suffix)
    require(status == 200 and isinstance(body.get("snapshot"), dict),
            f"Monetary status failed (HTTP {status}, error={body.get('error')})")
    require(isinstance(body.get("budget_policy"), dict) and body["budget_policy"].get("enabled") is True,
            "Operator-owned D1 monetary budget policy is unavailable or disabled")
    return body


def snapshot(task_id: str | None = None) -> dict[str, Any]:
    return monetary_status(task_id)["snapshot"]


def budget_policy() -> dict[str, Any]:
    return monetary_status()["budget_policy"]


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

    # Remove only rows created by this CI harness. These tests never call a
    # model provider, so deleting their settled/released audit rows is safe.
    prefix = PREFIX.replace("'", "''")
    for statement in (
        f"DELETE FROM monetary_route_quarantines WHERE route_id LIKE '{prefix}%'",
        f"DELETE FROM monetary_reservations WHERE provider LIKE '{prefix}%'",
        f"DELETE FROM quota_usage WHERE route_id LIKE '{prefix}%'",
        f"DELETE FROM quota_cooldowns WHERE route_id LIKE '{prefix}%'",
        f"DELETE FROM quota_audit_events WHERE route_id LIKE '{prefix}%'",
        f"DELETE FROM audit_log WHERE task_id LIKE '{prefix}%'",
    ):
        execute_staging_sql(statement, retry_safe=True)


def verify_token_quota_concurrency(run_tag: str) -> None:
    route_id = f"{PREFIX}{run_tag}-token-quota"
    task_id = f"{PREFIX}{run_tag}-token-task"

    def attempt(_index: int):
        status, body = api("POST", "/v1/quota", {
            "action": "reserve",
            "route_id": route_id,
            "task_id": task_id,
            "estimated_tokens": 10,
        })
        return status, body

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, range(3)))
    require(sorted(item[0] for item in results) == [201, 201, 409],
            "D1 token quota did not enforce concurrent request/token caps")
    for status, body in results:
        if status == 201:
            reservation_id = int(body["reservation_id"])
            settled, _ = api("POST", "/v1/quota", {
                "action": "settle", "reservation_id": reservation_id, "route_id": route_id,
                "task_id": task_id, "actual_tokens": 10,
            })
            require(settled == 200, "D1 token quota reservation did not settle")
            replay, replay_body = api("POST", "/v1/quota", {
                "action": "settle", "reservation_id": reservation_id, "route_id": route_id,
                "task_id": task_id, "actual_tokens": 10,
            })
            require(replay == 200 and replay_body.get("replay") is True,
                    "D1 token quota settlement replay was not idempotent")
        else:
            require(body.get("error") == "quota_unavailable",
                    "D1 token quota returned an unexpected concurrent rejection")
    from urllib.parse import quote
    query = f"/v1/quota?route_id={quote(route_id, safe='')}&task_id={quote(task_id, safe='')}"
    status, body = api("GET", query)
    usage = body.get("usage", {})
    require(status == 200 and usage.get("daily", {}).get("requests") == 2 and
            usage.get("daily", {}).get("tokens") == 20 and
            usage.get("task", {}).get("requests") == 2,
            "D1 token quota status does not match the two settled reservations")

    # Verify that the ordinary runner token cannot mutate administrative quota
    # state, while the isolated admin token can apply a bounded cooldown.
    ordinary_status, ordinary_body = api("POST", "/v1/quota", {
        "action": "cool_down", "route_id": route_id, "seconds": 30,
        "reason": "staging_scope_test",
    })
    require(ordinary_status == 403 and ordinary_body.get("error") == "forbidden_operation_scope",
            "Ordinary Staging token unexpectedly has quota-admin scope")
    admin_status, admin_body = api("POST", "/v1/quota", {
        "action": "cool_down", "route_id": route_id, "seconds": 30,
        "reason": "staging_scope_test",
    }, admin=True)
    require(admin_status == 200 and isinstance(admin_body.get("until_at"), str),
            "Dedicated Staging quota-admin token could not apply a bounded cooldown")


def cap_test(label: str, run_tag: str, cap_kind: str) -> None:
    current = snapshot()
    policy = budget_policy()
    original_policy = (
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
    )
    daily, monthly, task_cap = original_policy
    policy_override = None
    if cap_kind == "daily":
        remaining = daily - int(current["daily_total_nanos"])
        estimate = min(task_cap, remaining // 2)
        require(estimate > 0, "Staging daily monetary cap has no room for a safe concurrency test")
    elif cap_kind == "monthly":
        remaining = monthly - int(current["monthly_total_nanos"])
        estimate = remaining // 2
        require(estimate > 0, "Staging monthly monetary cap has no room for a safe concurrency test")
        # Raise only the temporary Staging daily ceiling so the monthly ceiling
        # is the limiting dimension; restore the exact original policy in finally.
        daily = int(current["daily_total_nanos"]) + 100 * estimate
        monthly = int(current["monthly_total_nanos"]) + 2 * estimate
        task_cap = estimate
        policy_override = (daily, monthly, task_cap)
    elif cap_kind == "task":
        estimate = task_cap // 2
        require(estimate > 0, "Staging per-task monetary cap is too small for a concurrency test")
    else:
        raise ValueError("unknown cap kind")

    try:
        if policy_override is not None:
            set_staging_policy(*policy_override)

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
    finally:
        if policy_override is not None:
            set_staging_policy(*original_policy)


def verify_operator_policy(run_tag: str) -> None:
    policy = budget_policy()
    status, body, _ = reserve(
        f"{PREFIX}{run_tag}-policy-mismatch",
        "model",
        f"{PREFIX}{run_tag}-policy-task",
        1,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]) + 1,
        int(policy["per_task_budget_nanos"]),
    )
    require(status == 409 and body.get("error") == "budget_policy_mismatch",
            "Caller was able to change an operator-owned D1 monetary hard cap")



def verify_task_invocation_lifecycle(run_tag: str) -> None:
    """Exercise the durable invocation endpoint in isolated Staging without provider I/O."""
    task_id = str(uuid.uuid4())
    lease_token = str(uuid.uuid4())
    replacement_lease = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600))
    idempotency_key = PREFIX + run_tag + "-invocation-" + task_id
    sql_values = [task_id, idempotency_key, lease_token, now, expires]
    for value in sql_values:
        require("'" not in value, "Generated Staging invocation fixture contained an unsafe SQL character")
    execute_staging_sql(
        "INSERT OR IGNORE INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,"
        "created_at,updated_at,lease_token,lease_expires_at) VALUES("
        f"'{task_id}','{idempotency_key}','system_audit','Invocation lifecycle verification','{{}}',"
        f"'running',1,2,'{now}','{now}','{lease_token}','{expires}')",
        retry_safe=True,
    )
    base_path = f"/v1/tasks/{task_id}/invocations"
    invocation_ids: list[str] = []
    try:
        prepare = {
            "action": "prepare", "lease_token": lease_token,
            "step_key": "research:primary", "task_kind": "research",
        }
        status, body = api("POST", base_path, prepare)
        require(status == 201 and isinstance(body.get("invocation"), dict),
                f"Staging invocation prepare failed (HTTP {status}, error={body.get('error')})")
        primary = body["invocation"]
        invocation_ids.append(primary["invocation_id"])
        require(primary.get("status") == "prepared" and isinstance(primary.get("request_id"), str),
                "Staging prepare omitted the durable request identity")
        status, replay = api("POST", base_path, prepare)
        require(status == 201 and replay.get("invocation", {}).get("invocation_id") == primary["invocation_id"] and
                replay.get("invocation", {}).get("request_id") == primary["request_id"],
                "Staging prepare replay did not return the original logical invocation identity")

        review_prepare = {**prepare, "step_key": "research:review"}
        status, review_body = api("POST", base_path, review_prepare)
        require(status == 201 and isinstance(review_body.get("invocation"), dict),
                f"Staging second step prepare failed (HTTP {status})")
        review = review_body["invocation"]
        invocation_ids.append(review["invocation_id"])
        require(review["invocation_id"] != primary["invocation_id"] and
                review["request_id"] != primary["request_id"],
                "Distinct task steps collided on their durable invocation identity")

        started, body = api("POST", base_path, {
            "action": "start", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"], "route_id": "staging-test:route-a",
        })
        require(started == 200, "Staging invocation start failed")
        duplicate_start, _ = api("POST", base_path, {
            "action": "start", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"], "route_id": "staging-test:route-a",
        })
        require(duplicate_start == 409, "Staging invocation allowed a duplicate in-flight route attempt")

        rejected, _ = api("POST", base_path, {
            "action": "reject", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"], "route_id": "staging-test:route-a",
            "rejection_code": "http_429",
        })
        require(rejected == 200, "Staging invocation did not accept an explicit route rejection")
        reject_replay, reject_body = api("POST", base_path, {
            "action": "reject", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"], "route_id": "staging-test:route-a",
            "rejection_code": "http_429",
        })
        require(reject_replay == 200 and reject_body.get("replay") is True,
                "Staging invocation reject transition was not idempotent after a lost response")

        alternate, _ = api("POST", base_path, {
            "action": "start", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"], "route_id": "staging-test:route-b",
        })
        require(alternate == 200, "Staging invocation did not permit a new route after explicit 429")
        settled, _ = api("POST", base_path, {
            "action": "settle", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"],
        })
        require(settled == 200, "Staging invocation settlement failed")
        settle_replay, settle_body = api("POST", base_path, {
            "action": "settle", "lease_token": lease_token,
            "invocation_id": primary["invocation_id"],
        })
        require(settle_replay == 200 and settle_body.get("replay") is True,
                "Staging invocation settlement replay was not idempotent")
        settled_prepare, settled_body = api("POST", base_path, prepare)
        require(settled_prepare == 409 and settled_body.get("error") == "invocation_replay_blocked",
                "Staging invocation allowed replay after settlement")

        ambiguous_start, _ = api("POST", base_path, {
            "action": "start", "lease_token": lease_token,
            "invocation_id": review["invocation_id"], "route_id": "staging-test:route-a",
        })
        require(ambiguous_start == 200, "Staging second-step invocation start failed")
        ambiguous, _ = api("POST", base_path, {
            "action": "ambiguous", "lease_token": lease_token,
            "invocation_id": review["invocation_id"],
        })
        require(ambiguous == 200, "Staging invocation ambiguous state was not persisted")
        ambiguous_replay, ambiguous_body = api("POST", base_path, {
            "action": "ambiguous", "lease_token": lease_token,
            "invocation_id": review["invocation_id"],
        })
        require(ambiguous_replay == 200 and ambiguous_body.get("replay") is True,
                "Staging ambiguous transition was not idempotent after a lost response")
        blocked, blocked_body = api("POST", base_path, review_prepare)
        require(blocked == 409 and blocked_body.get("error") == "invocation_replay_blocked",
                "Staging invocation allowed replay after an ambiguous provider outcome")

        stale, stale_body = api("POST", base_path, {
            "action": "prepare", "lease_token": replacement_lease,
            "step_key": "research:stale-worker", "task_kind": "research",
        })
        require(stale == 409 and stale_body.get("error") == "lease_lost",
                "Staging invocation endpoint accepted a stale task lease")
    finally:
        invocation_id_list = ",".join("'" + value.replace("'", "''") + "'" for value in invocation_ids)
        if invocation_id_list:
            execute_staging_sql(
                "DELETE FROM task_model_invocation_attempts WHERE invocation_id IN (" + invocation_id_list + ")",
                retry_safe=True,
            )
            execute_staging_sql(
                "DELETE FROM task_model_invocations WHERE invocation_id IN (" + invocation_id_list + ")",
                retry_safe=True,
            )
        execute_staging_sql("DELETE FROM tasks WHERE id='" + task_id + "'", retry_safe=True)

def verify_process_dispatch_lifecycle(run_tag: str) -> None:
    """Run one fixed, read-only child against isolated Staging D1, then replay its result."""
    verify_staging_target()
    task_id = str(uuid.uuid4())
    lease_token = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600))
    idempotency_key = PREFIX + run_tag + "-process-" + task_id
    values = (task_id, idempotency_key, lease_token, now, expires)
    require(all("'" not in value for value in values),
            "Generated Staging process fixture contained an unsafe SQL character")
    execute_staging_sql(
        "INSERT OR IGNORE INTO tasks(id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,"
        "created_at,updated_at,lease_token,lease_expires_at) VALUES("
        f"'{task_id}','{idempotency_key}','supervised_probe','Process dispatch verification',"
        "'{\"operation\":\"python_platform_probe\"}','running',1,2,"
        f"'{now}','{now}','{lease_token}','{expires}')",
        retry_safe=True,
    )

    store = RemoteTaskStore(BASE_URL, TOKEN, timeout=30, reject_redirects=True)
    supervisor = ProcessSupervisor(
        default_registry(), RemoteProcessDispatchLedger(store)
    )
    fence = lambda: store.heartbeat(task_id, lease_token, lease_timeout_seconds=900)
    try:
        first = supervisor.run_operation(
            "python_platform_probe", {}, task_id=task_id, lease_token=lease_token,
            fence_before=fence, fence_after=fence,
        )
        require(first.classification == "succeeded" and first.exit_code == 0 and
                first.started is True and first.replayed is False and
                isinstance(first.dispatch_id, str) and bool(first.stdout.strip()),
                "Staging process dispatch did not persist a successful read-only child outcome")

        replay = supervisor.run_operation(
            "python_platform_probe", {}, task_id=task_id, lease_token=lease_token,
            fence_before=fence, fence_after=fence,
        )
        require(replay.classification == "succeeded" and replay.exit_code == 0 and
                replay.started is False and replay.replayed is True and
                replay.dispatch_id == first.dispatch_id and replay.stdout == first.stdout,
                "Staging process dispatch did not replay its settled result without starting another child")
        store.finish(task_id, "succeeded", result={
            "verification": "staging_process_dispatch_v1",
            "dispatch_id": first.dispatch_id,
            "classification": first.classification,
            "inference": "none",
            "provider_calls": 0,
        }, lease_token=lease_token)
    finally:
        task_sql = task_id.replace("'", "''")
        execute_staging_sql(
            f"DELETE FROM task_process_dispatches WHERE task_id='{task_sql}'",
            retry_safe=True,
        )
        execute_staging_sql(
            f"DELETE FROM audit_log WHERE task_id='{task_sql}'",
            retry_safe=True,
        )
        execute_staging_sql(
            f"DELETE FROM tasks WHERE id='{task_sql}'",
            retry_safe=True,
        )


def run() -> None:
    require(BASE_URL.startswith("https://"), "Staging Worker URL must use HTTPS")
    require(bool(TOKEN), "RAAWAA_ENGINE_STAGING_API_TOKEN is required")
    verify_staging_target()
    set_staging_policy(*default_policy_nanos())
    wait_for_worker()
    health, _ = api("GET", "/health", authenticated=False)
    require(health == 200, f"Staging Worker health returned HTTP {health}")
    unauth, _ = api("GET", "/v1/monetary-ledger", authenticated=False)
    require(unauth == 401, f"Monetary ledger must require authentication (HTTP {unauth})")
    unauth_quota, _ = api("GET", "/v1/quota", authenticated=False)
    require(unauth_quota == 401, f"Token quota ledger must require authentication (HTTP {unauth_quota})")

    initial = monetary_status()
    require(isinstance(initial["snapshot"].get("daily_total_nanos"), int), "D1 monetary totals are missing")
    policy = initial["budget_policy"]
    expected_policy = default_policy_nanos()
    actual_policy = (
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
    )
    require(actual_policy == expected_policy,
            "Staging D1 hard caps do not match the reviewed policy configuration")
    run_tag = str(uuid.uuid4())
    cleanup_test_reservations()
    verify_token_quota_concurrency(run_tag)
    verify_task_invocation_lifecycle(run_tag)
    verify_process_dispatch_lifecycle(run_tag)

    cap_test("daily", run_tag, "daily")
    cap_test("monthly", run_tag, "monthly")
    cap_test("task", run_tag, "task")
    verify_operator_policy(run_tag)

    # Idempotent reservation replay returns the first durable reservation.
    current = snapshot()
    policy = budget_policy()
    estimate = 1_000
    provider = PREFIX + run_tag + "-idempotency"
    task = PREFIX + run_tag + "-idempotency-task"
    idem = str(uuid.uuid4())
    first_id = str(uuid.uuid4())
    first_status, first_body, _ = reserve(
        provider, "model", task, estimate,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]), idem, first_id,
    )
    require(first_status == 201, "Initial idempotency reservation failed")
    replay_status, replay_body, _ = reserve(
        provider, "model", task, estimate,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]), idem, str(uuid.uuid4()),
    )
    require(replay_status == 200 and replay_body.get("replay") is True and
            replay_body.get("reservation_id") == first_id,
            "Duplicate idempotency key did not replay the original reservation")
    released, _ = action("release", first_id)
    require(released == 200, "Idempotency test reservation could not be released")

    # Once provider I/O is marked started, release is forbidden. An ambiguous
    # outcome remains pending until an explicit zero-cost settlement.
    current = snapshot()
    policy = budget_policy()
    lifecycle_provider = PREFIX + run_tag + "-lifecycle"
    lifecycle_task = PREFIX + run_tag + "-lifecycle-task"
    lifecycle_status, _, lifecycle_id = reserve(
        lifecycle_provider, "model", lifecycle_task, 1,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
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
    policy = budget_policy()
    quarantine_provider = PREFIX + run_tag + "-quarantine"
    quarantine_model = "model"
    quarantine_task = PREFIX + run_tag + "-quarantine-task"
    q_status, _, q_id = reserve(
        quarantine_provider, quarantine_model, quarantine_task, 1,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
    )
    require(q_status == 201, "Over-estimate quarantine reservation failed")
    started, _ = action("start", q_id)
    require(started == 200, "Could not start quarantine test reservation")
    settled, body = action("settle", q_id, actual_cost_nanos=2, input_tokens=1, output_tokens=1)
    require(settled == 200 and body.get("over_budget") is True,
            "Actual cost above estimate was not recorded and flagged")
    blocked, body, _ = reserve(
        quarantine_provider, quarantine_model, quarantine_task, 1,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
    )
    require(blocked == 409 and body.get("error") == "budget_exceeded",
            "Quarantined provider/model route accepted another reservation")

    # Leave one ambiguous reservation, redeploy the Worker, and verify that
    # the durable D1 record survives the Worker restart/deployment.
    current = snapshot()
    policy = budget_policy()
    crash_provider = PREFIX + run_tag + "-restart"
    crash_task = PREFIX + run_tag + "-restart-task"
    crash_status, _, crash_id = reserve(
        crash_provider, "model", crash_task, 3,
        int(policy["daily_budget_nanos"]),
        int(policy["monthly_budget_nanos"]),
        int(policy["per_task_budget_nanos"]),
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
    require(mobile["monetary_ledger"].get("budget_policy_enabled") is True and
            mobile["monetary_ledger"].get("daily_budget_usd") == int(policy["daily_budget_nanos"]) / 1_000_000_000 and
            mobile["monetary_ledger"].get("monthly_budget_usd") == int(policy["monthly_budget_nanos"]) / 1_000_000_000,
            "Mobile status does not expose the operator-owned monetary caps")
    require("model_costs_usd" in mobile, "Mobile status omitted the model cost breakdown")
    require(set(mobile.get("provider_usage", {}).keys()) == {"requests", "tokens"},
            "Mobile status exposed route-level provider usage details")
    require("by_provider" not in mobile.get("model_costs_usd", {}),
            "Mobile status exposed provider-level model cost details")
    require("unresolved" not in mobile.get("monetary_ledger", {}),
            "Mobile status exposed reservation metadata")
    require(mobile.get("unavailable_metrics", {}).get("provider_usage") == "Available",
            "Mobile status does not report the D1 token quota ledger as available")

    cleanup_test_reservations()
    print("Staging D1 verification passed: token quota concurrency/settlement, durable multi-step invocation identities, lease fencing, explicit 429 route rotation, blocked ambiguous/settled replay, global daily/monthly/task monetary caps, operator-policy tamper rejection, idempotency, pre-I/O release, ambiguous settlement, over-estimate quarantine, Worker redeploy persistence, and aggregate-only authenticated mobile status. No model provider was called.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        try:
            set_staging_policy(*default_policy_nanos())
        except Exception:
            pass
        try:
            cleanup_test_reservations()
        except Exception:
            pass
        raise SystemExit(str(exc)) from None
