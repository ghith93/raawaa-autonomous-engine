"""Central, deny-by-default authorization gate for trusted tool adapters.

Only trusted application code may construct this broker and supply adapters,
audit storage, or an approval verifier. Agent/task payloads never register tools
or provide approval evidence. No adapter is registered by the current worker.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from engine.agent_runtime import TASK_AGENT_CONTRACTS
from engine.policy import evaluate_action


@dataclass(frozen=True)
class ToolRequest:
    task_id: str
    agent_id: str
    agent_role: str
    task_type: str
    tool_name: str
    action: str
    environment: str
    payload: dict[str, Any]
    estimated_cost_usd: float = 0.0
    approval_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ApprovalEvidence:
    """Evidence returned only by a trusted, atomic approval verifier."""

    approval_id: str
    task_id: str
    agent_id: str
    agent_role: str
    tool_name: str
    action: str
    environment: str


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    requires_approval: bool
    reason: str


class ToolBrokerDenied(PermissionError):
    """A tool call was denied before its adapter was invoked."""


class ToolBrokerAuditError(RuntimeError):
    """The audit sink failed; execution outcome may be ambiguous."""


ApprovalVerifier = Callable[[ToolRequest], ApprovalEvidence | None]
AuditSink = Callable[[dict[str, Any]], None]
ToolAdapter = Callable[[ToolRequest], Any]


def validate_permission_matrix(
    matrix: dict[str, Any], roster: dict[str, Any] | None = None
) -> list[str]:
    """Validate the static role/task/tool matrix; unknown grants fail closed."""
    errors: list[str] = []
    if not isinstance(matrix, dict) or matrix.get("schema_version") != 1:
        return ["permission matrix must use schema_version 1"]
    if matrix.get("default_effect") != "deny":
        errors.append("permission matrix default_effect must be deny")
    tools = matrix.get("tools")
    if not isinstance(tools, dict) or not tools:
        return errors + ["permission matrix tools must be a non-empty object"]

    agents = roster.get("agents", []) if isinstance(roster, dict) else None
    agent_by_id = {
        item.get("id"): item for item in agents
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if isinstance(agents, list) else {}
    if roster is not None and not agent_by_id:
        errors.append("agent roster is unavailable for permission validation")

    for name, spec in tools.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(spec, dict):
            errors.append("tool entries must have non-empty names and object definitions")
            continue
        if spec.get("access") not in {"read", "write"}:
            errors.append(f"{name}: access must be read or write")
        if not isinstance(spec.get("action"), str) or not spec["action"].strip():
            errors.append(f"{name}: action is required")
        environments = spec.get("environments")
        if (not isinstance(environments, list) or not environments
                or any(not isinstance(value, str) or not value.strip() for value in environments)
                or len(environments) != len(set(environments))):
            errors.append(f"{name}: environments must be a non-empty unique string list")
        elif not set(environments).issubset({"local", "staging", "production"}):
            errors.append(f"{name}: unsupported environment")

        grants = spec.get("grants")
        if not isinstance(grants, list) or not grants:
            errors.append(f"{name}: grants must be a non-empty list")
            continue
        seen: set[tuple[str, str]] = set()
        for grant in grants:
            if not isinstance(grant, dict):
                errors.append(f"{name}: each grant must be an object")
                continue
            role = grant.get("agent_role")
            task_types = grant.get("task_types")
            if not isinstance(role, str) or not role.strip():
                errors.append(f"{name}: grant agent_role is required")
                continue
            if (not isinstance(task_types, list) or not task_types
                    or any(not isinstance(task, str) or not task.strip() for task in task_types)
                    or len(task_types) != len(set(task_types))):
                errors.append(f"{name}: grant task_types must be a non-empty unique string list")
                continue
            if roster is not None:
                matching_agents = [
                    agent for agent in agent_by_id.values() if agent.get("role") == role
                ]
                if not matching_agents:
                    errors.append(f"{name}: grant references unknown agent role {role}")
                for task_type in task_types:
                    assignment = TASK_AGENT_CONTRACTS.get(task_type)
                    assigned_agent = agent_by_id.get(assignment[0]) if assignment else None
                    if not assigned_agent or assigned_agent.get("role") != role:
                        errors.append(
                            f"{name}: role {role} is not assigned to task type {task_type}"
                        )
            for task_type in task_types:
                key = (role, task_type)
                if key in seen:
                    errors.append(f"{name}: duplicate role/task grant {role}/{task_type}")
                seen.add(key)

        if spec.get("access") == "write" and "production" in environments:
            errors.append(f"{name}: production write tools are not permitted in this matrix")
    return errors


class ToolBroker:
    """Authorize and dispatch tools through one trusted adapter registry.

    The broker does not import, download, or execute skill code. Adapters must be
    registered by trusted host code. Every decision is durably audited before an
    adapter runs. A write requires an external-write policy gate, a one-time
    approval verified by trusted code, and an idempotency key.
    """

    def __init__(
        self,
        policy: dict[str, Any],
        permission_matrix: dict[str, Any],
        adapters: Mapping[str, ToolAdapter],
        *,
        agent_roster: dict[str, Any],
        audit_sink: AuditSink,
        approval_verifier: ApprovalVerifier | None = None,
    ):
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        errors = validate_permission_matrix(permission_matrix, agent_roster)
        if errors:
            raise ValueError("; ".join(errors))
        agents = agent_roster.get("agents") if isinstance(agent_roster, dict) else None
        if not isinstance(agents, list):
            raise ValueError("trusted agent roster is required")
        self._agent_by_id = {
            item.get("id"): item for item in agents
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if not isinstance(adapters, Mapping):
            raise ValueError("trusted adapter registry must be a mapping")
        if not callable(audit_sink):
            raise ValueError("a durable audit sink is required")
        if approval_verifier is not None and not callable(approval_verifier):
            raise ValueError("approval verifier must be callable")
        self._policy = policy
        self._matrix = permission_matrix
        self._adapters = dict(adapters)
        self._audit_sink = audit_sink
        self._approval_verifier = approval_verifier

    @staticmethod
    def _valid_request(request: ToolRequest) -> bool:
        required = (
            request.task_id, request.agent_id, request.agent_role,
            request.task_type, request.tool_name, request.action,
            request.environment,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            return False
        if request.environment not in {"local", "staging", "production"}:
            return False
        if not isinstance(request.payload, dict):
            return False
        cost = request.estimated_cost_usd
        return type(cost) in (int, float) and math.isfinite(cost) and cost >= 0

    def _audit(self, event: str, request: ToolRequest, reason: str) -> bool:
        record = {
            "event": event,
            "task_id": request.task_id,
            "agent_id": request.agent_id,
            "agent_role": request.agent_role,
            "task_type": request.task_type,
            "tool_name": request.tool_name,
            "action": request.action,
            "environment": request.environment,
            "reason": reason,
        }
        try:
            self._audit_sink(record)
            return True
        except Exception:
            return False

    def _deny(self, request: ToolRequest, reason: str, requires_approval: bool = False) -> ToolDecision:
        if not self._audit("tool_call_denied", request, reason):
            return ToolDecision(False, False, "audit_sink_unavailable")
        return ToolDecision(False, requires_approval, reason)

    def authorize(self, request: ToolRequest) -> ToolDecision:
        """Return a decision and persist it before any adapter can execute."""
        if not isinstance(request, ToolRequest) or not self._valid_request(request):
            return ToolDecision(False, False, "invalid_tool_request")
        spec = self._matrix["tools"].get(request.tool_name)
        if not isinstance(spec, dict):
            return self._deny(request, "tool_not_allowlisted")
        if request.tool_name not in self._adapters:
            return self._deny(request, "trusted_adapter_not_registered")
        agent = self._agent_by_id.get(request.agent_id)
        if not agent or agent.get("enabled") is not True or agent.get("role") != request.agent_role:
            return self._deny(request, "agent_identity_not_authorized")
        assignment = TASK_AGENT_CONTRACTS.get(request.task_type)
        if not assignment or assignment[0] != request.agent_id:
            return self._deny(request, "task_agent_assignment_mismatch")
        if assignment[1] not in agent.get("capabilities", []):
            return self._deny(request, "agent_capability_missing")
        if request.action != spec.get("action"):
            return self._deny(request, "tool_action_mismatch")
        grants = spec.get("grants", [])
        if not any(
            isinstance(grant, dict)
            and grant.get("agent_role") == request.agent_role
            and request.task_type in grant.get("task_types", [])
            for grant in grants
        ):
            return self._deny(request, "role_task_not_authorized")
        if request.environment not in spec.get("environments", []):
            return self._deny(request, "environment_not_authorized")

        access = spec.get("access")
        if access == "read":
            policy_decision = evaluate_action(
                request.action, self._policy, request.estimated_cost_usd
            )
            if not policy_decision.allowed:
                return self._deny(request, policy_decision.reason, policy_decision.requires_approval)
        elif access == "write":
            if self._policy.get("allow_external_writes") is not True:
                return self._deny(request, "external_writes_disabled")
            approval_actions = self._policy.get("require_human_approval_for", [])
            if request.action not in approval_actions:
                return self._deny(request, "write_action_not_approval_gated")
            if request.estimated_cost_usd > 0:
                return self._deny(request, "tool_cost_requires_atomic_budget_reservation")
            if not request.approval_id or not request.idempotency_key:
                return self._deny(request, "human_approval_and_idempotency_required", True)
            if self._approval_verifier is None:
                return self._deny(request, "trusted_approval_verifier_unavailable", True)
            try:
                evidence = self._approval_verifier(request)
            except Exception:
                return self._deny(request, "approval_verification_failed", True)
            expected = (
                request.approval_id, request.task_id, request.agent_id,
                request.agent_role, request.tool_name, request.action,
                request.environment,
            )
            actual = None if not isinstance(evidence, ApprovalEvidence) else (
                evidence.approval_id, evidence.task_id, evidence.agent_id,
                evidence.agent_role, evidence.tool_name, evidence.action,
                evidence.environment,
            )
            if actual != expected:
                return self._deny(request, "approval_evidence_mismatch", True)
        else:
            return self._deny(request, "unknown_access_class")

        if not self._audit("tool_call_authorized", request, "authorized"):
            return ToolDecision(False, False, "audit_sink_unavailable")
        return ToolDecision(True, False, "authorized")

    def execute(self, request: ToolRequest) -> Any:
        """Execute only a trusted, registered adapter after successful authorization."""
        decision = self.authorize(request)
        if not decision.allowed:
            if decision.requires_approval:
                raise ToolBrokerDenied("approval_required:" + decision.reason)
            raise ToolBrokerDenied(decision.reason)
        adapter = self._adapters[request.tool_name]
        try:
            result = adapter(request)
        except Exception as exc:
            if not self._audit("tool_call_failed", request, type(exc).__name__):
                raise ToolBrokerAuditError(
                    "adapter failed and failure audit could not be persisted"
                ) from exc
            raise
        if not self._audit("tool_call_completed", request, "completed"):
            raise ToolBrokerAuditError(
                "adapter may have completed; completion audit failed; do not replay automatically"
            )
        return result
