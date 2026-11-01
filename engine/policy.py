"""Conservative policy checks for actions proposed by agents."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    allowed: bool
    requires_approval: bool
    reason: str


def evaluate_action(action: str, policy: dict[str, Any], estimated_cost_usd: float = 0.0) -> Decision:
    """Evaluate an action without executing it. Unknown actions fail closed."""
    if estimated_cost_usd < 0:
        return Decision(False, False, "Estimated cost cannot be negative.")

    budget = float(policy.get("daily_budget_usd", 0))
    if estimated_cost_usd > budget:
        return Decision(False, False, "Estimated cost exceeds the configured daily budget.")

    if estimated_cost_usd > 0 and not policy.get("allow_paid_model_calls", False):
        return Decision(False, False, "Paid model calls are disabled.")

    approval_actions = set(policy.get("require_human_approval_for", []))
    if action in approval_actions:
        return Decision(False, True, "Human approval is required before this action.")

    if action not in {"plan", "research", "summarize", "inspect_code", "run_tests", "draft_content", "report"}:
        return Decision(False, False, "Action is not on the safe allowlist.")

    if action in {"publish_content", "send_messages", "spend_money", "change_prices", "deploy_production", "delete_data", "change_access", "modify_storefront"} and not policy.get("allow_external_writes", False):
        return Decision(False, True, "External writes are disabled; explicit approval and policy change are required.")

    return Decision(True, False, "Allowed by the current safe policy.")
