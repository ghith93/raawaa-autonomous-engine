"""Load staged provider quota profiles without enabling provider execution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engine.quota_manager import QuotaProfile


@dataclass(frozen=True)
class ProviderProfileEntry:
    profile_id: str
    enabled: bool
    account_tier: str
    api_key_env: str
    verification_note: str
    production_use_allowed: bool
    model_stage: str
    model_terms_source_url: str
    profile: QuotaProfile

    @property
    def ready_for_routing(self) -> bool:
        p = self.profile
        return bool(
            self.enabled
            and p.free_verified
            and p.commercial_use_allowed
            and self.production_use_allowed
            and p.input_cost_per_million_usd == 0.0
            and p.output_cost_per_million_usd == 0.0
            and p.minute_request_limit
            and p.minute_token_limit
            and p.daily_request_limit
            and p.daily_token_limit
            and p.monthly_request_limit
            and p.monthly_token_limit
            and p.per_task_request_limit
            and p.per_task_token_limit
            and p.endpoint_url
            and p.pricing_source_url
            and p.quota_source_url
            and p.terms_source_url
            and p.verified_at
            and p.max_output_tokens
        )


def load_provider_profile_registry(path: str | Path) -> list[ProviderProfileEntry]:
    """Load explicit route profiles; never reads or resolves API key values."""
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported provider profile registry schema")
    if data.get("mode") != "managed_opt_in":
        raise ValueError("provider profile registry must use managed_opt_in mode")
    routes = data.get("routes")
    if not isinstance(routes, list):
        raise ValueError("provider profile registry routes must be a list")

    entries = []
    seen = set()
    for item in routes:
        if not isinstance(item, dict):
            raise ValueError("provider profile entry must be an object")
        profile_id = item.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip() or profile_id in seen:
            raise ValueError("provider profile IDs must be non-empty and unique")
        seen.add(profile_id)
        if type(item.get("enabled")) is not bool:
            raise ValueError("provider profile enabled must be an explicit boolean")
        api_key_env = item.get("api_key_env")
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", api_key_env):
            raise ValueError("provider API key environment variable name is invalid")
        note = item.get("verification_note", "")
        production_use_allowed = item.get("production_use_allowed") is True
        model_stage = item.get("model_stage", "unknown")
        if not isinstance(model_stage, str) or not model_stage.strip():
            raise ValueError("model_stage must be non-empty text")
        if not isinstance(note, str):
            raise ValueError("verification_note must be text")
        profile = QuotaProfile(
            provider=item["provider"],
            account=item["account"],
            model=item["model"],
            free_verified=item["free_verified"],
            commercial_use_allowed=item["commercial_use_allowed"],
            production_use_allowed=item.get("production_use_allowed", False),
            enabled=item.get("enabled", False),
            minute_request_limit=item.get("minute_request_limit"),
            minute_token_limit=item.get("minute_token_limit"),
            daily_request_limit=item.get("daily_request_limit"),
            daily_token_limit=item.get("daily_token_limit"),
            monthly_request_limit=item.get("monthly_request_limit"),
            monthly_token_limit=item.get("monthly_token_limit"),
            per_task_request_limit=item.get("per_task_request_limit"),
            per_task_token_limit=item.get("per_task_token_limit"),
            input_cost_per_million_usd=item.get("input_cost_per_million_usd"),
            output_cost_per_million_usd=item.get("output_cost_per_million_usd"),
            endpoint_url=item.get("endpoint_url"),
            pricing_source_url=item.get("pricing_source_url"),
            quota_source_url=item.get("quota_source_url"),
            terms_source_url=item.get("terms_source_url"),
            verified_at=datetime.fromisoformat(item["verified_at"].replace("Z", "+00:00"))
            if isinstance(item.get("verified_at"), str) else None,
            max_output_tokens=item.get("max_output_tokens"),
            priority=item.get("priority", 100),
        )
        entries.append(ProviderProfileEntry(
            profile_id=profile_id,
            enabled=item["enabled"],
            account_tier=str(item.get("account_tier", "unknown")),
            api_key_env=api_key_env,
            verification_note=note,
            production_use_allowed=production_use_allowed,
            model_stage=model_stage,
            model_terms_source_url=item.get("model_terms_source_url", ""),
            profile=profile,
        ))
    return entries
