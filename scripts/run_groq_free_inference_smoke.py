"""One-shot, verified Groq Free-tier inference smoke against isolated Staging D1.

Never upgrade plans, use paid fallback, or send non-public task data. Staging admin credentials are isolated and revoked after each run. The Staging D1 budget policy is forced to zero before every live request; the exact policy is verified by the durable ledger before provider I/O.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.remote_task_store import RemoteTaskStore
from scripts.run_specialist_lanes import main as run_specialist_lanes

IDEMPOTENCY_KEY = "groq-free-inference-staging-specialist-smoke-2026-11-06-v9"
PREVIOUS_AMBIGUOUS_TASK_ID = "775916a6-8f2e-43c0-84aa-f3cc7712cf18"
EXECUTION_LANE = "specialist:researcher"
ROUTE_IDS = ("groq:primary:openai/gpt-oss-20b",)



def _wait_for_authenticated_worker(store: RemoteTaskStore, timeout_seconds: int = 120) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "worker_not_ready"
    while time.monotonic() < deadline:
        try:
            headers = {"Accept": "application/json"}
            client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
            client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
            if client_id and client_secret:
                headers["CF-Access-Client-Id"] = client_id
                headers["CF-Access-Client-Secret"] = client_secret
            headers["User-Agent"] = (
                "RaawaaAutonomousEngine/1.0 "
                "(+https://github.com/ghith93/raawaa-autonomous-engine)"
            )
            request = urllib.request.Request(
                os.environ["RAAWAA_ENGINE_API_URL"].rstrip("/") + "/health",
                headers=headers,
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    try:
                        mobile = store._request("/v1/mobile/status")
                        if os.environ.get("RAAWAA_ENGINE_WORKER_LANE", "general") != EXECUTION_LANE:
                            raise RuntimeError("specialist_worker_lane_not_configured")
                        store.list_tasks(1, worker_lane=EXECUTION_LANE)
                        return mobile
                    except Exception as exc:
                        last_error = type(exc).__name__
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP_{exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(2)
    raise SystemExit("Staging Worker authentication did not become ready: " + last_error)


def _counts(mobile: dict) -> dict[str, int]:
    rows = mobile.get("tasks", [])
    return {str(row.get("status")): int(row.get("count", 0))
            for row in rows if isinstance(row, dict)}


def _quota(store: RemoteTaskStore, route_id: str, task_id: str) -> dict:
    query = urlencode({"route_id": route_id, "task_id": task_id})
    return store._request("/v1/quota?" + query)


def _diagnostics(store: RemoteTaskStore, task_id: str) -> dict:
    mobile = store._request("/v1/mobile/status")
    routes = {route: _quota(store, route, task_id) for route in ROUTE_IDS}
    rows = store.list_tasks(100, worker_lane=EXECUTION_LANE)
    exact = next((row for row in rows if row.get("id") == task_id), None)
    task_state = None if exact is None else {
        key: exact.get(key)
        for key in ("status", "attempts", "max_attempts", "execution_mode",
                    "error", "lease_expires_at", "updated_at", "execution_lane")
        if key in exact
    }
    return {
        "task_id": task_id,
        "task_state": task_state,
        "task_counts": _counts(mobile),
        "provider_usage": mobile.get("provider_usage"),
        "model_costs_usd": mobile.get("model_costs_usd"),
        "monetary_ledger": mobile.get("monetary_ledger"),
        "route_usage_and_audit": routes,
    }


def _verify_previous_ambiguous_task(store: RemoteTaskStore) -> dict:
    """Settle the one known zero-cost ambiguity once; accept its exact settled replay."""
    recovery = store.recover_stale(worker_lane=EXECUTION_LANE)
    rows = store.list_tasks(100, worker_lane=EXECUTION_LANE)
    if len(rows) >= 100:
        raise SystemExit("Refusing smoke: researcher lane readback may be incomplete.")
    previous = next((row for row in rows
                     if row.get("id") == PREVIOUS_AMBIGUOUS_TASK_ID), None)
    if not previous or previous.get("status") not in {"failed", "blocked"}:
        raise SystemExit(
            "Refusing smoke: previous ambiguous task is not terminal; no replay or new task will be created."
        )
    active = [row for row in rows if row.get("status") in {"pending", "running"}]
    if active:
        raise SystemExit(
            "Refusing smoke: researcher lane still contains pending/running work after stale recovery."
        )

    query = urlencode({"task_id": PREVIOUS_AMBIGUOUS_TASK_ID})
    snapshot = store._request("/v1/monetary-ledger?" + query).get("snapshot")
    if not isinstance(snapshot, dict):
        raise SystemExit("Refusing smoke: previous task monetary snapshot is unavailable.")
    unresolved = snapshot.get("unresolved", [])
    settled = snapshot.get("settled", [])
    if not isinstance(unresolved, list) or not isinstance(settled, list):
        raise SystemExit("Refusing smoke: previous task monetary history is malformed.")

    def exact_zero_cost(row: dict) -> bool:
        return (
            row.get("task_id") == PREVIOUS_AMBIGUOUS_TASK_ID
            and row.get("provider") == "groq"
            and row.get("model") == "openai/gpt-oss-20b"
            and row.get("estimated_cost_nanos") == 0
        )

    ambiguous = [
        row for row in unresolved
        if isinstance(row, dict) and exact_zero_cost(row)
        and row.get("status") == "ambiguous"
    ]
    if ambiguous:
        if len(ambiguous) != 1 or len(unresolved) != 1 or int(snapshot.get("task_pending_count", -1)) != 1:
            raise SystemExit(
                "Refusing smoke: prior ambiguous reservation does not match the exact zero-cost record."
            )
        reservation_id = ambiguous[0].get("reservation_id")
        if not isinstance(reservation_id, str) or not reservation_id:
            raise SystemExit("Refusing smoke: the exact ambiguous reservation has no durable ID.")
        # The provider's Free tier is verified and this route has a hard zero
        # estimate. Settle only this exact reservation; keep token quota consumed.
        result = store._request("/v1/monetary-ledger", "POST", {
            "action": "settle",
            "reservation_id": reservation_id,
            "actual_cost_nanos": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        })
        if result.get("settled") is not True:
            raise SystemExit("Could not settle the exact verified-Free zero-cost reservation.")
        after = store._request("/v1/monetary-ledger?" + query).get("snapshot", {})
        status = "settled"
    else:
        # A later invocation may encounter the exact same task after the prior
        # run already settled it. Accept only a read-only, exact zero-cost row;
        # never repeat the settlement mutation or provider call.
        if unresolved or int(snapshot.get("task_pending_count", -1)) != 0:
            raise SystemExit(
                "Refusing smoke: prior ambiguous reservation does not match the exact zero-cost record or has unexpected unresolved monetary state."
            )
        reservation_id = None
        settled_matches = [
            row for row in settled
            if isinstance(row, dict) and exact_zero_cost(row)
            and row.get("status") == "settled"
            and row.get("actual_cost_nanos") == 0
            and row.get("input_tokens") == 0
            and row.get("output_tokens") == 0
            and isinstance(row.get("reservation_id"), str)
        ]
        if len(settled_matches) != 1 or len(settled) != 1:
            raise SystemExit(
                "Refusing smoke: previous task lacks one exact settled zero-cost reservation."
            )
        reservation_id = settled_matches[0]["reservation_id"]
        after = snapshot
        status = "already_settled"

    after_unresolved = after.get("unresolved", [])
    after_settled = after.get("settled", [])
    verified = [
        row for row in after_settled
        if isinstance(row, dict) and exact_zero_cost(row)
        and row.get("reservation_id") == reservation_id
        and row.get("status") == "settled"
        and row.get("actual_cost_nanos") == 0
        and row.get("input_tokens") == 0
        and row.get("output_tokens") == 0
    ]
    if (
        int(after.get("task_pending_count", -1)) != 0
        or after_unresolved
        or len(verified) != 1
    ):
        raise SystemExit("The exact ambiguous reservation is not durably settled at zero cost.")
    return {
        "task_id": PREVIOUS_AMBIGUOUS_TASK_ID,
        "task_status": previous.get("status"),
        "stale_recovery": recovery,
        "reservation_id": reservation_id,
        "reservation_status": status,
        "actual_cost_nanos": 0,
        "token_quota_estimate_preserved": True,
    }


def _assert_zero_spend(store: RemoteTaskStore, successful_task_id: str) -> dict:
    mobile = store._request("/v1/mobile/status")
    ledger = mobile.get("monetary_ledger", {})
    if (
        not ledger.get("available")
        or ledger.get("daily_spent_usd") != 0
        or ledger.get("monthly_spent_usd") != 0
        or ledger.get("pending_reserved_usd") != 0
        or ledger.get("pending_count") != 0
        or ledger.get("ambiguous_count") != 0
        or mobile.get("model_costs_usd", {}).get("total") != 0
    ):
        raise SystemExit("Monetary ledger did not settle with zero spend and no unresolved reservations.")

    global_snapshot = store._request("/v1/monetary-ledger").get("snapshot", {})
    if global_snapshot.get("unresolved", []):
        raise SystemExit("Global monetary ledger contains unresolved reservations.")

    task_snapshot = store._request(
        "/v1/monetary-ledger?" + urlencode({"task_id": successful_task_id})
    ).get("snapshot", {})
    if (
        int(task_snapshot.get("task_pending_count", -1)) != 0
        or int(task_snapshot.get("task_spent_nanos", -1)) != 0
    ):
        raise SystemExit("The new smoke task did not settle at exactly zero cost.")
    return {
        "daily_spend_usd": ledger.get("daily_spent_usd"),
        "monthly_spend_usd": ledger.get("monthly_spent_usd"),
        "pending_reservations": 0,
        "ambiguous_reservations": 0,
    }



def _assert_only_exact_smoke_task_pending(store: RemoteTaskStore, task_id: str) -> None:
    """Inspect only the researcher lane; other lanes cannot be claimed by this worker."""
    rows = store.list_tasks(100, worker_lane=EXECUTION_LANE)
    if len(rows) >= 100:
        raise SystemExit("Refusing smoke test: researcher lane readback may be incomplete.")
    pending = [row for row in rows if row.get("status") == "pending"]
    running = [row for row in rows if row.get("status") == "running"]
    if len(pending) != 1 or pending[0].get("id") != task_id or running:
        raise SystemExit(
            "Refusing smoke test: researcher lane contains work outside this exact smoke task."
        )


def main() -> int:
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "enabled":
        raise SystemExit("Refusing inference: explicit one-shot live switch is missing.")
    if not os.environ.get("GROQ_API_KEY", "").strip():
        raise SystemExit("Refusing inference: Groq API credential is not configured.")
    if os.environ.get("RAAWAA_ENGINE_WORKER_LANE") != EXECUTION_LANE:
        raise SystemExit("Refusing inference: the dedicated researcher lane is not selected.")
    if len(os.environ.get("RAAWAA_ENGINE_LANE_TOKEN", "")) < 32:
        raise SystemExit("Refusing inference: the ephemeral researcher lane capability is missing.")

    store = RemoteTaskStore(
        base_url=os.environ.get("RAAWAA_ENGINE_API_URL"),
        token=os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN"),
        access_client_id=os.environ.get("CF_ACCESS_CLIENT_ID"),
        access_client_secret=os.environ.get("CF_ACCESS_CLIENT_SECRET"),
    )
    before_mobile = _wait_for_authenticated_worker(store)
    previous_ambiguous = _verify_previous_ambiguous_task(store)
    task = store.enqueue(
        "research_brief",
        "Single public Groq Free-tier inference connectivity check",
        {
            "data_classification": "public",
            "topic": "Confirm one bounded free-tier model inference and report the selected model.",
            "questions": ["Return a concise confirmation that the smoke test completed."],
            "source_urls": [],
        },
        idempotency_key=IDEMPOTENCY_KEY,
        max_attempts=1,
        execution_lane=EXECUTION_LANE,
    )
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise SystemExit("Task enqueue did not return a valid task ID.")

    if task.get("status") == "succeeded":
        # Re-running this workflow must never repeat a successful model call.
        ledger_summary = _assert_zero_spend(store, task_id)
        print(json.dumps({
            "status": "already_succeeded_no_replay",
            "task_id": task_id,
            "provider": "groq",
            "model_routes": list(ROUTE_IDS),
            "new_provider_calls": 0,
            "previous_ambiguous_task": previous_ambiguous,
            **ledger_summary,
        }, sort_keys=True))
        return 0
    if task.get("status") != "pending":
        print(json.dumps({"status": "not_pending_no_replay", "diagnostics": _diagnostics(store, task_id)}, sort_keys=True))
        raise SystemExit("Smoke task is not pending; refusing automatic replay.")

    # A prior preflight may have enqueued this exact idempotent task before a
    # later local-policy check failed. Permit only that one pending task; never
    # drain unrelated Staging work or race an unrelated pending task.
    _assert_only_exact_smoke_task_pending(store, task_id)

    before_routes = {route: _quota(store, route, task_id) for route in ROUTE_IDS}
    if run_specialist_lanes() != 0:
        print(json.dumps({
            "status": "specialist_runner_failed_no_replay",
            "diagnostics": _diagnostics(store, task_id),
        }, sort_keys=True))
        raise SystemExit("The integrated Staging specialist runner did not complete the expected task.")

    saved = next((item for item in store.list_tasks(100, worker_lane=EXECUTION_LANE)
                  if item.get("id") == task_id), None)
    if not saved or saved.get("status") != "succeeded":
        raise SystemExit("D1 does not confirm the smoke task succeeded.")

    after_routes = {route: _quota(store, route, task_id) for route in ROUTE_IDS}
    used_routes = []
    for route in ROUTE_IDS:
        before = before_routes[route].get("usage", {}).get("task", {})
        after = after_routes[route].get("usage", {}).get("task", {})
        delta_requests = int(after.get("requests", 0)) - int(before.get("requests", 0))
        delta_tokens = int(after.get("tokens", 0)) - int(before.get("tokens", 0))
        if delta_requests > 0:
            used_routes.append({"route_id": route, "requests": delta_requests, "tokens": delta_tokens})
    if not used_routes or sum(item["tokens"] for item in used_routes) < 1:
        raise SystemExit("No durable provider token usage was recorded for the smoke task.")

    ledger_summary = _assert_zero_spend(store, task_id)

    print(json.dumps({
        "status": "success",
        "task_id": task_id,
        "mode": "live_inference",
        "provider": "groq",
        "execution_lane": EXECUTION_LANE,
        "used_routes": used_routes,
        "previous_ambiguous_task": previous_ambiguous,
        **ledger_summary,
        "paid_fallback_enabled": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
