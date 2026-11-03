"""Construct the opt-in live gateway only when every explicit safety gate is open.

This module performs local configuration and environment checks only. Constructing
an adapter never sends a provider request; network I/O begins only on generate().
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from engine.live_adapters import GeminiAdapter, OpenAICompatibleAdapter
from engine.model_gateway import UsageLedger
from engine.d1_monetary_ledger import D1MonetaryLedger
from engine.d1_quota_ledger import D1QuotaLedger
from engine.quota_config import load_provider_profile_registry
from engine.quota_live_gateway import QuotaAwareLiveGateway
from engine.quota_manager import QuotaLedger, QuotaManager
from engine.budget_policy import validate_budget_policy


LIVE_SWITCH = "RAAWAA_ENGINE_LIVE_INFERENCE"


def build_live_gateway(
    db_path: str | Path,
    policy_path: str | Path,
    registry_path: str | Path,
    environ=None,
    invocation_store=None,
) -> QuotaAwareLiveGateway | None:
    """Build an adapter map only after policy, operator, profile, quota and key checks."""
    source = os.environ if environ is None else environ
    if source.get(LIVE_SWITCH, "").strip().lower() != "enabled":
        return None

    with Path(policy_path).open(encoding="utf-8") as stream:
        policy = json.load(stream)
    if not isinstance(policy, dict):
        raise ValueError("engine policy must be a JSON object")
    if policy.get("mode") != "live_inference_opt_in":
        return None
    if policy.get("allow_external_writes") is not False:
        return None
    if validate_budget_policy(policy):
        return None
    # Provider-side spend hard stops are required only for paid routes.
    # This gateway intentionally admits verified-free routes only: the registry
    # loader requires explicit zero input/output pricing, commercial and
    # production permission, current verification, and finite minute/day/month/
    # per-task quotas. Paid fallback remains disabled by budget-policy validation.
    model_policy = policy.get("model_policy", {})
    if model_policy.get("paid_fallback_enabled") is not False:
        return None
    # Cloud runners must use the shared D1 ledger. Never fall back to ephemeral
    # SQLite when the control Worker is configured or when running in GitHub Actions.
    if source.get("RAAWAA_ENGINE_API_URL"):
        control_url = source.get("RAAWAA_ENGINE_API_URL")
        control_token = source.get("RAAWAA_ENGINE_API_TOKEN")
        monetary_ledger = D1MonetaryLedger(base_url=control_url, token=control_token)
        quota_admin_token = source.get("RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN")
        quota_ledger = D1QuotaLedger(base_url=control_url, token=control_token, admin_token=quota_admin_token)
    else:
        if source.get("GITHUB_ACTIONS", "").lower() == "true":
            raise RuntimeError("Cloud live inference requires both durable D1 monetary and token quota ledgers.")
        monetary_ledger = UsageLedger(db_path)
        quota_ledger = QuotaLedger(db_path)

    entries = load_provider_profile_registry(registry_path)
    candidates = [
        entry for entry in entries
        if entry.enabled and entry.ready_for_routing
    ]
    if not candidates:
        return None

    manager = QuotaManager([entry.profile for entry in candidates], quota_ledger)
    adapters = {}
    eligible_profiles = []
    for entry in candidates:
        profile = entry.profile
        # Never resolve, print, or persist key values; only test presence.
        if not source.get(entry.api_key_env, "").strip():
            continue
        if not manager.eligible(profile, estimated_tokens=1):
            continue
        # Select only explicitly supported provider protocols and exact endpoints.
        # Unknown providers/endpoints fail closed rather than being sent to an
        # arbitrary OpenAI-compatible URL from a profile.
        if profile.provider == "groq" and profile.endpoint_url == "https://api.groq.com/openai/v1":
            adapter = OpenAICompatibleAdapter(
                provider=profile.provider,
                base_url=profile.endpoint_url,
                api_key_env=entry.api_key_env,
                environ=source,
            )
        elif (profile.provider == "gemini"
              and profile.endpoint_url == "https://generativelanguage.googleapis.com/v1beta"):
            adapter = GeminiAdapter(api_key_env=entry.api_key_env, environ=source)
        else:
            continue
        adapters[profile.route_id] = adapter
        eligible_profiles.append(profile)

    if not eligible_profiles:
        return None

    # Both gates are rechecked inside QuotaAwareLiveGateway.generate().
    return QuotaAwareLiveGateway(
        manager,
        adapters,
        monetary_ledger,
        network_calls_enabled=True,
        environ=source,
        daily_budget_usd=policy.get("daily_budget_usd", 0),
        monthly_budget_usd=policy.get("monthly_budget_usd", 0),
        per_task_budget_usd=policy.get("per_task_budget_usd", 0),
        invocation_store=invocation_store,
    )
