"""Remote Staging-only verification for the Workers AI Neuron ledger.

This script tests D1 accounting and lifecycle controls only. It never calls
Cloudflare Workers AI, never sends a prompt, and requires no AI binding.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get(
    "RAAWAA_ENGINE_STAGING_URL",
    "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
).rstrip("/")
TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_API_TOKEN", "")
ADMIN_TOKEN = os.environ.get("RAAWAA_ENGINE_STAGING_QUOTA_ADMIN_TOKEN", "")
MODEL_ID = "@cf/google/gemma-4-26b-a4b-it"
DAILY_CAP = 10_000
PREFIX = "staging-verification-neuron-"
STAGING_D1_ID = "231dd948-04a0-49e3-ad73-444f77d69477"
PRODUCTION_D1_ID = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_staging_target() -> None:
    with Path("wrangler.staging.toml").open("rb") as stream:
        staging = tomllib.load(stream)
    with Path("wrangler.toml").open("rb") as stream:
        production = tomllib.load(stream)
    staging_d1 = staging.get("d1_databases", [])
    production_d1 = production.get("d1_databases", [])
    require(staging.get("name") == "raawaa-autonomous-engine-staging",
            "Refusing Neuron-ledger verification: Staging Worker name mismatch")
    require(len(staging_d1) == 1 and len(production_d1) == 1,
            "Refusing Neuron-ledger verification: expected exactly one D1 binding per config")
    require(staging_d1[0].get("database_name") == "raawaa-engine-staging" and
            staging_d1[0].get("database_id") == STAGING_D1_ID,
            "Refusing Neuron-ledger verification: Staging D1 identity mismatch")
    require(production_d1[0].get("database_id") == PRODUCTION_D1_ID and
            staging_d1[0].get("database_id") != production_d1[0].get("database_id"),
            "Refusing Neuron-ledger verification: production/Staging D1 separation mismatch")
    require(staging.get("vars", {}).get("WORKERS_AI_LEDGER_CONTROL") == "staging-only",
            "Refusing Neuron-ledger verification: missing Staging-only control")
    require("ai" not in staging and "ai" not in production,
            "Refusing accounting-only test: an AI binding must not be configured")
    require(production.get("vars", {}).get("WORKERS_AI_LEDGER_CONTROL") is None,
            "Refusing verification: production must not expose the Neuron ledger route")


def execute_staging_sql(sql: str) -> None:
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
        detail = (result.stderr or result.stdout or "unknown Wrangler error")[-1000:]
        if "[code: 7429]" not in detail.lower() or attempt == 2:
            raise RuntimeError("Staging-only Neuron-ledger cleanup failed: " + detail)
        # UPDATE/DELETE cleanup statements are idempotent; retry only D1's
        # transient overloaded-database error, never an uncertain provider call.
        time.sleep(2 ** (attempt + 1))


def api(method: str, path: str, payload: dict[str, Any] | None = None,
        *, admin: bool = False, authenticated: bool = True,
        readiness_timeout_seconds: int = 90) -> tuple[int, dict[str, Any]]:
    token = ADMIN_TOKEN if admin else TOKEN
    headers = {"Accept": "application/json", "User-Agent": "RaawaaStagingNeuronLedgerVerification/1.0"}
    if authenticated:
        require(bool(token), "Staging API token is unavailable")
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    deadline = time.monotonic() + readiness_timeout_seconds if authenticated else None
    while True:
        request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
            # The Worker checks auth before routing; 401 means the handler did
            # not execute, so replaying this exact request is safe during secret propagation.
            if authenticated and status == 401 and time.monotonic() < deadline:
                time.sleep(2)
                continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Staging Worker transport failed: " + type(exc).__name__) from None
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f"Staging Worker returned non-JSON HTTP {status}") from None
        require(isinstance(body, dict), f"Staging Worker returned an invalid JSON object (HTTP {status})")
        return status, body


def reservation(task_suffix: str, estimated: int, *, idempotency_key: str | None = None) -> dict[str, Any]:
    return {
        "action": "reserve",
        "reservation_id": str(uuid.uuid4()),
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "model_id": MODEL_ID,
        "task_kind": "research_brief",
        "task_id": PREFIX + task_suffix,
        "data_classification": "public",
        "estimated_neurons": estimated,
    }


def post(payload: dict[str, Any], *, admin: bool = False) -> tuple[int, dict[str, Any]]:
    return api("POST", "/v1/workers-ai-neurons", payload, admin=admin)


def cleanup_test_rows() -> None:
    verify_staging_target()
    execute_staging_sql(
        "DELETE FROM workers_ai_neuron_quarantines WHERE reservation_id IN "
        "(SELECT reservation_id FROM workers_ai_neuron_reservations "
        f"WHERE task_id LIKE '{PREFIX}%')"
    )
    execute_staging_sql(
        f"DELETE FROM workers_ai_neuron_reservations WHERE task_id LIKE '{PREFIX}%'"
    )


def main() -> None:
    verify_staging_target()
    require(bool(TOKEN) and bool(ADMIN_TOKEN), "Staging bearer/admin tokens are required")
    try:
        health, _ = api("GET", "/health", authenticated=False)
        require(health == 200, f"Staging Worker health failed: HTTP {health}")

        # Make a previous interrupted test safe to resume: disable first, then
        # delete only fixtures carrying this script's unique task prefix.
        status, body = post({"action": "disable"}, admin=True)
        require(status == 200 and body.get("enabled") is False,
                "Could not disable Staging Neuron ledger before cleanup")
        cleanup_test_rows()

        status, snapshot = api("GET", "/v1/workers-ai-neurons")
        require(status == 200, f"Neuron-ledger status failed: HTTP {status}")
        policy = snapshot.get("policy", {})
        require(policy.get("enabled") is False, "Neuron ledger must start disabled")
        require(policy.get("daily_cap_neurons") == DAILY_CAP, "Daily cap is not exactly 10,000 Neurons")
        require(policy.get("allowed_model_id") == MODEL_ID, "Pinned model policy mismatch")

        status, body = post({"action": "enable"}, admin=True)
        require(status == 200 and body.get("enabled") is True,
                "Admin could not enable the Staging-only accounting test")

        # Eight concurrent 1,500-Neuron reservations must admit exactly six
        # (9,000 total) and reject two; the SQL INSERT is the linearization point.
        payloads = [reservation(f"concurrency-{i}", 1500) for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(post, payloads))
        accepted = [item for item in results if item[0] == 201]
        rejected = [item for item in results if item[0] == 409 and item[1].get("error") == "daily_neuron_cap_exceeded"]
        require(len(accepted) == 6 and len(rejected) == 2,
                f"Concurrent cap result mismatch: accepted={len(accepted)} rejected={len(rejected)}")
        status, snapshot = api("GET", "/v1/workers-ai-neurons")
        require(status == 200 and snapshot["usage"]["total_neurons"] == 9000,
                "Concurrent reservations exceeded or undercounted the daily cap")
        cleanup_test_rows()

        # Stable idempotency, no release after I/O, ambiguous preservation,
        # and settlement replay are tested with one deterministic lifecycle.
        payload = reservation("lifecycle", 1000)
        status, created = post(payload)
        require(status == 201 and created.get("status") == "reserved", "Initial reservation failed")
        status, replay = post(payload)
        require(status == 200 and replay.get("replay") is True, "Exact reservation replay was not idempotent")
        changed = dict(payload, estimated_neurons=999)
        status, conflict = post(changed)
        require(status == 409 and conflict.get("error") == "idempotency_conflict",
                "Changed payload reused an idempotency key")

        reservation_id = payload["reservation_id"]
        status, started = post({"action": "mark_in_flight", "reservation_id": reservation_id})
        require(status == 200 and started.get("status") == "in_flight", "Could not mark reservation in flight")
        status, forbidden_release = post({"action": "release", "reservation_id": reservation_id})
        require(status == 409 and forbidden_release.get("error") == "release_after_provider_io_forbidden",
                "Release after provider I/O was not blocked")
        status, ambiguous = post({"action": "ambiguous", "reservation_id": reservation_id})
        require(status == 200 and ambiguous.get("reservation_preserved") is True,
                "Ambiguous outcome did not preserve reserved Neurons")
        status, snapshot = api("GET", "/v1/workers-ai-neurons")
        require(status == 200 and snapshot["usage"]["pending_neurons"] == 1000,
                "Ambiguous reservation was not counted against the daily cap")
        status, settled = post({"action": "settle", "reservation_id": reservation_id, "actual_neurons": 900})
        require(status == 200 and settled.get("status") == "settled", "Settlement failed")
        status, settled_replay = post({"action": "settle", "reservation_id": reservation_id, "actual_neurons": 900})
        require(status == 200 and settled_replay.get("replay") is True, "Exact settlement replay failed")
        status, settle_conflict = post({"action": "settle", "reservation_id": reservation_id, "actual_neurons": 901})
        require(status == 409 and settle_conflict.get("error") == "settlement_conflict",
                "Conflicting settlement was not blocked")
        status, snapshot = api("GET", "/v1/workers-ai-neurons")
        require(status == 200 and snapshot["usage"]["settled_neurons"] == 900 and
                snapshot["usage"]["pending_neurons"] == 0,
                "Settled Neuron usage was not reflected accurately")

        # Pre-I/O release is allowed and no longer consumes the daily allowance.
        releasable = reservation("pre-io-release", 1000)
        status, created = post(releasable)
        require(status == 201, "Pre-I/O reservation failed")
        status, released = post({"action": "release", "reservation_id": releasable["reservation_id"]})
        require(status == 200 and released.get("status") == "released", "Pre-I/O release failed")
        status, release_replay = post({"action": "release", "reservation_id": releasable["reservation_id"]})
        require(status == 200 and release_replay.get("replay") is True, "Release replay was not idempotent")
        status, snapshot = api("GET", "/v1/workers-ai-neurons")
        require(status == 200 and snapshot["usage"]["total_neurons"] == 900,
                "Released reservation still consumes the daily cap")

        # An actual usage above the estimate is recorded and quarantines the
        # pinned model; no subsequent reservation can reach an inference route.
        over = reservation("over-estimate", 500)
        status, created = post(over)
        require(status == 201, "Over-estimate fixture reservation failed")
        status, started = post({"action": "mark_in_flight", "reservation_id": over["reservation_id"]})
        require(status == 200, "Over-estimate fixture could not start")
        status, settled = post({"action": "settle", "reservation_id": over["reservation_id"], "actual_neurons": 600})
        require(status == 200 and settled.get("over_estimate") is True and
                settled.get("model_quarantined") is True,
                "Over-estimate did not quarantine the model")
        blocked = reservation("after-quarantine", 1)
        status, body = post(blocked)
        require(status == 409 and body.get("error") == "model_quarantined",
                "Quarantined model accepted a new reservation")

        print("workers_ai_neuron_ledger=verified")
        print("concurrent_reservations=6_of_8; accepted_neurons=9000; cap=10000")
        print("idempotency=verified; ambiguous_reservations_preserved=true")
        print("pre_io_release=verified; post_io_release=blocked")
        print("over_estimate_quarantine=verified")
        print("ai_binding=false; inference_requests=0; paid_fallback=0")
    finally:
        # Always return the isolated Staging ledger to disabled and remove only
        # this script's fixtures. No production D1 ID is accepted by these helpers.
        try:
            post({"action": "disable"}, admin=True)
        finally:
            # Directly force the Staging-only policy off even if the Worker
            # endpoint is temporarily unavailable, then remove only our fixtures.
            try:
                execute_staging_sql(
                    "UPDATE workers_ai_neuron_policy SET enabled=0,updated_at=CURRENT_TIMESTAMP "
                    "WHERE policy_key='default'"
                )
            finally:
                cleanup_test_rows()
        status, final = api("GET", "/v1/workers-ai-neurons")
        require(status == 200 and final.get("policy", {}).get("enabled") is False,
                "Staging Neuron ledger did not finish disabled")
        require(final.get("usage", {}).get("total_neurons") == 0,
                "Staging Neuron test fixtures remain after cleanup")
        print("final_policy_enabled=false; test_fixtures_remaining=0")


if __name__ == "__main__":
    main()
