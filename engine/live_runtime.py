"""Construct the opt-in live gateway only when every explicit safety gate is open.

This module performs local configuration and environment checks only. Constructing
an adapter never sends a provider request; network I/O begins only on generate().
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from engine.live_adapters import OpenAICompatibleAdapter
from engine.model_gateway import UsageLedger
from engine.quota_config import load_provider_profile_registry
from engine.quota_live_gateway import QuotaAwareLiveGateway
from engine.quota_ledger_factory import build_quota_ledger
from engine.quota_manager import QuotaManager


LIVE_SWITCH = "RAAWAA_ENGINE_LIVE_INFERENCE"


def build_live_gateway(
    db_path: str | Path,
    policy_path: str | Path,
    registry_path: str | Path,
    environ=None,
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
    if (policy.get("allow_paid_model_calls") is not False
            or policy.get("daily_budget_usd") != 0
            or policy.get("monthly_budget_usd") != 0
            or policy.get("allow_external_writes") is not False):
        return None

    entries = load_provider_profile_registry(registry_path)
    candidates = [
        entry for entry in entries
        if entry.enabled and entry.ready_for_routing
    ]
    if not candidates:
        return None

    quota_ledger = build_quota_ledger(db_path, source)
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
        adapter = OpenAICompatibleAdapter(
            provider=profile.provider,
            base_url=profile.endpoint_url,
            api_key_env=entry.api_key_env,
            environ=source,
        )
        adapters[profile.route_id] = adapter
        eligible_profiles.append(profile)

    if not eligible_profiles:
        return None

    # Both gates are rechecked inside QuotaAwareLiveGateway.generate().
    return QuotaAwareLiveGateway(
        manager,
        adapters,
        UsageLedger(db_path),
        network_calls_enabled=True,
        environ=source,
    )
