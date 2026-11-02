"""Quota-gated live inference entry point. Opt-in only; no paid fallback."""
from __future__ import annotations

import math
import os

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import BudgetExceeded, ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest
from engine.quota_manager import QuotaManager


class QuotaAwareLiveGateway:
    """Only calls a provider after a verified-free, commercial-use route is reserved."""

    def __init__(self, quota_manager: QuotaManager, adapters: dict, usage_ledger: UsageLedger,
                 network_calls_enabled: bool = False, cooldown_seconds: int = 900,
                 environ=None, daily_budget_usd: float = 0.0,
                 monthly_budget_usd: float = 0.0, per_task_budget_usd: float = 0.0):
        if cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be positive")
        self.quota_manager = quota_manager
        self.adapters = adapters
        self.usage_ledger = usage_ledger
        self.network_calls_enabled = network_calls_enabled
        self.cooldown_seconds = cooldown_seconds
        self.environ = os.environ if environ is None else environ
        self.daily_budget_usd = daily_budget_usd
        self.monthly_budget_usd = monthly_budget_usd
        if (type(per_task_budget_usd) not in (int, float)
                or not math.isfinite(per_task_budget_usd) or per_task_budget_usd < 0):
            raise ValueError("per_task_budget_usd must be finite and non-negative")
        self.per_task_budget_usd = per_task_budget_usd
        # Validate configured caps before the gateway can make any provider request.
        self.usage_ledger.budget_status(daily_budget_usd, monthly_budget_usd)

    def _live_enabled(self) -> bool:
        return (
            self.network_calls_enabled
            and self.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() == "enabled"
        )

    def _audit(self, event: str, request: GenerationRequest,
               profile=None, **details) -> None:
        self.quota_manager.ledger.audit_event(
            event,
            route_id=profile.route_id if profile is not None else None,
            task_kind=request.task_kind,
            details=details,
        )

    def generate(self, request: GenerationRequest):
        if not self._live_enabled():
            self._audit("live_request_blocked", request, reason="kill_switch_off")
            raise ProviderUnavailable("Live network execution is disabled by policy or operator kill switch.")
        if not request.prompt.strip():
            raise ValueError("prompt is required")
        if not isinstance(request.task_id, str) or not request.task_id.strip():
            raise ProviderUnavailable("A task ID is required for per-task quota enforcement.")
        if not 1 <= request.max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 1 and 4096")

        # Reserve against UTF-8 bytes, not a lossy character/word heuristic. This
        # intentionally overestimates most tokenizers so concurrent calls are less
        # likely to exceed a provider's hard daily token cap before settlement.
        estimated_input_tokens = max(1, len(request.prompt.encode("utf-8")))
        estimated_tokens = estimated_input_tokens + request.max_output_tokens

        candidates = sorted(
            self.quota_manager.profiles,
            key=lambda profile: (
                profile.priority, profile.provider, profile.account, profile.model
            ),
        )
        last_error = None
        for profile in candidates:
            if (profile.max_output_tokens is None
                    or request.max_output_tokens > profile.max_output_tokens):
                continue
            if not self.quota_manager.eligible(profile, estimated_tokens):
                continue
            adapter = self.adapters.get(profile.route_id) or self.adapters.get(profile.provider)
            if adapter is None:
                continue
            input_price = profile.input_cost_per_million_usd
            output_price = profile.output_cost_per_million_usd
            if (type(input_price) not in (int, float)
                    or type(output_price) not in (int, float)
                    or not math.isfinite(input_price) or not math.isfinite(output_price)
                    or input_price < 0 or output_price < 0):
                continue
            estimated_cost_usd = (
                estimated_input_tokens * input_price
                + request.max_output_tokens * output_price
            ) / 1_000_000
            try:
                monetary_reservation_id = self.usage_ledger.reserve(
                    profile.provider, profile.model, request.task_kind,
                    estimated_cost_usd, self.daily_budget_usd, self.monthly_budget_usd,
                    task_id=request.task_id,
                    per_task_budget_usd=self.per_task_budget_usd,
                )
            except BudgetExceeded as exc:
                last_error = exc
                self._audit(
                    "live_request_blocked", request, profile,
                    reason="monetary_budget_exceeded",
                )
                continue

            try:
                reservation_id = self.quota_manager.ledger.reserve(
                    profile.route_id,
                    estimated_tokens,
                    profile.daily_request_limit,
                    profile.daily_token_limit,
                    monthly_request_limit=profile.monthly_request_limit,
                    monthly_token_limit=profile.monthly_token_limit,
                    task_id=request.task_id,
                    per_task_request_limit=profile.per_task_request_limit,
                    per_task_token_limit=profile.per_task_token_limit,
                    minute_request_limit=profile.minute_request_limit,
                    minute_token_limit=profile.minute_token_limit,
                )
            except RuntimeError:
                # Another worker may have reserved the remaining token capacity.
                # No provider request was sent, so releasing the monetary reservation is safe.
                self.usage_ledger.release(monetary_reservation_id)
                continue
            except Exception:
                # The token reservation did not return, so no provider request was sent.
                self.usage_ledger.release(monetary_reservation_id)
                raise

            self._audit(
                "quota_reserved", request, profile,
                estimated_tokens=estimated_tokens,
            )
            try:
                response = adapter.generate(profile.model, request)
                if response.provider != profile.provider or response.model != profile.model:
                    raise ValueError("adapter response route does not match reserved profile")
                if not isinstance(response.text, str) or not response.text.strip():
                    raise ValueError("adapter response text must be a non-empty string")
                if response.mocked is not False:
                    raise ValueError("live adapter returned a mocked completion")
                if (type(response.input_tokens) is not int or type(response.output_tokens) is not int
                        or response.input_tokens < 0 or response.output_tokens < 0):
                    raise ValueError("adapter response token counts are invalid")
                if not math.isfinite(response.cost_usd) or response.cost_usd < 0:
                    raise ValueError("adapter response cost is invalid")
            except LiveAdapterError as exc:
                last_error = exc
                self._audit(
                    "provider_error", request, profile,
                    http_status=exc.status_code or 0,
                    error_type=type(exc).__name__,
                )
                # The provider may have processed the request before a timeout,
                # transport failure, or malformed response. Retain the full token
                # reservation conservatively; request count is already consumed.
                self.quota_manager.ledger.settle(reservation_id, estimated_tokens)
                if exc.status_code == 429:
                    # An explicit 429 rejects the request; no provider charge is expected.
                    self.usage_ledger.release(monetary_reservation_id)
                    self.quota_manager.ledger.cool_down(
                        profile.route_id, self.cooldown_seconds, "http_429"
                    )
                    self._audit(
                        "provider_cooldown", request, profile,
                        reason="http_429", cooldown_seconds=self.cooldown_seconds,
                    )
                    # An explicit rate-limit response rejects this request, so a
                    # different verified route may be tried safely.
                    continue
                # A timeout, transport failure, malformed response, 5xx, or
                # non-429 HTTP error may follow provider-side processing. Never
                # replay the same request against another route in this case.
                self._audit(
                    "provider_failure_stopped", request, profile,
                    reason="non_429_no_route_rotation",
                    http_status=exc.status_code or 0,
                )
                raise ProviderUnavailable(
                    "Provider error was not HTTP 429; route rotation stopped to avoid replay."
                ) from exc
            except Exception as exc:
                # Do not silently rotate after an unknown adapter/programming error.
                # Unknown adapter failures have ambiguous remote execution status.
                # Keep the reservation rather than making possibly-used quota reusable.
                self.quota_manager.ledger.settle(reservation_id, estimated_tokens)
                self._audit(
                    "adapter_failure", request, profile,
                    error_type=type(exc).__name__, reason="ambiguous_adapter_failure",
                )
                raise ProviderUnavailable(
                    "Live adapter failed unexpectedly; route rotation was stopped."
                ) from exc

            actual_tokens = response.input_tokens + response.output_tokens
            # Persist monetary usage first so a later token-ledger failure cannot
            # lose the provider's actual charge.
            self.usage_ledger.settle(
                monetary_reservation_id,
                response.input_tokens,
                response.output_tokens,
                response.cost_usd,
            )
            self.quota_manager.ledger.settle(reservation_id, actual_tokens)
            self._audit(
                "completion_received", request, profile,
                actual_tokens=actual_tokens, cost_usd=response.cost_usd,
            )
            if (response.input_tokens > estimated_input_tokens
                    or response.output_tokens > request.max_output_tokens):
                # The provider exceeded a hard reservation ceiling. Keep the actual
                # usage/cost in both ledgers, quarantine this route, and never rotate
                # to another provider after a potentially billable contract violation.
                self.quota_manager.ledger.cool_down(
                    profile.route_id, self.cooldown_seconds, "response_exceeded_token_ceiling"
                )
                self._audit(
                    "route_quarantined", request, profile,
                    reason="response_exceeded_token_ceiling",
                    cooldown_seconds=self.cooldown_seconds,
                )
                raise ProviderUnavailable(
                    "Provider response exceeded reserved token ceilings; route quarantined."
                )
            if response.cost_usd > 0:
                # A route advertised as free has contradicted its verified profile.
                # Preserve the measured usage/cost, quarantine the route, and fail
                # closed rather than returning a result or silently trying another.
                self.quota_manager.ledger.cool_down(
                    profile.route_id, self.cooldown_seconds, "unexpected_nonzero_cost"
                )
                self._audit(
                    "route_quarantined", request, profile,
                    reason="unexpected_nonzero_cost",
                    cooldown_seconds=self.cooldown_seconds,
                    cost_usd=response.cost_usd,
                )
                raise ProviderUnavailable(
                    "Provider reported non-zero cost for a verified-free route; route quarantined."
                )
            budget_status = self.usage_ledger.budget_status(
                self.daily_budget_usd, self.monthly_budget_usd
            )
            if budget_status["daily_over_budget"] or budget_status["monthly_over_budget"]:
                self.quota_manager.ledger.cool_down(
                    profile.route_id, self.cooldown_seconds, "monetary_budget_exceeded"
                )
                self._audit(
                    "route_quarantined", request, profile,
                    reason="monetary_budget_exceeded",
                    cooldown_seconds=self.cooldown_seconds,
                )
                raise ProviderUnavailable(
                    "Actual provider cost exceeded the configured monetary budget; route quarantined."
                )
            return response

        if isinstance(last_error, BudgetExceeded):
            raise ProviderUnavailable(
                "No eligible provider route remains within the configured monetary budget."
            ) from last_error
        if last_error is not None:
            raise ProviderUnavailable(
                "All eligible free routes failed or were rate-limited."
            ) from last_error
        raise ProviderUnavailable("No verified free route has sufficient remaining quota.")

    def status(self):
        return {
            "network_calls_enabled": self._live_enabled(),
            "operator_kill_switch_enabled": (
                self.environ.get("RAAWAA_ENGINE_LIVE_INFERENCE", "").strip().lower() == "enabled"
            ),
            "paid_fallback_enabled": False,
            "quota_routes": self.quota_manager.snapshot(),
            "registered_adapters": sorted(self.adapters),
            "monetary_budgets": {
                "daily_usd": self.daily_budget_usd,
                "monthly_usd": self.monthly_budget_usd,
                "per_task_usd": self.per_task_budget_usd,
            },
            "monetary_usage": self.usage_ledger.totals(),
            "cost_accounting_note": (
                "Monetary spend is reserved before provider I/O and settled idempotently; "
                "ambiguous outcomes retain reservations. Only verified zero-cost profiles "
                "are currently eligible; unexpected charges are recorded and quarantine the route."
            ),
        }
