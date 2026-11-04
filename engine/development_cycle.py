"""Deterministic, read-only development-cycle report.

The engine already produces the pieces of a development loop — an executive
portfolio snapshot, outcome scorecards, a capability-gap analysis, CI and
watchdog runs. What it did not have was a single place where those signals are
ranked into *what to do next*, with an explanation of why that item is above
the others, and with an explicit statement of what the ranking is not allowed
to authorise.

This module is that place, and only that place:

* no network I/O, no model calls, no spend;
* no task created, claimed, reordered or executed;
* no merge, deploy, migration or provider activation;
* no second queue, scheduler or state store.

It consumes a state document that something else collected and returns a
ranking. Every number below is a documented constant, so two runs over the same
state produce the same order and a disagreement can be argued about at the
level of a weight rather than a feeling.

The state document is intentionally plain JSON so it can be produced by a
workflow, by a CLI probe, or by hand in a review.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
MODE = "read_only_development_cycle"

# --- Escalation thresholds -------------------------------------------------
# These are the only numbers that decide whether a signal becomes an urgent
# action. They are separated from the scoring weights on purpose: changing how
# impatient the loop is about a stale watchdog should not silently reorder the
# backlog.
WATCHDOG_STALE_MINUTES = 30
QUEUE_STALE_MINUTES = 120
PR_AGE_FULL_CREDIT_DAYS = 10
ISSUE_AGE_FULL_CREDIT_DAYS = 30
BRANCH_SPRAWL_THRESHOLD = 150

# --- Weights ---------------------------------------------------------------
# A failing main is always the top of the list: nothing else can be verified
# while the baseline is red, so every other item is worth less until it clears.
W_MAIN_CI_RED = 100
W_DEPLOY_FAILED = 98
W_WATCHDOG_STALE = 92
W_QUEUE_STALE = 84
W_READY_PR = 60
W_REBASE_PR = 46
W_HYGIENE = 18
W_ISSUE_LABEL = {
    "security": 88,
    "bug": 74,
    "enhancement": 44,
}
W_ISSUE_DEFAULT = 40
# Age is worth a fraction of a point per day, capped, so a ten-day-old
# enhancement outranks a fresh one but never outranks a broken pipeline.
W_AGE_PER_DAY = 0.6
W_BLOCKED = 5

OWNER_APPROVAL_KINDS = frozenset({"deploy", "provider_activation", "permissions"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(value: Any, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def _minutes_since(value: Any, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _item(
    kind: str,
    target: str,
    action: str,
    score: float,
    reason: str,
    *,
    gate: str = "no owner approval required",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "target": target,
        "action": action,
        "score": round(score, 1),
        "reason": reason,
        "gate": gate,
        "evidence": evidence or {},
    }


def _pipeline_actions(state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    signals = state.get("signals") or {}

    main_ci = str(signals.get("main_ci_status") or "").lower()
    if main_ci and main_ci not in {"success", "completed/success", "green"}:
        actions.append(
            _item(
                "pipeline",
                "main",
                "fix_red_main_ci",
                W_MAIN_CI_RED,
                f"main CI is {main_ci}; no merge or verification is trustworthy until it is green",
                evidence={"main_ci_status": main_ci, "head_sha": signals.get("main_head_sha")},
            )
        )

    deploy = str(signals.get("deploy_status") or "").lower()
    if deploy in {"failure", "failed", "completed/failure"}:
        actions.append(
            _item(
                "pipeline",
                "deploy-control-worker",
                "diagnose_failed_deploy",
                W_DEPLOY_FAILED,
                "the deploy gate failed, so the running Worker may not match main",
                gate="owner approval required before any redeploy",
                evidence={"deploy_status": deploy},
            )
        )

    watchdog_age = _minutes_since(signals.get("watchdog_last_success_at"), now)
    if watchdog_age is not None and watchdog_age > WATCHDOG_STALE_MINUTES:
        actions.append(
            _item(
                "pipeline",
                "scheduled-task-runner",
                "restore_watchdog_cadence",
                W_WATCHDOG_STALE,
                f"no scheduled run for {int(watchdog_age)} minutes "
                f"(threshold {WATCHDOG_STALE_MINUTES}); the loop is not turning",
                evidence={"minutes_since_last_success": int(watchdog_age)},
            )
        )

    queue_age = signals.get("oldest_pending_age_minutes")
    if isinstance(queue_age, (int, float)) and queue_age > QUEUE_STALE_MINUTES:
        actions.append(
            _item(
                "pipeline",
                "task-queue",
                "drain_stalled_queue",
                W_QUEUE_STALE + min(10.0, queue_age / 60.0),
                f"oldest pending task is {int(queue_age)} minutes old "
                f"(threshold {QUEUE_STALE_MINUTES}); work is arriving but not completing",
                evidence={"oldest_pending_age_minutes": int(queue_age)},
            )
        )
    return actions


def _pull_request_actions(state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for pr in state.get("open_pull_requests") or []:
        number = pr.get("number")
        if number is None:
            continue
        target = f"#{number}"
        age = _age_days(pr.get("created_at"), now) or 0.0
        age_credit = min(PR_AGE_FULL_CREDIT_DAYS, age) / PR_AGE_FULL_CREDIT_DAYS
        mergeable = str(pr.get("mergeable") or "").upper()
        merge_state = str(pr.get("merge_state_status") or "").upper()
        checks = str(pr.get("checks") or "").lower()
        title = str(pr.get("title") or "")[:80]
        labels = [str(label).lower() for label in pr.get("labels") or []]
        # A green check is evidence that nothing regressed; it is not evidence
        # that a change to a trust boundary is correct. Those two are flagged
        # separately so the loop cannot quietly merge one on the strength of CI.
        security_sensitive = "security" in labels or "security" in title.lower()
        gate = (
            "independent correctness review required (security-sensitive)"
            if security_sensitive
            else "no owner approval required"
        )
        draft_note = " (draft)" if pr.get("is_draft") else ""

        if mergeable == "CONFLICTING":
            actions.append(
                _item(
                    "pull_request",
                    target,
                    "rebase_or_supersede",
                    W_REBASE_PR + 4 * age_credit,
                    f"conflicts with main and carries no verifiable evidence until rebuilt: {title}",
                    gate=gate,
                    evidence={"mergeable": mergeable, "age_days": round(age, 1)},
                )
            )
        elif checks in {"success", "green"} and mergeable in {"MERGEABLE", ""} and merge_state == "BEHIND":
            # Green on an old base is not evidence about current main: the run
            # that produced the check never saw the commits it will merge into.
            actions.append(
                _item(
                    "pull_request",
                    target,
                    "refresh_branch_then_verify",
                    W_READY_PR + 2 * age_credit,
                    f"green but its base is stale, so the check does not describe "
                    f"current main: {title}",
                    gate=gate,
                    evidence={
                        "merge_state_status": merge_state,
                        "stale_evidence": True,
                        "age_days": round(age, 1),
                        "is_draft": bool(pr.get("is_draft")),
                        "security_sensitive": security_sensitive,
                    },
                )
            )
        elif checks in {"success", "green"} and mergeable in {"MERGEABLE", ""}:
            actions.append(
                _item(
                    "pull_request",
                    target,
                    "merge_after_verification",
                    W_READY_PR + 10 * age_credit,
                    f"green and mergeable, waiting {round(age, 1)} days{draft_note}: {title}",
                    gate=gate,
                    evidence={
                        "mergeable": mergeable,
                        "checks": checks,
                        "age_days": round(age, 1),
                        "is_draft": bool(pr.get("is_draft")),
                        "security_sensitive": security_sensitive,
                    },
                )
            )
        elif checks in {"failure", "failing", "red"}:
            actions.append(
                _item(
                    "pull_request",
                    target,
                    "diagnose_failing_check",
                    W_READY_PR + 6 * age_credit,
                    f"checks are failing, so the branch cannot be judged: {title}",
                    gate=gate,
                    evidence={"checks": checks},
                )
            )
    return actions


def _issue_actions(state: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for issue in state.get("open_issues") or []:
        number = issue.get("number")
        if number is None:
            continue
        labels = [str(label).lower() for label in issue.get("labels") or []]
        title = str(issue.get("title") or "")[:80]
        age = _age_days(issue.get("created_at"), now) or 0.0
        age_credit = min(ISSUE_AGE_FULL_CREDIT_DAYS, age) * W_AGE_PER_DAY

        if "blocked" in labels or "needs-owner" in labels:
            blocked.append(
                _item(
                    "issue",
                    f"#{number}",
                    "hold",
                    W_BLOCKED,
                    f"marked blocked; it cannot move without an external decision: {title}",
                    gate="external decision required",
                    evidence={"labels": labels, "age_days": round(age, 1)},
                )
            )
            continue

        weight = W_ISSUE_DEFAULT
        for label in labels:
            weight = max(weight, W_ISSUE_LABEL.get(label, W_ISSUE_DEFAULT))
        actions.append(
            _item(
                "issue",
                f"#{number}",
                "advance_issue",
                weight + age_credit,
                f"open {round(age, 1)} days with labels {labels or ['none']}: {title}",
                evidence={"labels": labels, "age_days": round(age, 1)},
            )
        )
    return actions, blocked


def _hygiene_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    branch_count = state.get("remote_branch_count")
    if isinstance(branch_count, int) and branch_count > BRANCH_SPRAWL_THRESHOLD:
        actions.append(
            _item(
                "hygiene",
                "remote-branches",
                "audit_stale_branches",
                W_HYGIENE,
                f"{branch_count} remote branches (threshold {BRANCH_SPRAWL_THRESHOLD}); "
                "the branch list no longer functions as a signal",
                evidence={"remote_branch_count": branch_count},
            )
        )
    drift = state.get("documentation_drift") or []
    if drift:
        actions.append(
            _item(
                "hygiene",
                "docs",
                "reconcile_documentation",
                W_HYGIENE + min(6.0, len(drift) / 2.0),
                f"{len(drift)} documents disagree with the code or with each other",
                evidence={"documents": [str(doc) for doc in drift[:10]]},
            )
        )
    return actions


def build_development_cycle(state: dict[str, Any] | None = None, *, limit: int = 12) -> dict[str, Any]:
    """Rank the next actions from a collected state document.

    Deterministic for a given ``state`` and a given clock minute: ties break on
    the target string, so identical inputs never produce two different orders.
    """
    state = state or {}
    now = _now()

    pipeline = _pipeline_actions(state, now)
    pull_requests = _pull_request_actions(state, now)
    issues, blocked = _issue_actions(state, now)
    hygiene = _hygiene_actions(state)

    ranked = sorted(
        pipeline + pull_requests + issues + hygiene,
        key=lambda item: (-item["score"], item["target"], item["kind"]),
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index

    selected = ranked[: max(1, int(limit))]
    signals = state.get("signals") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "mode": MODE,
        "inputs": {
            "open_pull_requests": len(state.get("open_pull_requests") or []),
            "open_issues": len(state.get("open_issues") or []),
            "signals_present": sorted(signals),
        },
        "health": {
            "main_ci_status": signals.get("main_ci_status"),
            "deploy_status": signals.get("deploy_status"),
            "watchdog_last_success_at": signals.get("watchdog_last_success_at"),
            "oldest_pending_age_minutes": signals.get("oldest_pending_age_minutes"),
            "remote_branch_count": state.get("remote_branch_count"),
        },
        "next_actions": selected,
        "blocked": blocked,
        "deferred": ranked[len(selected):],
        "ranking_notes": {
            "thresholds": {
                "watchdog_stale_minutes": WATCHDOG_STALE_MINUTES,
                "queue_stale_minutes": QUEUE_STALE_MINUTES,
                "branch_sprawl_threshold": BRANCH_SPRAWL_THRESHOLD,
            },
            "note": (
                "Score is an ordering device, not an authorisation. Nothing in this "
                "report permits a merge, deploy, migration, provider activation or "
                "spend; those stay behind their own gates."
            ),
        },
        "side_effects": {
            "network_calls": 0,
            "model_calls": 0,
            "tasks_created": 0,
            "tasks_claimed": 0,
            "queue_reordered": False,
            "merges": 0,
            "deploys": 0,
            "spend_usd": 0.0,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a report as a compact Markdown block for a run summary."""
    lines = [
        "## Development cycle",
        "",
        f"Mode: `{report['mode']}` — read-only, no merges, no deploys, no spend.",
        "",
        "| # | Kind | Target | Action | Score | Gate |",
        "|---|------|--------|--------|-------|------|",
    ]
    for item in report.get("next_actions") or []:
        lines.append(
            f"| {item['rank']} | {item['kind']} | `{item['target']}` | {item['action']} "
            f"| {item['score']} | {item['gate']} |"
        )
    if not report.get("next_actions"):
        lines.append("| — | — | — | nothing to do | — | — |")

    lines += ["", "### Why this order", ""]
    for item in report.get("next_actions") or []:
        lines.append(f"- **{item['target']}** — {item['reason']}")

    blocked = report.get("blocked") or []
    if blocked:
        lines += ["", "### Held, not forgotten", ""]
        for item in blocked:
            lines.append(f"- **{item['target']}** — {item['reason']}")

    lines += [
        "",
        f"Deferred: {len(report.get('deferred') or [])} further items. "
        "Scores order work; they do not authorise it.",
    ]
    return "\n".join(lines) + "\n"
