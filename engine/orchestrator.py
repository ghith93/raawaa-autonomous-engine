"""Safe DAG workflow planner. It validates and plans work; it does not call agents or providers."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

SAFE_TASK_TYPES = {"research_brief", "code_review", "content_draft", "system_audit"}
ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class WorkflowValidationError(ValueError):
    """Raised when a workflow is invalid or unsafe."""


@dataclass(frozen=True)
class WorkflowPlan:
    workflow_id: str
    order: tuple[str, ...]
    ready: tuple[str, ...]
    waiting: dict[str, tuple[str, ...]]
    assignments: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "mode": "plan_only",
            "order": list(self.order),
            "ready": list(self.ready),
            "waiting": {key: list(value) for key, value in self.waiting.items()},
            "assignments": dict(self.assignments),
            "execution_started": False,
            "provider_calls": 0,
            "external_writes": 0,
        }


def validate_and_plan(workflow: dict[str, Any], agent_roster: dict[str, Any]) -> WorkflowPlan:
    """Validate a declarative DAG and return a deterministic topological plan."""
    workflow_id = workflow.get("id")
    steps = workflow.get("steps")
    if not isinstance(workflow_id, str) or not ID_RE.fullmatch(workflow_id):
        raise WorkflowValidationError("workflow id must be a short stable identifier")
    if not isinstance(steps, list) or not steps:
        raise WorkflowValidationError("workflow must contain at least one step")

    agents = {
        item.get("id"): item for item in agent_roster.get("agents", [])
        if isinstance(item, dict) and item.get("enabled") is True
    }
    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            raise WorkflowValidationError("each step must be an object")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not ID_RE.fullmatch(step_id):
            raise WorkflowValidationError("each step needs a valid stable id")
        if step_id in by_id:
            raise WorkflowValidationError(f"duplicate step id: {step_id}")
        if step.get("task_type") not in SAFE_TASK_TYPES:
            raise WorkflowValidationError(f"task type is not allowlisted: {step.get('task_type')}")
        agent_id = step.get("agent")
        if agent_id not in agents:
            raise WorkflowValidationError(f"unknown or disabled agent: {agent_id}")
        deps = step.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise WorkflowValidationError(f"depends_on must be a list of step ids: {step_id}")
        if len(deps) != len(set(deps)) or step_id in deps:
            raise WorkflowValidationError(f"duplicate or self dependency: {step_id}")
        if not isinstance(step.get("title"), str) or not step["title"].strip():
            raise WorkflowValidationError(f"step title is required: {step_id}")
        by_id[step_id] = step

    indegree = {step_id: 0 for step_id in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for step_id, step in by_id.items():
        for dep in step.get("depends_on", []):
            if dep not in by_id:
                raise WorkflowValidationError(f"unknown dependency {dep} in step {step_id}")
            indegree[step_id] += 1
            children[dep].append(step_id)

    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(by_id):
        raise WorkflowValidationError("workflow contains a dependency cycle")

    ready = tuple(step_id for step_id in order if not by_id[step_id].get("depends_on", []))
    waiting = {
        step_id: tuple(by_id[step_id].get("depends_on", []))
        for step_id in order if by_id[step_id].get("depends_on", [])
    }
    assignments = {step_id: by_id[step_id]["agent"] for step_id in order}
    return WorkflowPlan(workflow_id, tuple(order), ready, waiting, assignments)
