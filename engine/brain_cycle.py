"""Read-only executive brain cycle: portfolio triage and outcome feedback.

This is a deterministic decision-support layer. It does not call models, write
memory, enqueue or claim tasks, alter policy, or authorize execution.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from engine.executive_intelligence import build_executive_snapshot

TERMINAL_OUTCOMES = {"succeeded", "failed"}
MIN_RELIABILITY_SAMPLE = 5


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def build_outcome_scorecards(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize execution reliability, not semantic quality, by task type.

    Only succeeded/failed terminal executions are counted. Blocked, cancelled,
    pending and running tasks are excluded so they cannot distort the rate.
    Small samples are explicitly marked insufficient and never treated as
    learned capability scores.
    """
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"succeeded": 0, "failed": 0})
    for task in tasks:
        if not isinstance(task, dict):
            continue
        outcome = task.get("status")
        task_type = task.get("task_type")
        if outcome in TERMINAL_OUTCOMES and isinstance(task_type, str) and task_type.strip():
            grouped[task_type]["succeeded" if outcome == "succeeded" else "failed"] += 1

    scorecards = []
    for task_type, counts in sorted(grouped.items()):
        sample = counts["succeeded"] + counts["failed"]
        rate = counts["succeeded"] / sample if sample else None
        if sample < MIN_RELIABILITY_SAMPLE:
            evidence = "insufficient_sample"
            recommendation = "collect_more_verified_outcomes"
        elif counts["failed"] / sample >= 0.2:
            evidence = "reliability_needs_review"
            recommendation = "investigate_failure_patterns"
        else:
            evidence = "provisional_reliability_signal"
            recommendation = "continue_monitoring"
        scorecards.append({
            "task_type": task_type,
            "sample_size": sample,
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "execution_success_rate": round(rate, 4) if rate is not None else None,
            "evidence_level": evidence,
            "recommended_action": recommendation,
            "quality_score": None,
            "quality_note": "Execution status is not a quality/reviewer score; semantic quality requires independent acceptance-check evidence.",
        })
    return scorecards


def build_brain_cycle(
    tasks: list[dict[str, Any]], *, now: datetime | None = None, limit: int = 10
) -> dict[str, Any]:
    """Build one deterministic, read-only brain cycle from existing task records."""
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise ValueError("tasks must be a list of task objects")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    portfolio = build_executive_snapshot(tasks, now=current, limit=limit)
    scorecards = build_outcome_scorecards(tasks)
    review_needed = [
        item for item in scorecards
        if item["evidence_level"] == "reliability_needs_review"
    ]
    insufficient = [
        item for item in scorecards
        if item["evidence_level"] == "insufficient_sample"
    ]

    decisions = []
    for recommendation in portfolio["recommendations"]:
        decisions.append({
            "decision": recommendation["action"],
            "priority": recommendation["priority"],
            "basis": recommendation["reason"],
            "authority": "deterministic_policy_and_task_state",
            "requires_model": False,
        })
    for item in review_needed:
        decisions.append({
            "decision": "review_task_type_reliability",
            "priority": "high",
            "basis": (
                f"{item['task_type']} has {item['failed']} failures in "
                f"{item['sample_size']} terminal executions."
            ),
            "authority": "observed_terminal_execution_counts",
            "requires_model": False,
        })

    return {
        "schema_version": 1,
        "cycle_id": "read-only-brain-cycle-v1",
        "generated_at": current.isoformat(),
        "mode": "read_only_deterministic_brain",
        "portfolio": portfolio,
        "learning": {
            "method": "outcome_feedback_v1",
            "scorecards": scorecards,
            "reliability_reviews_needed": len(review_needed),
            "insufficient_sample_count": len(insufficient),
            "automatic_policy_changes": 0,
            "automatic_prompt_changes": 0,
            "memory_writes": 0,
            "limitations": [
                "Task terminal status measures execution reliability, not semantic output quality.",
                "Independent reviewer and acceptance-check evidence are not yet represented in this scorecard.",
                "The current snapshot is limited to records returned by the existing task-store adapter.",
            ],
        },
        "decision_candidates": decisions,
        "guardrails": {
            "task_writes": 0,
            "task_claims": 0,
            "queue_reordering": False,
            "model_calls": 0,
            "provider_calls": 0,
            "external_writes": 0,
            "budget_or_permission_changes": 0,
            "dispatch_authorized": False,
            "production_changes_authorized": False,
        },
    }
