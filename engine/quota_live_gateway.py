"""Quota-gated live inference entry point. Opt-in only; no paid fallback."""
from __future__ import annotations

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest
from engine.quota_manager import QuotaManager


class QuotaAwareLiveGateway:
    """Only calls a provider after a verified-free, commercial-use route is reserved."""

    def __init__(self, quota_manager: QuotaManager, adapters: dict, usage_ledger: UsageLedger,
                 network_calls_enabled: bool = False, cooldown_seconds: int = 900):
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
        estimated_tokens = max(1, len(request.prompt.split())) + request.max_output_tokens

        # Try eligible profiles in priority order. Reservation is atomic across workers.
        candidates = sorted(self.quota_manager.profiles,
                            key=lambda p: (p.priority, p.provider, p.account, p.model))
        last_error = None
        for profile in candidates:
            if not self.quota_manager.eligible(profile, estimated_tokens):
                continue
            adapter = self.adapters.get(profile.provider)
            if adapter is None:
                continue
            try:
                reservation_id = self.quota_manager.ledger.reserve(
                    profile.route_id, estimated_tokens,
                    profile.daily_request_limit, profile.daily_token_limit
                )
            except RuntimeError:
                continue
            try:
                response = adapter.generate(profile.model, request)
            except LiveAdapterError as exc:
                last_error = exc
                if exc.status_code == 429:
                    self.quota_manager.ledger.cool_down(
                        profile.route_id, self.cooldown_seconds, "http_429"
                    )
                # Failed requests consume the reserved request; release estimated tokens.
                self.quota_manager.ledger.settle(reservation_id, 0)
                continue
            actual_tokens = max(0, response.input_tokens) + max(0, response.output_tokens)
            self.quota_manager.ledger.settle(reservation_id, actual_tokens)
            self.usage_ledger.record(response.provider, response.model, request.task_kind,
                                     response.input_tokens, response.output_tokens, 0.0)
            return response

        if last_error is not None:
            raise ProviderUnavailable("All eligible free routes failed or were rate-limited.") from last_error
        raise ProviderUnavailable("No verified free route has sufficient remaining quota.")

    def status(self):
        return {
            "network_calls_enabled": self.network_calls_enabled,
            "paid_fallback_enabled": False,
            "quota_routes": self.quota_manager.snapshot(),
            "registered_adapters": sorted(self.adapters),
        }
