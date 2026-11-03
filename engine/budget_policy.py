"""Validate the repository's strict monthly cost envelope.

The $15 ceiling is an all-in ceiling. Only $12 is currently allocatable to
model usage; infrastructure overages are disabled and the remaining $3 is
unallocated reserve, not permission to spend.
"""
from __future__ import annotations

import math
from typing import Any


def _finite_nonnegative(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_budget_policy(policy: dict[str, Any]) -> list[str]:
    """Return policy violations; callers must fail closed when this is non-empty."""
    errors: list[str] = []
    envelope = policy.get("budget_envelope")
    model = policy.get("model_policy")
    if not isinstance(envelope, dict) or not isinstance(model, dict):
        return ["budget_envelope and model_policy must be objects"]

    amounts = {
        "monthly_total_hard_cap_usd": envelope.get("monthly_total_hard_cap_usd"),
        "model_monthly_hard_cap_usd": envelope.get("model_monthly_hard_cap_usd"),
        "infrastructure_monthly_hard_cap_usd": envelope.get("infrastructure_monthly_hard_cap_usd"),
        "unallocated_reserve_usd": envelope.get("unallocated_reserve_usd"),
        "daily_model_hard_cap_usd": envelope.get("daily_model_hard_cap_usd"),
        "per_task_model_hard_cap_usd": envelope.get("per_task_model_hard_cap_usd"),
        "paid_fallback_monthly_cap_usd": model.get("paid_fallback_monthly_cap_usd"),
        "daily_budget_usd": policy.get("daily_budget_usd"),
        "monthly_budget_usd": policy.get("monthly_budget_usd"),
        "per_task_budget_usd": policy.get("per_task_budget_usd"),
    }
    for name, value in amounts.items():
        if not _finite_nonnegative(value):
            errors.append(f"{name} must be a finite non-negative number")

    if errors:
        return errors

    total = envelope["monthly_total_hard_cap_usd"]
    model_cap = envelope["model_monthly_hard_cap_usd"]
    infra_cap = envelope["infrastructure_monthly_hard_cap_usd"]
    reserve = envelope["unallocated_reserve_usd"]
    daily = envelope["daily_model_hard_cap_usd"]
    task = envelope["per_task_model_hard_cap_usd"]

    if total != 15.0:
        errors.append("monthly_total_hard_cap_usd must remain exactly 15.00 USD")
    if model_cap > total or infra_cap > total or reserve > total:
        errors.append("budget allocations cannot exceed the all-in monthly hard cap")
    if round(model_cap + infra_cap + reserve, 2) != round(total, 2):
        errors.append("model, infrastructure, and unallocated reserve must sum to the all-in cap")
    if infra_cap != 0:
        errors.append("infrastructure paid overages must remain zero until an external hard stop is verified")
    if policy["monthly_budget_usd"] != model_cap:
        errors.append("monthly_budget_usd must equal the model monthly hard cap")
    if policy["daily_budget_usd"] != daily or daily > model_cap:
        errors.append("daily_budget_usd must equal the daily model cap and not exceed the monthly model cap")
    if policy["per_task_budget_usd"] != task or task > daily:
        errors.append("per_task_budget_usd must equal the per-task cap and not exceed the daily model cap")
    if model["strategy"] != "hybrid":
        errors.append("model strategy must be hybrid")
    if model.get("prefer_free_first") is not True:
        errors.append("hybrid routing must prefer verified-free providers first")
    if model.get("routing_order") != ["verified_free", "paid_fallback"]:
        errors.append("hybrid routing order must be verified_free then paid_fallback")
    if model.get("paid_fallback_enabled") is not False:
        errors.append("paid fallback remains disabled until a provider-side hard cap is verified")
    if model.get("paid_fallback_requires_provider_hard_cap") is not True:
        errors.append("paid fallback must require a provider-enforced hard cap")
    if model.get("paid_fallback_requires_prepaid_or_provider_enforced_cap") is not True:
        errors.append("paid fallback must require prepaid balance or provider-enforced spend blocking")
    if model.get("no_automatic_plan_upgrade") is not True:
        errors.append("automatic provider plan upgrades must remain disabled")
    if policy.get("allow_paid_model_calls") is not False:
        errors.append("paid model calls must remain disabled until the paid fallback gate is satisfied")
    if envelope.get("currency") != "USD":
        errors.append("budget currency must be USD")
    if envelope.get("stop_on_unknown_cost") is not True:
        errors.append("unknown provider costs must stop execution")
    if envelope.get("stop_on_unsettled_reservation") is not True:
        errors.append("unsettled monetary reservations must block further spending")
    if envelope.get("require_atomic_pre_call_reservation") is not True:
        errors.append("atomic pre-call monetary reservations are mandatory")
    cloudflare = envelope.get("cloudflare")
    actions = envelope.get("github_actions")
    if not isinstance(cloudflare, dict) or cloudflare.get("plan") != "free_only" or cloudflare.get("paid_overages_allowed") is not False:
        errors.append("Cloudflare must remain Free-plan-only with paid overages disabled")
    if not isinstance(actions, dict) or actions.get("usage") != "included_allowance_only" or actions.get("paid_overages_allowed") is not False:
        errors.append("GitHub Actions must remain within included allowance with paid overages disabled")
    return errors
