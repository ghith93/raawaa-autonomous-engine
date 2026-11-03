"""Deterministic, read-only executive view over the existing task ledger.

This module deliberately does not create tasks, reorder the queue, claim work,
or call models. It provides explainable operational triage using existing data.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# These are triage weights, not permissions or execution guarantees.
TASK_PROFILES = {
    "system_audit": (90, "Protects system integrity and operating-policy compliance."),
    "code_review": (85, "Reduces change risk before integration."),
    "research_brief": (65, "Builds evidence for strategic and technical decisions."),
    "content_draft": (40, "Produces a reviewable content artifact."),
}
ATTENTION_STATUSES = {"failed", "blocked"}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_hours(value: Any, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600)


def _task_priority(task: dict[str, Any], now: datetime) -> dict[str, Any]:
    task_type = str(task.get("task_type") or "unknown")
    base, rationale = TASK_PROFILES.get(
        task_type, (50, "Unclassified task; requires an explicit business rationale.")
    )
    age = _age_hours(task.get("created_at"), now)
    age_boost = min(20.0, (age or 0.0) / 24.0 * 10.0)
    retry_boost = min(10, max(0, int(task.get("attempts") or 0)) * 5)
    score = round(min(100.0, base * 0.7 + age_boost + retry_boost), 1)
    return {
        "task_id": task.get("id"),
        "title": task.get("title"),
        "task_type": task_type,
        "status": task.get("status"),
        "priority_score": score,
        "priority_band": "critical" if score >= 80 else "high" if score >= 65 else "normal",
        "age_hours": round(age, 2) if age is not None else None,
        "attempts": task.get("attempts", 0),
        "rationale": rationale,
    }


def build_executive_snapshot(
    tasks: list[dict[str, Any]], *, now: datetime | None = None, limit: int = 10
) -> dict[str, Any]:
    """Return an explainable portfolio snapshot without mutating the task store."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    counts = Counter(str(task.get("status") or "unknown") for task in tasks)
    pending = [_task_priority(task, current) for task in tasks if task.get("status") == "pending"]
    pending.sort(key=lambda item: (-item["priority_score"], item["age_hours"] is None,
                                   -(item["age_hours"] or 0), str(item["task_id"] or "")))

    attention = []
    for task in tasks:
        if task.get("status") not in ATTENTION_STATUSES:
            continue
        attention.append({
            "task_id": task.get("id"),
            "title": task.get("title"),
            "task_type": task.get("task_type"),
            "status": task.get("status"),
            "attempts": task.get("attempts", 0),
            "error": task.get("error"),
            "updated_at": task.get("updated_at"),
        })
    attention.sort(key=lambda item: (item["status"] != "failed", str(item.get("updated_at") or "")))

    expired_running = []
    for task in tasks:
        if task.get("status") != "running":
            continue
        expiry = _parse_time(task.get("lease_expires_at"))
        if expiry is None or expiry <= current:
            expired_running.append({
                "task_id": task.get("id"),
                "title": task.get("title"),
                "lease_expires_at": task.get("lease_expires_at"),
                "reason": "missing_or_expired_lease",
            })

    recommendations = []
    if expired_running:
        recommendations.append({
            "priority": "critical",
            "action": "inspect_expired_leases",
            "reason": "Running tasks have missing or expired leases; verify recovery before new work.",
        })
    if attention:
        recommendations.append({
            "priority": "high",
            "action": "triage_failed_or_blocked",
            "reason": "Resolve failed/blocked work before expanding the backlog.",
        })
    if pending:
        recommendations.append({
            "priority": "normal",
            "action": "execute_highest_ranked_pending_task",
            "task_id": pending[0]["task_id"],
            "reason": "Use the deterministic ranking as decision support; the queue's existing claim order remains unchanged.",
        })
    if not recommendations:
        recommendations.append({
            "priority": "normal",
            "action": "maintain_and_monitor",
            "reason": "No pending, failed, blocked, or expired-lease tasks were found in this snapshot.",
        })

    return {
        "schema_version": 1,
        "generated_at": current.isoformat(),
        "mode": "read_only_decision_support",
        "tasks_inspected": len(tasks),
        "status_counts": dict(sorted(counts.items())),
        "portfolio_health": {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "needs_attention": len(attention),
            "expired_or_unleased_running": len(expired_running),
        },
        "priority_method": {
            "type_weight": "70% of task-type base weight",
            "age_boost": "up to 20 points; 10 points per 24 hours",
            "retry_boost": "5 points per prior attempt, capped at 10",
            "unknown_task_type_base": 50,
            "note": "Scores are transparent triage hints, not automatic queue ordering or authorization.",
        },
        "next_best_tasks": pending[:limit],
        "needs_attention": attention[:limit],
        "expired_running_tasks": expired_running[:limit],
        "recommendations": recommendations,
        "side_effects": {
            "tasks_created": 0,
            "tasks_claimed": 0,
            "queue_reordered": False,
            "network_calls": 0,
            "model_calls": 0,
        },
    }


def executive_status(task_store, *, limit: int = 10) -> dict[str, Any]:
    """Read the existing task ledger through its established adapter."""
    return build_executive_snapshot(task_store.list_tasks(limit=200), limit=limit)
