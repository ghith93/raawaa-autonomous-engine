"""Quota-gated live inference entry point. Opt-in only; no paid fallback."""
from __future__ import annotations

import math

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest
from engine.quota_manager import QuotaManager


class QuotaAwareLiveGateway:
    """Only calls a provider after a verified-free, commercial-use route is reserved."""

    def __init__(self, quota_manager: QuotaManager, adapters: dict, usage_ledger: UsageLedger,
                 network_calls_enabled: bool = False, cooldown_seconds: int = 900):
        if cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be positive")
        self.quota_manager = quota_manager
        self.adapters = adapters
        self.usage_ledger = usage_ledger
        self.network_calls_enabled = network_calls_enabled
        self.cooldown_seconds = cooldown_seconds

    def generate(self, request: GenerationRequest):
        if not self.network_calls_enabled:
            raise ProviderUnavailable("Live network execution is disabled by policy.")
        if not request.prompt.strip():
            raise ValueError("prompt is required")
        if not 1 <= request.max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 1 and 4096")

        # Character-based estimate is more conservative than whitespace word count.
        estimated_input_tokens = max(1, (len(request.prompt) + 2) // 3)
        estimated_tokens = estimated_input_tokens + request.max_output_tokens

        candidates = sorted(
            self.quota_manager.profiles,
            key=lambda profile: (
                profile.priority, profile.provider, profile.account, profile.model
            ),
        )
        last_error = None
        for profile in candidates:
            if not self.quota_manager.eligible(profile, estimated_tokens):
                continue
            adapter = self.adapters.get(profile.route_id) or self.adapters.get(profile.provider)
            if adapter is None:
                continue
            try:
                reservation_id = self.quota_manager.ledger.reserve(
                    profile.route_id,
                    estimated_tokens,
                    profile.daily_request_limit,
                    profile.daily_token_limit,
                )
            except RuntimeError:
                # Another worker may have reserved the remaining capacity.
                continue

            try:
                response = adapter.generate(profile.model, request)
                if response.provider != profile.provider or response.model != profile.model:
                    raise ValueError("adapter response route does not match reserved profile")
                if not isinstance(response.text, str):
                    raise ValueError("adapter response text must be a string")
                if (type(response.input_tokens) is not int or type(response.output_tokens) is not int
                        or response.input_tokens < 0 or response.output_tokens < 0):
                    raise ValueError("adapter response token counts are invalid")
                if not math.isfinite(response.cost_usd) or response.cost_usd < 0:
                    raise ValueError("adapter response cost is invalid")
            except LiveAdapterError as exc:
                last_error = exc
                self.quota_manager.ledger.settle(reservation_id, 0)
                if exc.status_code == 429:
                    self.quota_manager.ledger.cool_down(
                        profile.route_id, self.cooldown_seconds, "http_429"
                    )
                # Only known provider errors are eligible for route rotation.
                continue
            except Exception as exc:
                # Do not silently rotate after an unknown adapter/programming error.
                self.quota_manager.ledger.settle(reservation_id, 0)
                raise ProviderUnavailable(
                    "Live adapter failed unexpectedly; route rotation was stopped."
                ) from exc

            actual_tokens = max(0, response.input_tokens) + max(0, response.output_tokens)
            self.quota_manager.ledger.settle(reservation_id, actual_tokens)
            self.usage_ledger.record(
                response.provider,
                response.model,
                request.task_kind,
                response.input_tokens,
                response.output_tokens,
                response.cost_usd,
            )
            return response

        if last_error is not None:
            raise ProviderUnavailable(
                "All eligible free routes failed or were rate-limited."
            ) from last_error
        raise ProviderUnavailable("No verified free route has sufficient remaining quota.")

    def status(self):
        return {
            "network_calls_enabled": self.network_calls_enabled,
            "paid_fallback_enabled": False,
            "quota_routes": self.quota_manager.snapshot(),
            "registered_adapters": sorted(self.adapters),
            "cost_accounting_note": (
                "Adapter cost_usd is a placeholder until provider pricing is verified."
            ),
        }
