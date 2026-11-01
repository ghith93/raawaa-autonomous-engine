"""Provider-neutral model selection. This phase intentionally performs no network calls."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    provider: str
    model: str
    tier: str
    enabled: bool = False
    estimated_cost_per_1k_tokens_usd: float = 0.0


class ModelRouter:
    def __init__(self, options: list[ModelOption] | None = None, paid_calls_enabled: bool = False):
        self.options = options or []
        self.paid_calls_enabled = paid_calls_enabled

    def select(self, task_kind: str) -> ModelOption | None:
        """Return a configured eligible model; never silently fall back to a paid model."""
        eligible = [m for m in self.options if m.enabled]
        if not self.paid_calls_enabled:
            eligible = [m for m in eligible if m.tier == "free" and m.estimated_cost_per_1k_tokens_usd == 0]
        if not eligible:
            return None
        # Stable ordering makes routing deterministic until measured quality/latency data exists.
        return sorted(eligible, key=lambda m: (m.estimated_cost_per_1k_tokens_usd, m.provider, m.model))[0]

    @staticmethod
    def status() -> dict:
        return {
            "network_calls_enabled": False,
            "provider_credentials_required": False,
            "paid_fallback_enabled": False,
            "note": "No provider adapters are installed in this phase; routing is selection-only."
        }
