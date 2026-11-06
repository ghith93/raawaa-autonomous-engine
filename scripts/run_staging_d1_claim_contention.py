"""Prove one-winner concurrent claims against the isolated remote Staging D1.

This uses the exact-task supervised-probe claim route, so it cannot drain or
claim unrelated queue items. It performs no process dispatch, model inference,
provider calls, production writes, or external actions.
"""
from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.remote_task_store import RemoteTaskStore

STAGING_API_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
TASK_TYPE = "supervised_probe"
TITLE = "Remote Staging D1 concurrent claim contention proof"
OPERATION = "python_platform_probe"
IDEMPOTENCY_PREFIX = "staging-d1-contention:"


class StagingContentionProofError(RuntimeError):
    """The remote Staging D1 contention proof failed a safety or result check."""


def validate_environment(environ: Mapping[str, str]) -> tuple[str, str]:
    """Refuse any target other than a reviewed-main, zero-inference Staging run."""
    if environ.get("GITHUB_REF") != "refs/heads/main":
        raise StagingContentionProofError("Proof is restricted to reviewed main")
    if environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise StagingContentionProofError("Unsupported workflow event")
    run_id = environ.get("GITHUB_RUN_ID", "")
    attempt = environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not re.fullmatch(r"[0-9]{1,20}", run_id) or not re.fullmatch(r"[0-9]{1,3}", attempt):
        raise StagingContentionProofError("Invalid workflow run identity")
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise StagingContentionProofError("Refusing any API target other than isolated Staging")
    if environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "disabled":
        raise StagingContentionProofError("Live inference must be explicitly disabled")
    if any(environ.get(name, "") for name in (
        "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    )):
        raise StagingContentionProofError("Provider credentials must not be present")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise StagingContentionProofError("Temporary Staging-only API token is required")
    return token, f"{IDEMPOTENCY_PREFIX}{run_id}:{attempt}"


def run_proof(
    environ: Mapping[str, str] | None = None,
    *,
    store_factory: Callable[..., Any] = RemoteTaskStore,
    executor_factory: Callable[..., Any] = ThreadPoolExecutor,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    token, idempotency_key = validate_environment(env)
    store = store_factory(STAGING_API_URL, token, timeout=30, reject_redirects=True)

    task = store.enqueue(
        TASK_TYPE,
        TITLE,
        {"operation": OPERATION},
        idempotency_key=idempotency_key,
        max_attempts=1,
        priority=100,
    )
    if not isinstance(task, dict):
        raise StagingContentionProofError("Staging queue omitted the enqueued task")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise StagingContentionProofError("Staging queue returned an invalid task id")
    if (task.get("task_type") != TASK_TYPE or task.get("title") != TITLE
            or task.get("idempotency_key") != idempotency_key
            or task.get("status") != "pending"):
        raise StagingContentionProofError("Staging did not create the exact fresh pending proof task")
    try:
        payload = json.loads(task.get("payload_json", ""))
    except (TypeError, ValueError):
        payload = None
    if payload != {"operation": OPERATION}:
        raise StagingContentionProofError("Staging returned an unexpected proof payload")

    winner = None
    settled = False
    try:
        # Both requests target the same exact task. The Worker uses one atomic
        # conditional UPDATE ... WHERE id=? AND status='pending' ... RETURNING.
        with executor_factory(max_workers=2) as pool:
            futures = [pool.submit(store.claim_supervised_probe, task_id) for _ in range(2)]
            claims = [future.result() for future in futures]
        winners = [claim for claim in claims if claim is not None]
        losers = [claim for claim in claims if claim is None]
        if len(winners) != 1 or len(losers) != 1:
            raise StagingContentionProofError("Remote D1 contention did not yield exactly one winner")
        winner = winners[0]
        if (winner.get("id") != task_id or winner.get("task_type") != TASK_TYPE
                or winner.get("status") != "running" or winner.get("execution_mode") != "dry_run"
                or not isinstance(winner.get("lease_token"), str) or not winner["lease_token"]):
            raise StagingContentionProofError("The winning Staging lease failed identity or mode checks")

        rows = [row for row in store.list_tasks(100)
                if isinstance(row, dict) and row.get("id") == task_id]
        if len(rows) != 1 or rows[0].get("status") != "running" or rows[0].get("attempts") != 1:
            raise StagingContentionProofError("Remote D1 readback did not show one running attempt")

        store.finish(
            task_id,
            "succeeded",
            result={
                "mode": "dry_run",
                "proof": "remote_d1_single_winner",
                "claim_winners": 1,
                "provider_calls": 0,
                "external_writes": 0,
            },
            lease_token=winner["lease_token"],
            execution_mode="dry_run",
        )
        settled = True

        terminal = [row for row in store.list_tasks(100)
                    if isinstance(row, dict) and row.get("id") == task_id]
        if (len(terminal) != 1 or terminal[0].get("status") != "succeeded"
                or terminal[0].get("attempts") != 1):
            raise StagingContentionProofError("Remote D1 terminal readback failed")
        if store.claim_supervised_probe(task_id) is not None:
            raise StagingContentionProofError("A terminal task accepted a duplicate claim")

        audit = store.audit_events(limit=100, task_id=task_id)
        names = [row.get("event") for row in audit
                 if isinstance(row, dict) and row.get("task_id") == task_id]
        if names.count("task_claimed") != 1 or not all(
            event in names for event in ("task_enqueued", "task_succeeded")
        ):
            raise StagingContentionProofError("Remote audit readback did not prove one claim and completion")

        return {
            "status": "succeeded",
            "task_id": task_id,
            "backend": "cloudflare_staging_d1",
            "concurrent_claim_requests": 2,
            "claim_winners": 1,
            "claim_losers": 1,
            "attempts": 1,
            "terminal_status": "succeeded",
            "duplicate_claim_blocked": True,
            "audit_claim_events": names.count("task_claimed"),
            "provider_calls": 0,
            "external_writes": 0,
            "production_touched": False,
            "spend_usd": 0,
        }
    finally:
        # Do not leave an owned lease running if an assertion fails mid-proof.
        if winner is not None and not settled:
            try:
                store.finish(
                    task_id, "blocked",
                    error="Remote D1 contention proof aborted; no task execution was started.",
                    lease_token=winner["lease_token"],
                    execution_mode="dry_run",
                )
            except Exception:
                pass


def main() -> int:
    try:
        result = run_proof()
    except Exception as exc:
        # Exception messages from transport layers are intentionally not echoed:
        # they may contain infrastructure details. The type is sufficient here.
        print(f"Remote Staging D1 contention proof failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
