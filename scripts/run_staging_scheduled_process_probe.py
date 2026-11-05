"""Run one exact, read-only supervised task on the Staging queue from Cron only.

The primary is the isolated Cloudflare Staging queue. A narrowly scoped,
GitHub-OIDC-authenticated Supabase Free queue is used only when Cloudflare is
unavailable or the Staging token could not be installed. Neither backend can
claim an unrelated task; the fallback is not a general-purpose engine queue.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.github_oidc import GithubOidcError, request_github_oidc_token
from engine.redaction import redact
from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError
from engine.supabase_fallback_task_store import (
    SUPABASE_FALLBACK_PUBLISHABLE_KEY,
    SUPABASE_FALLBACK_URL,
    SUPABASE_OIDC_AUDIENCE,
    SupabaseFallbackTaskStore,
)
from engine.supervised_task import complete_supervised_task

STAGING_API_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
TASK_TYPE = "supervised_probe"
OPERATION = "python_platform_probe"
TITLE = "Scheduled Staging read-only process probe"
IDEMPOTENCY_PREFIX = "staging-cron-supervised-probe:"
LEASE_TIMEOUT_SECONDS = 120


class ScheduledProbeError(RuntimeError):
    """The scheduled Staging probe failed a fail-closed precondition."""


def _daily_slot_idempotency_key(now: datetime | None = None) -> str:
    """Deduplicate schedule events by the UTC calendar slot, not run ID."""
    instant = now or datetime.now(timezone.utc)
    return IDEMPOTENCY_PREFIX + instant.astimezone(timezone.utc).strftime("%Y%m%d")


def _previous_slot_key(idempotency_key: str) -> str:
    """Return the exact previous UTC daily-slot key for ``idempotency_key``."""
    raw = idempotency_key.removeprefix(IDEMPOTENCY_PREFIX)
    day = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
    return IDEMPOTENCY_PREFIX + (day - timedelta(days=1)).strftime("%Y%m%d")


def _reconcile_previous_slot(store: Any, idempotency_key: str, *, backend: str) -> dict[str, Any]:
    """Disposition yesterday's exact slot without creating or sweeping tasks.

    A new UTC day creates a new task; it must not silently abandon a prior
    slot left ``running`` by a crashed runner. This narrowly scoped check
    looks up only the previous day's exact probe key (never enqueues it),
    and — on the Cloudflare ledger only — routes a still-running row through
    the same exact-task expired-lease recovery as today's slot. Its outcome
    is returned for monitoring; the row is exposed by task ID and terminal
    status rather than being hidden or broadly swept.
    """
    previous_key = _previous_slot_key(idempotency_key)
    if backend != "cloudflare_staging":
        return {"status": "skipped_cross_ledger", "idempotency_key": previous_key}
    matches = [
        task for task in store.list_tasks(100)
        if isinstance(task, dict)
        and task.get("idempotency_key") == previous_key
        and task.get("task_type") == TASK_TYPE
        and task.get("title") == TITLE
    ]
    if not matches:
        return {"status": "absent", "idempotency_key": previous_key}
    if len(matches) != 1:
        raise ScheduledProbeError("Previous-slot probe lookup returned multiple exact tasks")
    previous = matches[0]
    previous_id = previous.get("id")
    if not isinstance(previous_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", previous_id):
        raise ScheduledProbeError("Previous-slot probe returned an invalid task id")
    _payload(previous)
    if previous.get("status") != "running":
        return {
            "status": "already_dispositioned",
            "idempotency_key": previous_key,
            "task_id": previous_id,
            "task_status": previous.get("status"),
        }
    recovery = store.recover_supervised_probe(previous_id)
    if not isinstance(recovery, dict) or recovery.get("status") not in {"pending", "failed", "blocked"}:
        status = recovery.get("status") if isinstance(recovery, dict) else "invalid"
        raise ScheduledProbeError(f"Previous-slot probe is not dispositioned (status={status})")
    return {
        "status": "recovery_applied",
        "idempotency_key": previous_key,
        "task_id": previous_id,
        "task_status": recovery.get("status"),
        "recovered": bool(recovery.get("recovered")),
    }


def _payload(task: Mapping[str, Any]) -> dict[str, Any]:
    raw = task.get("payload_json")
    if not isinstance(raw, str):
        raise ScheduledProbeError("Queue returned an invalid probe payload")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise ScheduledProbeError("Queue returned invalid probe JSON") from None
    if payload != {"operation": OPERATION}:
        raise ScheduledProbeError("Queue returned an unexpected probe operation")
    return payload


def validate_environment(environ: Mapping[str, str]) -> tuple[str, str]:
    """Require a reviewed Staging target and a real main-branch Cron event."""
    if environ.get("GITHUB_EVENT_NAME") != "schedule":
        raise ScheduledProbeError("This probe runs only from a GitHub schedule event")
    if environ.get("GITHUB_REF") != "refs/heads/main":
        raise ScheduledProbeError("This probe runs only from reviewed main")
    run_id = environ.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        raise ScheduledProbeError("Invalid scheduled workflow run identifier")
    api_url = environ.get("RAAWAA_ENGINE_API_URL", "").rstrip("/")
    if api_url != STAGING_API_URL:
        raise ScheduledProbeError("Refusing any API target other than the isolated Staging Worker")
    if environ.get("RAAWAA_ENGINE_PROCESS_SUPERVISOR", "").strip().lower() != "enabled":
        raise ScheduledProbeError("The process supervisor switch must be explicitly enabled for this probe")
    if environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() != "disabled":
        raise ScheduledProbeError("Live inference must be explicitly disabled for this probe")
    if any(environ.get(name, "") for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")):
        raise ScheduledProbeError("Provider credentials must not be present in this process")
    token_installed = environ.get("RAAWAA_ENGINE_STAGING_TOKEN_INSTALLED", "true").strip().lower()
    if token_installed not in {"true", "false"}:
        raise ScheduledProbeError("Staging token installation status is invalid")
    token = environ.get("RAAWAA_ENGINE_API_TOKEN", "")
    if token_installed == "true" and len(token) < 32:
        raise ScheduledProbeError("A temporary Staging-only API token is required")
    return token if token_installed == "true" else "", _daily_slot_idempotency_key()


def _make_fallback_store(
    environ: Mapping[str, str],
    factory: Callable[..., Any],
    oidc_token_factory: Callable[..., str],
) -> Any:
    oidc_token = oidc_token_factory(environ, audience=SUPABASE_OIDC_AUDIENCE)
    return factory(
        SUPABASE_FALLBACK_URL,
        SUPABASE_FALLBACK_PUBLISHABLE_KEY,
        oidc_token,
        timeout=15,
    )


def _run_on_store(
    store: Any,
    environ: Mapping[str, str],
    idempotency_key: str,
    *,
    backend: str,
    fallback_standby: str,
    processor: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    previous_slot = _reconcile_previous_slot(store, idempotency_key, backend=backend)
    task = store.enqueue(
        TASK_TYPE,
        TITLE,
        {"operation": OPERATION},
        idempotency_key=idempotency_key,
        max_attempts=1,
        priority=100,
    )
    if not isinstance(task, dict):
        raise ScheduledProbeError("Queue omitted the enqueued task")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
        raise ScheduledProbeError("Queue returned an invalid task id")
    if (task.get("task_type") != TASK_TYPE or task.get("title") != TITLE
            or task.get("idempotency_key") != idempotency_key):
        raise ScheduledProbeError("Queue returned a task outside the reviewed probe envelope")
    _payload(task)

    # Same-day duplicate schedule events reuse one durable row. A completed row
    # is evidence, not permission to run the child again.
    if task.get("status") == "succeeded":
        return {
            "task_id": task_id,
            "status": "succeeded",
            "classification": "already_settled",
            "replayed": True,
            "recovered": False,
            "previous_slot": previous_slot,
            "provider_calls": 0,
            "external_writes": 0,
            "backend": backend,
            "fallback_standby": fallback_standby,
        }
    recovered = False
    if task.get("status") == "running":
        if backend != "cloudflare_staging":
            raise ScheduledProbeError(
                "The Supabase fallback does not recover a running task; refusing a cross-ledger replay"
            )
        recovery = store.recover_supervised_probe(task_id)
        if not isinstance(recovery, dict) or recovery.get("status") != "pending":
            status = recovery.get("status") if isinstance(recovery, dict) else "invalid"
            raise ScheduledProbeError(f"The exact Staging probe is not recoverable (status={status})")
        recovered = bool(recovery.get("recovered"))
    elif task.get("status") != "pending":
        raise ScheduledProbeError("The idempotent probe task is not pending")

    claimed = store.claim_supervised_probe(task_id)
    if claimed is None:
        raise ScheduledProbeError("The exact probe task was not claimable")
    if (claimed.get("id") != task_id or claimed.get("task_type") != TASK_TYPE
            or claimed.get("status") != "running" or claimed.get("execution_mode") != "dry_run"):
        raise ScheduledProbeError("The exact probe claim failed its task/mode checks")
    _payload(claimed)

    result = processor(
        store, claimed, environ=dict(environ), lease_timeout_seconds=LEASE_TIMEOUT_SECONDS,
    )
    if (not isinstance(result, dict) or result.get("id") != task_id
            or result.get("status") != "succeeded"
            or result.get("classification") != "succeeded"
            or result.get("operation") != OPERATION
            or not isinstance(result.get("dispatch_id"), str)
            or result.get("replayed") is not False):
        raise ScheduledProbeError("The supervised task did not produce a fresh successful read-only result")
    return {
        "task_id": task_id,
        "status": "succeeded",
        "classification": "succeeded",
        "operation": OPERATION,
        "dispatch_id": result["dispatch_id"],
        "replayed": False,
        "recovered": recovered,
        "previous_slot": previous_slot,
        "provider_calls": 0,
        "external_writes": 0,
        "backend": backend,
        "fallback_standby": fallback_standby,
    }


def _safe_failure_detail(exc: Exception) -> str | None:
    """Expose only bounded, redacted messages from known safe domain errors."""
    if not isinstance(exc, (ScheduledProbeError, RemoteTaskStoreError, GithubOidcError)):
        return None
    detail = " ".join(redact(str(exc)).split())[:320]
    return detail or None


def _fallback_eligible(exc: RemoteTaskStoreError) -> bool:
    return exc.retryable or exc.status_code == 1027


def run_scheduled_probe(
    environ: Mapping[str, str] | None = None,
    *,
    store_factory: Callable[..., Any] = RemoteTaskStore,
    fallback_store_factory: Callable[..., Any] = SupabaseFallbackTaskStore,
    oidc_token_factory: Callable[..., str] = request_github_oidc_token,
    processor: Callable[..., dict[str, Any]] = complete_supervised_task,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    token, idempotency_key = validate_environment(env)
    fallback_store = None
    fallback_standby = "not_checked"

    # The scheduled workflow deliberately does NOT enable the standby check:
    # the fallback `/health` RPC (`engine_fallback_ping`) performs a persistent
    # heartbeat write on every call, so invoking it daily from the schedule
    # would be an unreported external mutation. The standby branch below is
    # retained only for explicit manual diagnostics; the fallback store is
    # otherwise created solely when the Cloudflare primary actually fails.
    if env.get("RAAWAA_ENGINE_FALLBACK_STANDBY_CHECK", "").strip().lower() == "enabled":
        try:
            fallback_store = _make_fallback_store(env, fallback_store_factory, oidc_token_factory)
            fallback_store._request("/health", "POST", {})
            fallback_standby = "ready"
        except Exception as exc:
            fallback_store = None
            fallback_standby = "unavailable"

    primary_failure: str | None = None
    if token:
        primary_store = store_factory(
            STAGING_API_URL, token, timeout=30, reject_redirects=True,
        )
        try:
            return _run_on_store(
                primary_store, env, idempotency_key,
                backend="cloudflare_staging", fallback_standby=fallback_standby,
                processor=processor,
            )
        except RemoteTaskStoreError as exc:
            if not _fallback_eligible(exc):
                raise
            primary_failure = _safe_failure_detail(exc) or "Cloudflare Staging API unavailable"
    else:
        primary_failure = "Cloudflare Staging token installation unavailable; primary queue skipped"

    if fallback_store is None:
        try:
            fallback_store = _make_fallback_store(env, fallback_store_factory, oidc_token_factory)
        except Exception as exc:
            detail = _safe_failure_detail(exc) or "Supabase fallback identity unavailable"
            prior = primary_failure or "Cloudflare Staging API unavailable"
            raise ScheduledProbeError(f"{prior}; Supabase fallback unavailable: {detail}") from None

    try:
        result = _run_on_store(
            fallback_store, env, idempotency_key,
            backend="supabase_free_fallback", fallback_standby=fallback_standby,
            processor=processor,
        )
        result["fallback_standby"] = "ready"
    except Exception as exc:
        detail = _safe_failure_detail(exc) or "Supabase fallback operation failed"
        primary = primary_failure or "Cloudflare Staging API unavailable"
        raise ScheduledProbeError(f"{primary}; Supabase fallback failed: {detail}") from None
    result["cloudflare_primary_failure"] = primary_failure
    return result


def main() -> int:
    try:
        result = run_scheduled_probe()
    except Exception as exc:  # Fail closed; unexpected exception messages may contain request data.
        detail = _safe_failure_detail(exc)
        suffix = f": {detail}" if detail else ""
        print(
            f"Scheduled Staging process probe failed: {type(exc).__name__}{suffix}",
            file=sys.stderr,
        )
        return 1
    if result.get("fallback_standby") == "unavailable":
        print(
            "::warning::Supabase Free fallback heartbeat failed; Cloudflare primary result remains independent.",
            file=sys.stderr,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
