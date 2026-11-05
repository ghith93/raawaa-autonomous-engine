"""Safe DAG workflow planner with deterministic specialist assignment.

This component validates proposals; it does not enqueue, claim, execute, or call models.
An optional LLM planner may draft a workflow, but this module is the trusted gate.
"""
from __future__ import annotations

import json
import re
import heapq
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from engine.agent_runtime import AgentContractError, build_task_contract

SAFE_TASK_TYPES = {"research_brief", "code_review", "content_draft", "system_audit"}
ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
DEFAULT_POLICY = {
    "max_parallel_agents": 2,
    "per_task_budget_usd": 0,
    "monthly_budget_usd": 0,
    "allow_paid_model_calls": False,
    "mode": "safe_dry_run",
    "allow_external_writes": False,
}


class WorkflowValidationError(ValueError):
    """Raised when a workflow is invalid or unsafe."""


@dataclass(frozen=True)
class WorkflowPlan:
    workflow_id: str
    order: tuple[str, ...]
    ready: tuple[str, ...]
    waiting: dict[str, tuple[str, ...]]
    assignments: dict[str, str]
    steps: dict[str, dict[str, Any]]
    execution_waves: tuple[tuple[str, ...], ...]
    dispatch_blockers: tuple[str, ...]
    estimated_model_cost_usd: float
    human_approval_required: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "mode": "plan_only",
            "order": list(self.order),
            "ready": list(self.ready),
            "waiting": {key: list(value) for key, value in self.waiting.items()},
            "assignments": dict(self.assignments),
            "steps": {key: dict(value) for key, value in self.steps.items()},
            "execution_waves": [list(wave) for wave in self.execution_waves],
            "dispatch_blockers": list(self.dispatch_blockers),
            "estimated_model_cost_usd": self.estimated_model_cost_usd,
            "human_approval_required": self.human_approval_required,
            "dispatch_authorized": False,
            "execution_started": False,
            "provider_calls": 0,
            "external_writes": 0,
        }


def _cost(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise WorkflowValidationError(f"{field} must be a non-negative number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WorkflowValidationError(f"{field} must be a non-negative number") from exc
    if not amount.is_finite() or amount < 0:
        raise WorkflowValidationError(f"{field} must be finite and non-negative")
    return amount


def validate_and_plan(
    workflow: dict[str, Any],
    agent_roster: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> WorkflowPlan:
    """Validate a bounded DAG, assign specialists from trusted mappings, and return plan-only waves."""
    if not isinstance(workflow, dict):
        raise WorkflowValidationError("workflow must be an object")
    if not isinstance(agent_roster, dict) or not isinstance(agent_roster.get("agents"), list):
        raise WorkflowValidationError("agent roster must contain an agents list")
    if policy is not None and not isinstance(policy, dict):
        raise WorkflowValidationError("policy must be an object")
    policy = {**DEFAULT_POLICY, **(policy or {})}
    workflow_id = workflow.get("id")
    steps = workflow.get("steps")
    if not isinstance(workflow_id, str) or not ID_RE.fullmatch(workflow_id):
        raise WorkflowValidationError("workflow id must be a short stable identifier")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise WorkflowValidationError("workflow must contain between 1 and 20 steps")

    max_parallel = policy.get("max_parallel_agents")
    if type(max_parallel) is not int or not 1 <= max_parallel <= 5:
        raise WorkflowValidationError("max_parallel_agents must be an integer between 1 and 5")
    per_task_cap = _cost(policy.get("per_task_budget_usd", 0), "per_task_budget_usd")
    monthly_cap = _cost(policy.get("monthly_budget_usd", 0), "monthly_budget_usd")
    paid_calls_enabled = policy.get("allow_paid_model_calls") is True

    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            raise WorkflowValidationError("each step must be an object")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not ID_RE.fullmatch(step_id):
            raise WorkflowValidationError("each step needs a valid stable id")
        if step_id in by_id:
            raise WorkflowValidationError(f"duplicate step id: {step_id}")
        task_type = step.get("task_type")
        if not isinstance(task_type, str) or task_type not in SAFE_TASK_TYPES:
            raise WorkflowValidationError(f"task type is not allowlisted: {task_type}")
        title = step.get("title")
        if not isinstance(title, str) or not title.strip():
            raise WorkflowValidationError(f"step title is required: {step_id}")
        payload = step.get("payload", {})
        if not isinstance(payload, dict):
            raise WorkflowValidationError(f"step payload must be an object: {step_id}")
        checks = step.get("acceptance_checks")
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(check, str) or not check.strip() for check in checks)):
            raise WorkflowValidationError(f"step needs at least one acceptance check: {step_id}")
        deps = step.get("depends_on", [])
        if (not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps)
                or len(deps) != len(set(deps))):
            raise WorkflowValidationError(f"depends_on must be a unique list of step ids: {step_id}")
        if step_id in deps:
            raise WorkflowValidationError(f"step cannot depend on itself: {step_id}")
        risk = step.get("risk", "low")
        if not isinstance(risk, str) or risk not in {"low", "medium", "high", "critical"}:
            raise WorkflowValidationError(f"invalid risk level for step: {step_id}")
        priority = step.get("priority", 50)
        if type(priority) is not int or not 0 <= priority <= 100:
            raise WorkflowValidationError(f"priority must be an integer between 0 and 100: {step_id}")
        estimate = _cost(step.get("estimated_model_cost_usd", 0), f"{step_id}.estimated_model_cost_usd")
        if estimate > per_task_cap:
            raise WorkflowValidationError(f"step {step_id} exceeds the per-task model budget")

        try:
            contract = build_task_contract(
                {"task_type": task_type, "title": title, "payload_json": json.dumps(payload)},
                agent_roster,
            )
        except AgentContractError as exc:
            raise WorkflowValidationError(f"step {step_id}: {exc}") from exc
        supplied_agent = step.get("agent")
        if supplied_agent is not None and supplied_agent != contract["agent_id"]:
            raise WorkflowValidationError(
                f"step {step_id}: requested agent {supplied_agent} does not match "
                f"required specialist {contract['agent_id']}"
            )

        by_id[step_id] = {
            **step,
            "task_type": task_type,
            "title": title.strip(),
            "payload": payload,
            "depends_on": list(deps),
            "assigned_agent": contract["agent_id"],
            "assigned_agent_role": contract["agent_role"],
            "required_capability": contract["required_capability"],
            "risk": risk,
            "priority": priority,
            "estimated_model_cost_usd": estimate,
        }

    indegree = {step_id: 0 for step_id in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for step_id, step in by_id.items():
        for dep in step["depends_on"]:
            if dep not in by_id:
                raise WorkflowValidationError(f"unknown dependency {dep} in step {step_id}")
            indegree[step_id] += 1
            children[dep].append(step_id)

    queue = [(-by_id[key]["priority"], key) for key, degree in indegree.items() if degree == 0]
    heapq.heapify(queue)
    order: list[str] = []
    depth: dict[str, int] = {}
    while queue:
        _, current = heapq.heappop(queue)
        order.append(current)
        for child in sorted(children[current]):
            depth[child] = max(depth.get(child, 0), depth.get(current, 0) + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(queue, (-by_id[child]["priority"], child))
    if len(order) != len(by_id):
        raise WorkflowValidationError("workflow contains a dependency cycle")

    ready = tuple(step_id for step_id in order if not by_id[step_id]["depends_on"])
    waiting = {
        step_id: tuple(by_id[step_id]["depends_on"])
        for step_id in order if by_id[step_id]["depends_on"]
    }
    assignments = {step_id: by_id[step_id]["assigned_agent"] for step_id in order}

    raw_waves: dict[int, list[str]] = defaultdict(list)
    for step_id in order:
        raw_waves[depth.get(step_id, 0)].append(step_id)
    execution_waves: list[tuple[str, ...]] = []
    for wave_depth in sorted(raw_waves):
        wave = sorted(raw_waves[wave_depth], key=lambda step_id: (-by_id[step_id]["priority"], step_id))
        for start in range(0, len(wave), max_parallel):
            execution_waves.append(tuple(wave[start:start + max_parallel]))

    total_estimate = sum((by_id[step_id]["estimated_model_cost_usd"] for step_id in order), Decimal("0"))
    blockers = []
    if total_estimate > monthly_cap:
        blockers.append("workflow_estimated_cost_exceeds_monthly_budget")
    if total_estimate > 0 and not paid_calls_enabled:
        blockers.append("paid_model_calls_disabled")
    if policy.get("mode") == "safe_dry_run":
        blockers.append("safe_dry_run_mode")
    if policy.get("allow_external_writes") is not False:
        blockers.append("external_write_policy_not_fail_closed")

    return WorkflowPlan(
        workflow_id=workflow_id,
        order=tuple(order),
        ready=ready,
        waiting=waiting,
        assignments=assignments,
        steps={step_id: {
            "task_type": by_id[step_id]["task_type"],
            "title": by_id[step_id]["title"],
            "assigned_agent": by_id[step_id]["assigned_agent"],
            "assigned_agent_role": by_id[step_id]["assigned_agent_role"],
            "required_capability": by_id[step_id]["required_capability"],
            "depends_on": list(by_id[step_id]["depends_on"]),
            "acceptance_checks": [check.strip() for check in by_id[step_id]["acceptance_checks"]],
            "risk": by_id[step_id]["risk"],
            "priority": by_id[step_id]["priority"],
            "estimated_model_cost_usd": float(by_id[step_id]["estimated_model_cost_usd"]),
        } for step_id in order},
        execution_waves=tuple(execution_waves),
        dispatch_blockers=tuple(blockers),
        estimated_model_cost_usd=float(total_estimate),
        human_approval_required=any(by_id[step_id]["risk"] in {"high", "critical"} for step_id in order),
    )
