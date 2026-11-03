"""Central, deny-by-default authorization gate for trusted tool adapters.

Only trusted application code may construct this broker and supply adapters,
audit storage, or an approval verifier. Agent/task payloads never register tools
or provide approval evidence. No adapter is registered by the current worker.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

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


def validate_permission_matrix(matrix: dict[str, Any]) -> list[str]:
    """Validate the static role/task/tool matrix; unknown fields fail closed."""
    errors: list[str] = []
    if not isinstance(matrix, dict) or matrix.get("schema_version") != 1:
        return ["permission matrix must use schema_version 1"]
    if matrix.get("default_effect") != "deny":
        errors.append("permission matrix default_effect must be deny")
    tools = matrix.get("tools")
    if not isinstance(tools, dict) or not tools:
        return errors + ["permission matrix tools must be a non-empty object"]
    for name, spec in tools.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(spec, dict):
            errors.append("tool entries must have non-empty names and object definitions")
            continue
        if spec.get("access") not in {"read", "write"}:
            errors.append(f"{name}: access must be read or write")
        if not isinstance(spec.get("action"), str) or not spec["action"].strip():
            errors.append(f"{name}: action is required")
        for field in ("agent_roles", "task_types", "environments"):
            values = spec.get(field)
            if (not isinstance(values, list) or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(values) != len(set(values))):
                errors.append(f"{name}: {field} must be a non-empty unique string list")
        if isinstance(spec.get("environments"), list) and not set(spec["environments"]).issubset(
            {"local", "staging", "production"}
        ):
            errors.append(f"{name}: unsupported environment")
        if spec.get("access") == "write" and "production" in spec.get("environments", []):
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
        audit_sink: AuditSink,
        approval_verifier: ApprovalVerifier | None = None,
    ):
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        errors = validate_permission_matrix(permission_matrix)
        if errors:
            raise ValueError("; ".join(errors))
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
        return (
            type(cost) in (int, float)
            and math.isfinite(cost)
            and cost >= 0
        )

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

    def authorize(self, request: ToolRequest) -> ToolDecision:
        """Return a decision and persist it before any adapter can execute."""
        if not isinstance(request, ToolRequest) or not self._valid_request(request):
            return ToolDecision(False, False, "invalid_tool_request")
        spec = self._matrix["tools"].get(request.tool_name)
        if not isinstance(spec, dict):
            return ToolDecision(False, False, "tool_not_allowlisted")
        if request.tool_name not in self._adapters:
            return ToolDecision(False, False, "trusted_adapter_not_registered")
        if request.action != spec.get("action"):
            return ToolDecision(False, False, "tool_action_mismatch")
        if request.agent_role not in spec.get("agent_roles", []):
            return ToolDecision(False, False, "agent_role_not_authorized")
        if request.task_type not in spec.get("task_types", []):
            return ToolDecision(False, False, "task_type_not_authorized")
        if request.environment not in spec.get("environments", []):
            return ToolDecision(False, False, "environment_not_authorized")

        access = spec.get("access")
        if access == "read":
            policy_decision = evaluate_action(
                request.action, self._policy, request.estimated_cost_usd
            )
            if not policy_decision.allowed:
                return ToolDecision(
                    False, policy_decision.requires_approval, policy_decision.reason
                )
        elif access == "write":
            if self._policy.get("allow_external_writes") is not True:
                return ToolDecision(False, False, "external_writes_disabled")
            approval_actions = self._policy.get("require_human_approval_for", [])
            if request.action not in approval_actions:
                return ToolDecision(False, False, "write_action_not_approval_gated")
            if not request.approval_id or not request.idempotency_key:
                return ToolDecision(True, True, "human_approval_and_idempotency_required")
            if self._approval_verifier is None:
                return ToolDecision(False, True, "trusted_approval_verifier_unavailable")
            try:
                evidence = self._approval_verifier(request)
            except Exception:
                return ToolDecision(False, True, "approval_verification_failed")
            expected = (
                request.approval_id, request.task_id, request.agent_id,
                request.agent_role, request.tool_name, request.action,
                request.environment,
            )
            actual = None if evidence is None else (
                evidence.approval_id, evidence.task_id, evidence.agent_id,
                evidence.agent_role, evidence.tool_name, evidence.action,
                evidence.environment,
            )
            if actual != expected:
                return ToolDecision(False, True, "approval_evidence_mismatch")
        else:
            return ToolDecision(False, False, "unknown_access_class")

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
