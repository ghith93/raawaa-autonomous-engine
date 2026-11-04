"""Explainable route planning from the provider profile registry.

Phase 3 of the execution plan was recorded as "model router present as API-only
selection stub". That is accurate and it is also the reason routing *looks*
implemented: `ModelRouter` selects from a list of `ModelOption` objects that
somebody constructs by hand, while `config/provider_profiles.json` — the file
that actually records which providers are enabled, free, verified and allowed —
is never read by anything on the routing path. A selection could not be traced
back to a profile, so it could not be audited.

This module closes that gap and nothing else. It answers one question:

    given the registry as it is right now, which profile would be chosen for
    this task kind, and why were the others rejected?

It performs no network I/O, holds no credentials, spends nothing, and cannot
enable a profile. Every exclusion carries the first rule that failed, in a fixed
order, so the answer is reproducible and arguable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.model_router import ModelOption, ModelRouter
from engine.quota_config import load_provider_profile_registry

SCHEMA_VERSION = 1
MODE = "read_only_route_plan"
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "provider_profiles.json"


def _zero(value: Any) -> bool:
    return value is None or value == 0 or value == 0.0


def _exclusion_reason(entry: Any) -> str | None:
    """Return the first failed eligibility rule, or None when the profile is eligible.

    Specific causes are checked before the aggregate `ready_for_routing`, because
    the aggregate also returns false for a dozen unrelated reasons — reporting
    only "not ready" would tell an operator nothing about what to fix. Order is
    fixed so two runs never disagree about *why* a profile was skipped.
    """
    if not entry.enabled:
        return "profile_disabled"
    if not entry.profile.production_use_allowed:
        return "production_use_not_allowed"
    if not entry.profile.free_verified:
        return "free_usage_not_verified"
    if not entry.profile.commercial_use_allowed:
        return "commercial_use_not_allowed"
    if not _zero(entry.profile.input_cost_per_million_usd) or not _zero(
        entry.profile.output_cost_per_million_usd
    ):
        return "would_incur_cost"
    if not entry.verification_is_current:
        return "verification_stale"
    if not entry.ready_for_routing:
        return "not_ready_for_routing"
    return None


def _option(entry: Any) -> ModelOption:
    return ModelOption(
        provider=entry.profile.provider,
        model=entry.profile.model,
        tier="free",
        enabled=True,
        estimated_cost_per_1k_tokens_usd=0.0,
    )


def build_route_plan(
    registry_path: Path | str | None = None,
    *,
    task_kind: str = "default",
) -> dict[str, Any]:
    """Describe the route a registry-backed router would take, with reasons."""
    path = Path(registry_path) if registry_path is not None else REGISTRY_PATH
    now = datetime.now(timezone.utc)

    try:
        entries = load_provider_profile_registry(path)
    except Exception as error:  # fail closed and say so, rather than guess a profile
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.isoformat(),
            "mode": MODE,
            "task_kind": task_kind,
            "registry": str(path),
            "selected": None,
            "candidates": [],
            "excluded": [],
            "error": f"registry_unavailable:{type(error).__name__}",
            "side_effects": _no_side_effects(),
        }

    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    options: list[ModelOption] = []

    for entry in entries:
        reason = _exclusion_reason(entry)
        record = {
            "provider": entry.profile.provider,
            "model": entry.profile.model,
            "stage": entry.model_stage,
            "api_key_env": entry.api_key_env,
        }
        if reason is not None:
            excluded.append({**record, "reason": reason})
            continue
        options.append(_option(entry))
        candidates.append({**record, "tier": "free"})

    selected = ModelRouter(options, paid_calls_enabled=False).select(task_kind)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "mode": MODE,
        "task_kind": task_kind,
        "registry": str(path),
        "selected": (
            {"provider": selected.provider, "model": selected.model, "tier": selected.tier}
            if selected is not None
            else None
        ),
        "selection_reason": (
            "eligible profiles are ordered by cost, then provider, then model; the first wins"
            if selected is not None
            else "no profile in the registry is simultaneously enabled, routing-ready, "
                 "production-allowed, free-verified and zero-cost"
        ),
        "candidates": candidates,
        "excluded": excluded,
        "counts": {"profiles": len(entries), "eligible": len(candidates), "excluded": len(excluded)},
        "side_effects": _no_side_effects(),
    }


def _no_side_effects() -> dict[str, Any]:
    return {
        "network_calls": 0,
        "model_calls": 0,
        "credentials_read": 0,
        "profiles_enabled": 0,
        "spend_usd": 0.0,
    }


def render_markdown(plan: dict[str, Any]) -> str:
    lines = ["## Route plan", "", f"Task kind: `{plan['task_kind']}` — read-only, no network, no spend.", ""]
    selected = plan.get("selected")
    if selected:
        lines.append(f"**Selected:** `{selected['provider']}/{selected['model']}` ({selected['tier']})")
    else:
        lines.append("**Selected:** none")
        lines.append("")
        lines.append(f"_{plan.get('selection_reason', '')}_")
    if plan.get("error"):
        lines += ["", f"**Registry error:** `{plan['error']}`"]

    lines += ["", "| Profile | Stage | Excluded because |", "|---|---|---|"]
    for item in plan.get("excluded") or []:
        lines.append(f"| `{item['provider']}/{item['model']}` | {item['stage']} | {item['reason']} |")
    if not plan.get("excluded"):
        lines.append("| — | — | nothing excluded |")
    return "\n".join(lines) + "\n"
