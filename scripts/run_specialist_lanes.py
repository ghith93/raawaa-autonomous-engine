"""Run explicitly enabled specialist consumers as bounded, zero-cost Staging batches.

The runner is deliberately pinned to the isolated Staging Worker. It uses the
reviewed Groq Free-only overlay, durable D1 quota/monetary ledgers, and D1-backed
invocation checkpoints. No task is claimed unless the live switch, unique lane
capability, public-data policy, and gateway preflight all pass.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.batch_runner import run_batch
from engine.cli import db_path
from engine.live_runtime import build_live_gateway
from engine.remote_task_store import RemoteTaskStore
from engine.task_invocation_store import TaskInvocationStore
from engine.worker import run_once


POLICY_PATH = ROOT / "config" / "policy.groq-smoke.json"
REGISTRY_PATH = ROOT / "config" / "provider_profiles.groq-smoke.json"
STAGING_HOST = "raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"

# The current verified Free route admits public research briefs only. Other
# specialist lanes remain dormant until their own task/data policy and provider
# route have been independently verified.
LANES = (
    ("specialist:researcher", "RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN", "research_brief"),
    ("specialist:reviewer", "RAAWAA_ENGINE_REVIEWER_LANE_TOKEN", "code_review"),
    ("specialist:marketer", "RAAWAA_ENGINE_MARKETER_LANE_TOKEN", "content_draft"),
    ("specialist:operator", "RAAWAA_ENGINE_OPERATOR_LANE_TOKEN", "system_audit"),
    ("specialist:mobile_tester", "RAAWAA_ENGINE_MOBILE_TESTER_LANE_TOKEN", "mobile_test"),
)


def _is_exact_staging_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == STAGING_HOST
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


def _safe_batch_outcome(batch: dict) -> tuple[bool, int, str]:
    """Accept only an idle queue or one successfully completed live task."""
    if not isinstance(batch, dict) or batch.get("status") != "completed":
        return False, 0, "invalid_batch_report"
    processed = batch.get("tasks_processed")
    if type(processed) is not int or processed not in (0, 1):
        return False, 0, "unexpected_task_count"
    results = batch.get("results", [])
    if not isinstance(results, list):
        return False, 0, "invalid_results"
    for result in results:
        if not isinstance(result, dict):
            return False, 0, "invalid_task_result"
        if result.get("status") == "idle":
            continue
        if result.get("status") != "succeeded" or result.get("mode") != "live_inference":
            return False, processed, "task_not_successfully_executed"
    if processed == 0 and batch.get("stop_reason") != "queue_idle":
        return False, processed, "empty_batch_not_idle"
    if processed == 1 and not any(
        isinstance(item, dict)
        and item.get("status") == "succeeded"
        and item.get("mode") == "live_inference"
        for item in results
    ):
        return False, processed, "missing_live_success"
    return True, processed, str(batch.get("stop_reason", "unknown"))


def main() -> int:
    base = os.environ.copy()
    required = (
        "RAAWAA_ENGINE_API_URL",
        "RAAWAA_ENGINE_API_TOKEN",
        "RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN",
        "GROQ_API_KEY",
    )
    missing = [name for name in required if not base.get(name, "").strip()]
    if missing:
        print("Specialist runner blocked: required Staging configuration is missing.")
        return 1

    if base.get("RAAWAA_ENGINE_LIVE_INFERENCE", "disabled").strip().lower() != "enabled":
        print("Specialist runner dormant: live inference is disabled; no tasks were claimed.")
        return 0

    if not _is_exact_staging_url(base["RAAWAA_ENGINE_API_URL"]):
        print("Specialist runner blocked: live specialist execution is restricted to the exact Staging Worker.")
        return 1

    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        allowed_task_types = policy["live_inference_data_policy"]["allowed_task_types"]
    except (OSError, ValueError, KeyError, TypeError):
        print("Specialist runner blocked: the reviewed live data policy could not be loaded.")
        return 1
    if not isinstance(allowed_task_types, list) or not all(
        isinstance(item, str) for item in allowed_task_types
    ):
        print("Specialist runner blocked: the live data policy is invalid.")
        return 1

    enabled = []
    seen_tokens = {
        base.get("RAAWAA_ENGINE_API_TOKEN", ""),
        base.get("RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN", ""),
    }
    for lane, token_name, task_type in LANES:
        lane_token = base.get(token_name, "").strip()
        if len(lane_token) < 32 or lane_token in seen_tokens:
            print(f"{lane}: dormant (dedicated unique capability not configured)")
            continue
        seen_tokens.add(lane_token)
        if task_type not in allowed_task_types:
            print(f"{lane}: dormant (current verified route does not admit {task_type})")
            continue
        enabled.append((lane, lane_token))

    if not enabled:
        print("No specialist lane is enabled by both a unique capability and the reviewed task policy.")
        return 0

    failures = 0
    for lane, lane_token in enabled:
        env = base.copy()
        env["RAAWAA_ENGINE_WORKER_LANE"] = lane
        env["RAAWAA_ENGINE_LANE_TOKEN"] = lane_token
        try:
            task_store = RemoteTaskStore(
                base_url=env["RAAWAA_ENGINE_API_URL"],
                token=env["RAAWAA_ENGINE_API_TOKEN"],
                access_client_id=env.get("CF_ACCESS_CLIENT_ID") or env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID"),
                access_client_secret=env.get("CF_ACCESS_CLIENT_SECRET") or env.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET"),
                lane_token=lane_token,
            )
            invocation_store = TaskInvocationStore(task_store, worker_lane=lane)
            gateway = build_live_gateway(
                db_path(),
                POLICY_PATH,
                REGISTRY_PATH,
                environ=env,
                invocation_store=invocation_store,
            )
            if gateway is None:
                print(f"{lane}: blocked (zero-cost live gateway failed closed)")
                failures += 1
                continue

            batch = run_batch(
                task_store,
                max_tasks=1,
                max_seconds=35,
                runner=lambda current_store: run_once(
                    current_store,
                    live_gateway=gateway,
                    environ=env,
                    worker_lane=lane,
                ),
                max_parallel=1,
            )
            ok, processed, stop_reason = _safe_batch_outcome(batch)
            print(
                f"{lane}: {'ok' if ok else 'failed'}, "
                f"tasks_processed={processed}, stop_reason={stop_reason}"
            )
            if not ok:
                failures += 1
        except Exception as exc:
            # Do not echo exception text; it may contain provider or transport details.
            print(f"{lane}: failed ({type(exc).__name__})")
            failures += 1

    print(f"Specialist lanes enabled={len(enabled)}, failed={failures}; Staging-only, zero-cost policy enforced.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
