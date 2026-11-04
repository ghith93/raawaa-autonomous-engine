"""Deterministic, side-effect-free workflow run manifest construction.

This is a pre-dispatch contract: it binds a validated plan to a stable run identity
and derives idempotency keys, but does not persist, enqueue, claim, or execute tasks.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_STEPS = 20


class WorkflowManifestError(ValueError):
    """Raised when a plan cannot be safely bound to a workflow run identity."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise WorkflowManifestError(f"{field} must be a stable identifier")
    return value


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for cross-run hashing and replay checks."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowManifestError("plan must contain finite JSON values only") from exc


@dataclass(frozen=True)
class WorkflowRunManifest:
    workflow_id: str
    run_id: str
    plan_version: int
    plan_digest: str
    plan_json: str
    root_step_ids: tuple[str, ...]
    steps: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "plan_version": self.plan_version,
            "plan_digest": self.plan_digest,
            "plan": json.loads(self.plan_json),
            "root_step_ids": list(self.root_step_ids),
            "steps": [dict(step) for step in self.steps],
            "dispatch_authorized": False,
            "execution_started": False,
        }


def build_workflow_run_manifest(
    plan: dict[str, Any], run_id: str, plan_version: int = 1,
) -> WorkflowRunManifest:
    """Bind a trusted plan-only result to stable run and step identities.

    The planner output is rechecked here because this function is also a public
    CLI boundary. Unknown or inconsistent dependency/order data fails closed.
    """
    if not isinstance(plan, dict):
        raise WorkflowManifestError("plan must be an object")
    workflow_id = _identifier(plan.get("workflow_id"), "workflow_id")
    run_id = _identifier(run_id, "run_id")
    if type(plan_version) is not int or plan_version < 1:
        raise WorkflowManifestError("plan_version must be a positive integer")
    if plan.get("mode") != "plan_only":
        raise WorkflowManifestError("only plan_only output can become a manifest")
    if (plan.get("dispatch_authorized") is not False
            or plan.get("execution_started") is not False
            or plan.get("provider_calls") != 0
            or plan.get("external_writes") != 0):
        raise WorkflowManifestError("plan must remain non-dispatching and side-effect-free")

    raw_steps = plan.get("steps")
    order = plan.get("order")
    if not isinstance(raw_steps, dict) or not 1 <= len(raw_steps) <= _MAX_STEPS:
        raise WorkflowManifestError("plan must contain between 1 and 20 steps")
    if (not isinstance(order, list) or len(order) != len(raw_steps)
            or any(not isinstance(item, str) for item in order)
            or len(order) != len(set(order)) or set(order) != set(raw_steps)):
        raise WorkflowManifestError("plan order must contain every step exactly once")

    seen: set[str] = set()
    normalized_steps: list[dict[str, Any]] = []
    roots: list[str] = []
    for step_id in order:
        _identifier(step_id, "step_id")
        step = raw_steps.get(step_id)
        if not isinstance(step, dict):
            raise WorkflowManifestError(f"step {step_id} must be an object")
        dependencies = step.get("depends_on")
        if (not isinstance(dependencies, list)
                or any(not isinstance(dep, str) for dep in dependencies)
                or len(dependencies) != len(set(dependencies))):
            raise WorkflowManifestError(f"step {step_id} has invalid dependencies")
        if (step.get("task_type") not in {"research_brief", "code_review", "content_draft", "system_audit"}
                or not isinstance(step.get("title"), str) or not step["title"].strip()
                or not isinstance(step.get("assigned_agent"), str) or not step["assigned_agent"].strip()
                or type(step.get("priority", 50)) is not int
                or not 0 <= step.get("priority", 50) <= 100):
            raise WorkflowManifestError(f"step {step_id} has invalid execution metadata")
        if any(dep not in raw_steps or dep not in seen for dep in dependencies):
            raise WorkflowManifestError(f"step {step_id} has a missing or forward dependency")
        if not dependencies:
            roots.append(step_id)
        key_material = f"{workflow_id}\0{run_id}\0{plan_version}\0{step_id}".encode("utf-8")
        task_key = "workflow-step:" + hashlib.sha256(key_material).hexdigest()
        normalized_steps.append({
            "step_id": step_id,
            "task_type": step.get("task_type"),
            "title": step.get("title"),
            "assigned_agent": step.get("assigned_agent"),
            "priority": step.get("priority", 50),
            "depends_on": list(dependencies),
            "task_idempotency_key": task_key,
        })
        seen.add(step_id)

    plan_json = canonical_json(plan)
    digest = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
    return WorkflowRunManifest(
        workflow_id=workflow_id,
        run_id=run_id,
        plan_version=plan_version,
        plan_digest=digest,
        plan_json=plan_json,
        root_step_ids=tuple(roots),
        steps=tuple(normalized_steps),
    )
