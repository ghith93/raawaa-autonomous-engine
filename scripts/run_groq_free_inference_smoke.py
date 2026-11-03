"""One-shot, Free-only Groq inference smoke test against isolated Staging D1."""
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

from engine.batch_runner import run_batch
from engine.cli import db_path
from engine.live_runtime import build_live_gateway
from engine.remote_task_store import RemoteTaskStore
from engine.worker import run_once

IDEMPOTENCY_KEY = "groq-free-inference-staging-smoke-2026-11-03-v4"
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
                        return store._request("/v1/mobile/status")
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
    return {
        "task_id": task_id,
        "task_counts": _counts(mobile),
        "provider_usage": mobile.get("provider_usage"),
        "model_costs_usd": mobile.get("model_costs_usd"),
        "monetary_ledger": mobile.get("monetary_ledger"),
        "route_usage_and_audit": routes,
    }


def main() -> int:
    if os.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "enabled":
        raise SystemExit("Refusing inference: explicit one-shot live switch is missing.")
    if not os.environ.get("GROQ_API_KEY", "").strip():
        raise SystemExit("Refusing inference: Groq API credential is not configured.")

    store = RemoteTaskStore(
        base_url=os.environ.get("RAAWAA_ENGINE_API_URL"),
        token=os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN"),
        access_client_id=os.environ.get("CF_ACCESS_CLIENT_ID"),
        access_client_secret=os.environ.get("CF_ACCESS_CLIENT_SECRET"),
    )
    before_mobile = _wait_for_authenticated_worker(store)
    counts = _counts(before_mobile)
    if counts.get("pending", 0) or counts.get("running", 0):
        raise SystemExit("Refusing smoke test: Staging queue is not idle.")

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
    )
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise SystemExit("Task enqueue did not return a valid task ID.")

    if task.get("status") == "succeeded":
        # Re-running this workflow must never repeat a successful model call.
        mobile = store._request("/v1/mobile/status")
        ledger = mobile.get("monetary_ledger", {})
        if (not ledger.get("available") or ledger.get("daily_spent_usd") != 0
                or ledger.get("pending_count") != 0 or ledger.get("ambiguous_count") != 0
                or mobile.get("model_costs_usd", {}).get("total") != 0):
            raise SystemExit("Previously completed smoke task has unexpected cost or ledger state.")
        print(json.dumps({
            "status": "already_succeeded_no_replay",
            "task_id": task_id,
            "provider": "groq",
            "model_routes": list(ROUTE_IDS),
            "new_provider_calls": 0,
            "daily_spend_usd": ledger.get("daily_spent_usd"),
        }, sort_keys=True))
        return 0
    if task.get("status") != "pending":
        print(json.dumps({"status": "not_pending_no_replay", "diagnostics": _diagnostics(store, task_id)}, sort_keys=True))
        raise SystemExit("Smoke task is not pending; refusing automatic replay.")

    before_routes = {route: _quota(store, route, task_id) for route in ROUTE_IDS}
    gateway = build_live_gateway(
        db_path(), ROOT / "config" / "policy.groq-smoke.json",
        ROOT / "config" / "provider_profiles.groq-smoke.json", environ=os.environ,
    )
    if gateway is None:
        raise SystemExit("Live gateway failed closed; no provider request was sent.")

    batch = run_batch(
        store, max_tasks=1, max_seconds=60, max_parallel=1,
        runner=lambda current_store: run_once(
            current_store, live_gateway=gateway, environ=os.environ,
        ),
    )
    result = (batch.get("results") or [{}])[0]
    if (batch.get("tasks_processed") != 1 or result.get("status") != "succeeded"
            or result.get("mode") != "live_inference" or result.get("id") != task_id):
        print(json.dumps({
            "status": "batch_failed_no_replay",
            "batch": batch,
            "diagnostics": _diagnostics(store, task_id),
        }, sort_keys=True))
        raise SystemExit("Bounded batch did not complete the expected live-inference task.")

    saved = next((item for item in store.list_tasks(100) if item.get("id") == task_id), None)
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

    after_mobile = store._request("/v1/mobile/status")
    ledger = after_mobile.get("monetary_ledger", {})
    if (not ledger.get("available") or ledger.get("daily_spent_usd") != 0
            or ledger.get("monthly_spent_usd") != 0 or ledger.get("pending_count") != 0
            or ledger.get("ambiguous_count") != 0 or after_mobile.get("model_costs_usd", {}).get("total") != 0):
        raise SystemExit("Monetary ledger did not settle at exactly zero cost.")

    print(json.dumps({
        "status": "success",
        "task_id": task_id,
        "mode": result.get("mode"),
        "provider": "groq",
        "used_routes": used_routes,
        "daily_spend_usd": ledger.get("daily_spent_usd"),
        "monthly_spend_usd": ledger.get("monthly_spent_usd"),
        "pending_reservations": ledger.get("pending_count"),
        "ambiguous_reservations": ledger.get("ambiguous_count"),
        "paid_fallback_enabled": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
