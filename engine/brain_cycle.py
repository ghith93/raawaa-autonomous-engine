"""Read-only executive brain cycle: portfolio triage and outcome feedback.

This is a deterministic decision-support layer. It does not call models, write
memory, enqueue or claim tasks, alter policy, or authorize execution.

Statistics policy (v2): a failure share is always reported with a Wilson 95%
interval, and a task type is only escalated to ``reliability_needs_review`` when
the *lower bound* of that interval clears the threshold. A point estimate from a
handful of runs is reported as ``reliability_watch`` instead, because at n=5 a
single failure produces a 20% point estimate whose 95% interval spans roughly
3.6% to 62.4% -- indistinguishable from a healthy task type.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from engine.executive_intelligence import build_executive_snapshot

TERMINAL_OUTCOMES = {"succeeded", "failed"}
MIN_RELIABILITY_SAMPLE = 5
FAILURE_SHARE_THRESHOLD = 0.20
WILSON_Z = 1.96

#: The local SQLite adapter caps list_tasks at 200 and the remote D1 adapter at
#: 100, so 100 is the only limit both adapters honour. Requesting 200 silently
#: yields 100 rows in production.
DEFAULT_SNAPSHOT_LIMIT = 100


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


def wilson_interval(failures: int, sample: int, z: float = WILSON_Z) -> dict[str, float] | None:
    """Return the Wilson score interval for a failure share, or None without data."""
    if sample <= 0:
        return None
    proportion = failures / sample
    denominator = 1.0 + z * z / sample
    centre = proportion + z * z / (2 * sample)
    margin = z * math.sqrt(proportion * (1 - proportion) / sample + z * z / (4 * sample * sample))
    return {
        "low": round(max(0.0, (centre - margin) / denominator), 4),
        "high": round(min(1.0, (centre + margin) / denominator), 4),
    }


def _tally(tasks: list[Any]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """Count terminal outcomes per task type and how many records were excluded.

    A record is counted at most once: non-terminal status first, then a missing
    or blank task type. Non-dict records are counted as malformed rather than
    raising, because a single unexpected row from a remote adapter must not make
    the whole cycle unusable.
    """
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"succeeded": 0, "failed": 0})
    excluded = {"non_terminal": 0, "missing_task_type": 0, "malformed": 0}

    for task in tasks:
        if not isinstance(task, dict):
            excluded["malformed"] += 1
            continue
        status = task.get("status")
        raw_type = task.get("task_type")
        # Group on the stripped value: "research_brief " and "research_brief"
        # are the same task type, and splitting them halves the sample.
        task_type = raw_type.strip() if isinstance(raw_type, str) else ""
        if status not in TERMINAL_OUTCOMES:
            excluded["non_terminal"] += 1
            continue
        if not task_type:
            excluded["missing_task_type"] += 1
            continue
        grouped[task_type]["succeeded" if status == "succeeded" else "failed"] += 1

    return grouped, excluded


def build_outcome_scorecards(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize execution reliability, not semantic quality, by task type.

    Only succeeded/failed terminal executions are counted. Blocked, cancelled,
    pending and running tasks are excluded so they cannot distort the rate.
    Small samples are explicitly marked insufficient and never treated as
    learned capability scores, and no escalation is issued from a point estimate
    alone: the Wilson lower bound has to clear the threshold.
    """
    grouped, _ = _tally(tasks)

    scorecards = []
    for task_type, counts in sorted(grouped.items()):
        sample = counts["succeeded"] + counts["failed"]
        rate = counts["succeeded"] / sample if sample else None
        failure_share = counts["failed"] / sample if sample else None
        interval = wilson_interval(counts["failed"], sample)

        if sample < MIN_RELIABILITY_SAMPLE:
            evidence = "insufficient_sample"
            recommendation = "collect_more_verified_outcomes"
        elif interval is not None and interval["low"] >= FAILURE_SHARE_THRESHOLD:
            evidence = "reliability_needs_review"
            recommendation = "investigate_failure_patterns"
        elif interval is not None and interval["high"] >= FAILURE_SHARE_THRESHOLD:
            # The point estimate is at or above the threshold but the interval
            # reaches well below it: a signal worth watching, not an escalation.
            evidence = "reliability_watch"
            recommendation = "widen_sample_before_concluding"
        else:
            evidence = "provisional_reliability_signal"
            recommendation = "continue_monitoring"

        scorecards.append({
            "task_type": task_type,
            "sample_size": sample,
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "execution_success_rate": round(rate, 4) if rate is not None else None,
            "failure_share": round(failure_share, 4) if failure_share is not None else None,
            "failure_share_ci95": interval,
            "evidence_level": evidence,
            "recommended_action": recommendation,
            "quality_score": None,
            "quality_note": "Execution status is not a quality/reviewer score; semantic quality requires independent acceptance-check evidence.",
        })
    return scorecards


def build_brain_cycle(
    tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    limit: int = 10,
    snapshot_limit: int = DEFAULT_SNAPSHOT_LIMIT,
) -> dict[str, Any]:
    """Build one deterministic, read-only brain cycle from existing task records.

    ``limit`` is a portfolio view control only (it bounds the recommendation
    list); reliability scorecards always consume the full snapshot, because
    trimming them would change the measured denominator.
    """
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list of task objects")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    if type(snapshot_limit) is not int or snapshot_limit < 1:
        raise ValueError("snapshot_limit must be a positive integer")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    clean = [task for task in tasks if isinstance(task, dict)]
    _, excluded = _tally(tasks)

    portfolio = build_executive_snapshot(clean, now=current, limit=limit)
    scorecards = build_outcome_scorecards(clean)
    review_needed = [
        item for item in scorecards
        if item["evidence_level"] == "reliability_needs_review"
    ]
    watch = [
        item for item in scorecards
        if item["evidence_level"] == "reliability_watch"
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
                f"{item['sample_size']} terminal executions "
                f"(failure share 95% interval "
                f"{item['failure_share_ci95']['low']:.1%}-"
                f"{item['failure_share_ci95']['high']:.1%})."
            ),
            "authority": "observed_terminal_execution_counts",
            "requires_model": False,
        })
    for item in watch:
        decisions.append({
            "decision": "widen_sample_for_task_type",
            "priority": "medium",
            "basis": (
                f"{item['task_type']} shows {item['failed']} failures in "
                f"{item['sample_size']} terminal executions, but the 95% interval "
                f"({item['failure_share_ci95']['low']:.1%}-"
                f"{item['failure_share_ci95']['high']:.1%}) still includes "
                "comfortable failure shares."
            ),
            "authority": "observed_terminal_execution_counts",
            "requires_model": False,
        })

    created = [stamp for stamp in (_timestamp(task.get("created_at")) for task in clean) if stamp]
    snapshot = {
        "requested_limit": snapshot_limit,
        "ordering": "created_at desc",
        "records_returned": len(tasks),
        "records_usable": len(clean),
        "truncated": len(tasks) >= snapshot_limit,
        "truncation_note": (
            "truncated=True means the adapter returned a full window, so older "
            "records may exist and the scorecards describe a recent window "
            "rather than the whole population."
        ),
        "oldest_returned": min(created).isoformat() if created else None,
        "newest_returned": max(created).isoformat() if created else None,
    }

    return {
        "schema_version": 2,
        "cycle_id": "read-only-brain-cycle-v1",
        "generated_at": current.isoformat(),
        "mode": "read_only_deterministic_brain",
        "portfolio": portfolio,
        "snapshot": snapshot,
        "excluded": excluded,
        "learning": {
            "method": "outcome_feedback_v2",
            "scorecards": scorecards,
            "reliability_reviews_needed": len(review_needed),
            "reliability_watch_count": len(watch),
            "insufficient_sample_count": len(insufficient),
            "automatic_policy_changes": 0,
            "automatic_prompt_changes": 0,
            "memory_writes": 0,
            "limitations": [
                "Task terminal status measures execution reliability, not semantic output quality.",
                "Independent reviewer and acceptance-check evidence are not yet represented in this scorecard.",
                "The snapshot is bounded by the task-store adapter: the remote D1 adapter returns at most 100 records ordered newest first.",
                "A failure share is escalated only when the Wilson 95% lower bound clears the threshold; smaller samples are reported as a watch signal.",
                "One row per task is counted; retries collapse into the task's attempt count and never inflate the denominator.",
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
