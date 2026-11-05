"""Run one harmless end-to-end task proof on the isolated Staging queue.

This is a *manual* (``workflow_dispatch``) P1 evidence run: enqueue -> exact
claim/lease -> supervised read-only child execution -> persisted result ->
terminal read-back -> duplicate-claim rejection. It uses the Cloudflare
Staging queue only (no Supabase fallback, so the evidence lives in one
ledger), never touches Production, never calls a provider, and
**does not count toward the Runtime-004 scheduled acceptance**, which still
requires two distinct genuine ``event=schedule`` probe successes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.redaction import redact
from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError
from engine.supervised_task import complete_supervised_task

STAGING_API_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
TASK_TYPE = "supervised_probe"
OPERATION = "python_platform_probe"
TITLE = "Manual Staging P1 end-to-end task proof (non-schedule)"
IDEMPOTENCY_PREFIX = "staging-p1-proof:"
LEASE_TIMEOUT_SECONDS = 120


class P1ProofError(RuntimeError):
    """The manual Staging P1 proof failed a fail-closed precondition."""


def validate_environment(environ: Mapping[str, str]) -> tuple[str, str]:
    """Require a reviewed Staging target and a real manual main-branch run."""
    if environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise P1ProofError("This proof runs only from a workflow_dispatch event")
    if environ.get("GITHUB_REF") != "refs/heads/main":
        raise P1ProofError("This proof runs only from reviewed main")
    run_id = environ.get("GITHUB_RUN_ID", "")
    run_attempt = environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not re.fullmatch(r"[0-9]{1,20}", run_id) or not re.fullmatch(r"[0-9]{1,3}", run_attempt):
        raise P1ProofError("Invalid workflow run identifier")
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise P1ProofError("Refusing any API target other than the isolated Staging Worker")
    if environ.get("RAAWAA_ENGINE_PROCESS_SUPERVISOR", "").strip().lower() != "enabled":
        raise P1ProofError("The process supervisor switch must be explicitly enabled for this proof")
    if environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "disabled":
        raise P1ProofError("Live inference must be explicitly disabled for this proof")
    if any(environ.get(name, "") for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")):
        raise P1ProofError("Provider credentials must not be present in this process")
    if environ.get("RAAWAA_ENGINE_STAGING_TOKEN_INSTALLED", "").strip().lower() != "true":
        raise P1ProofError("The temporary Staging token must be installed for this proof")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise P1ProofError("A temporary Staging-only API token is required")
    # One fresh durable task per workflow run attempt: a re-dispatch is a new
    # proof, and an idempotent replay must never masquerade as fresh execution.
    return token, f"{IDEMPOTENCY_PREFIX}{run_id}:{run_attempt}"


def _payload(task: Mapping[str, Any]) -> dict[str, Any]:
    raw = task.get("payload_json")
    if not isinstance(raw, str):
        raise P1ProofError("Queue returned an invalid proof payload")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise P1ProofError("Queue returned invalid proof JSON") from None
    if payload != {"operation": OPERATION}:
        raise P1ProofError("Queue returned an unexpected proof operation")
    return payload


def run_p1_proof(
    environ: Mapping[str, str] | None = None,
    *,
    store_factory: Callable[..., Any] = RemoteTaskStore,
    processor: Callable[..., dict[str, Any]] = complete_supervised_task,
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
        raise P1ProofError("Queue omitted the enqueued task")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise P1ProofError("Queue returned an invalid task id")
    if (task.get("task_type") != TASK_TYPE or task.get("title") != TITLE
            or task.get("idempotency_key") != idempotency_key):
        raise P1ProofError("Queue returned a task outside the reviewed proof envelope")
    _payload(task)
    if task.get("status") != "pending":
        # A fresh run-scoped key must produce a fresh pending row. Anything
        # else means the evidence would not prove a new execution.
        raise P1ProofError("The proof task is not freshly pending")

    claimed = store.claim_supervised_probe(task_id)
    if claimed is None:
        raise P1ProofError("The exact proof task was not claimable")
    if (claimed.get("id") != task_id or claimed.get("task_type") != TASK_TYPE
            or claimed.get("status") != "running" or claimed.get("execution_mode") != "dry_run"):
        raise P1ProofError("The exact proof claim failed its task/mode checks")
    _payload(claimed)

    result = processor(
        store, claimed, environ=dict(env), lease_timeout_seconds=LEASE_TIMEOUT_SECONDS,
    )
    if (not isinstance(result, dict) or result.get("id") != task_id
            or result.get("status") != "succeeded"
            or result.get("classification") != "succeeded"
            or result.get("operation") != OPERATION
            or not isinstance(result.get("dispatch_id"), str)
            or result.get("replayed") is not False):
        # Surface only the processor's own bounded status fields so the
        # failure is diagnosable from the workflow summary without logs.
        fields = result if isinstance(result, dict) else {}
        raise P1ProofError(
            "The supervised task did not produce a fresh successful read-only result "
            f"(status={fields.get('status')}, classification={fields.get('classification')}, "
            f"reason={fields.get('reason')})"
        )

    # Durable read-back: the terminal row must be visible through the list API,
    # and the settled task must refuse a second claim (no duplicate execution).
    matches = [
        row for row in store.list_tasks(100)
        if isinstance(row, dict) and row.get("id") == task_id
    ]
    if len(matches) != 1 or matches[0].get("status") != "succeeded":
        raise P1ProofError("Terminal proof task was not readable as succeeded")
    if store.claim_supervised_probe(task_id) is not None:
        raise P1ProofError("A settled proof task accepted a duplicate claim")

    # Audit read-back: the enqueue/claim/finish rows for this exact task must
    # be independently inspectable through the remote API, not only asserted
    # to exist inside the Worker.
    audit_events = store.audit_events(limit=100, task_id=task_id)
    audit_names = [
        row.get("event") for row in audit_events
        if isinstance(row, dict) and row.get("task_id") == task_id
    ]
    for required in ("task_enqueued", "task_claimed", "task_succeeded"):
        if required not in audit_names:
            raise P1ProofError(f"Audit read-back is missing {required} for the proof task")

    return {
        "task_id": task_id,
        "dispatch_id": result["dispatch_id"],
        "idempotency_key": idempotency_key,
        "status": "succeeded",
        "classification": "succeeded",
        "operation": OPERATION,
        "readback_status": matches[0].get("status"),
        "audit_events": audit_names,
        "duplicate_claim_blocked": True,
        "replayed": False,
        "provider_calls": 0,
        "external_writes": 0,
        "backend": "cloudflare_staging",
        "counts_toward_schedule_acceptance": False,
    }


def _safe_failure_detail(exc: Exception) -> str | None:
    if not isinstance(exc, (P1ProofError, RemoteTaskStoreError)):
        return None
    detail = " ".join(redact(str(exc)).split())[:320]
    return detail or None


def _write_step_summary(line: str) -> None:
    """Mirror the bounded outcome into the Actions step summary.

    Step logs are not always retrievable by automation; the summary carries
    the same redacted, length-bounded line so failures stay diagnosable.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        result = run_p1_proof()
    except Exception as exc:  # Fail closed; unexpected messages may carry request data.
        detail = _safe_failure_detail(exc)
        suffix = f": {detail}" if detail else ""
        print(f"Staging P1 task proof failed: {type(exc).__name__}{suffix}", file=sys.stderr)
        _write_step_summary(f"Staging P1 task proof FAILED: {type(exc).__name__}{suffix}")
        return 1
    print(json.dumps(result, sort_keys=True))
    _write_step_summary("Staging P1 task proof: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
