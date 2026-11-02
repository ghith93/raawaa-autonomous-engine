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
from engine.quota_backend import open_quota_ledger, remote_ledger_configured
from engine.quota_config import load_provider_profile_registry
from engine.quota_live_gateway import QuotaAwareLiveGateway
from engine.quota_manager import QuotaManager
from engine.remote_task_store import RemoteTaskStoreError


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
    # GitHub Actions runners are ephemeral: a local SQLite quota ledger would be
    # empty on every run, so quota limits and 429 cooldowns would never hold.
    # Refuse live inference there unless the durable D1 ledger is configured.
    if (str(source.get("GITHUB_ACTIONS", "")).strip().lower() == "true"
            and not remote_ledger_configured(source)):
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

    quota_ledger = open_quota_ledger(db_path, source)
    manager = QuotaManager([entry.profile for entry in candidates], quota_ledger)
    adapters = {}
    eligible_profiles = []
    try:
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
    except RemoteTaskStoreError:
        # The durable ledger is unreachable: fail closed, no live inference.
        return None

    if not eligible_profiles:
        return None

    # Both gates are rechecked inside QuotaAwareLiveGateway.generate().
    return QuotaAwareLiveGateway(
        manager,
        adapters,
        UsageLedger(db_path),
        network_calls_enabled=True,
        environ=source,
        daily_budget_usd=policy.get("daily_budget_usd", 0),
        monthly_budget_usd=policy.get("monthly_budget_usd", 0),
        per_task_budget_usd=policy.get("per_task_budget_usd", 0),
    )
