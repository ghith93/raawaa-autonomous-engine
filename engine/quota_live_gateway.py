"""Quota-gated live inference entry point. Opt-in only; no paid fallback."""
from __future__ import annotations

import math
import os
import uuid
from dataclasses import replace

from engine.live_adapters import LiveAdapterError
from engine.model_gateway import BudgetExceeded, ProviderUnavailable, UsageLedger
from engine.provider import GenerationRequest
from engine.quota_manager import QuotaManager


class QuotaAwareLiveGateway:
    """Only calls a provider after a verified-free, commercial-use route is reserved."""

    def __init__(self, quota_manager: QuotaManager, adapters: dict, usage_ledger: UsageLedger,
                 network_calls_enabled: bool = False, cooldown_seconds: int = 900,
                 environ=None, daily_budget_usd: float = 0.0,
                 monthly_budget_usd: float = 0.0, per_task_budget_usd: float = 0.0,
                 invocation_store=None):
        if cooldown_seconds < 1:
            raise ValueError("cooldown_seconds must be positive")
        self.quota_manager = quota_manager
        self.adapters = adapters
        self.usage_ledger = usage_ledger
        self.invocation_store = invocation_store
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

    def preflight_capacity(self, max_output_tokens: int = 256,
                           estimated_input_tokens: int = 1) -> dict:
        """Read-only capacity signal before a task is claimed; never reserves or calls a provider."""
        if not self._live_enabled():
            return {"eligible": False, "reason": "live_inference_disabled"}
        if (type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4096
                or type(estimated_input_tokens) is not int or estimated_input_tokens < 1):
            return {"eligible": False, "reason": "invalid_preflight_request"}
        try:
            budget = self.usage_ledger.budget_status(
                self.daily_budget_usd, self.monthly_budget_usd,
            )
        except Exception:
            return {"eligible": False, "reason": "monetary_budget_status_unavailable"}
        if budget["daily_over_budget"] or budget["monthly_over_budget"]:
            return {"eligible": False, "reason": "monetary_budget_exhausted"}

        estimated_tokens = estimated_input_tokens + max_output_tokens
        candidates = sorted(
            self.quota_manager.profiles,
            key=lambda profile: (
                profile.priority, profile.provider, profile.account, profile.model
            ),
        )
        for profile in candidates:
            if (profile.max_output_tokens is None
                    or max_output_tokens > profile.max_output_tokens):
                continue
            if not self.quota_manager.eligible(profile, estimated_tokens):
                continue
            if self.adapters.get(profile.route_id) is None and self.adapters.get(profile.provider) is None:
                continue
            return {
                "eligible": True,
                "reason": "verified_free_route_available",
                "route_id": profile.route_id,
                "estimated_tokens": estimated_tokens,
                "per_task_budget_checked_after_claim": self.per_task_budget_usd > 0,
            }
        return {
            "eligible": False,
            "reason": "provider_capacity_unavailable",
            "estimated_tokens": estimated_tokens,
        }

    def generate(self, request: GenerationRequest):
        if not self._live_enabled():
            self._audit("live_request_blocked", request, reason="kill_switch_off")
            raise ProviderUnavailable("Live network execution is disabled by policy or operator kill switch.")
        if not request.prompt.strip():
            raise ValueError("prompt is required")
        if not isinstance(request.task_id, str) or not request.task_id.strip():
            raise ProviderUnavailable("A task ID is required for per-task quota enforcement.")
        if self.invocation_store is not None:
            if not isinstance(request.step_key, str) or not request.step_key.strip():
                raise ProviderUnavailable("A durable step_key is required for invocation checkpoints.")
            if not isinstance(request.lease_token, str) or not request.lease_token.strip():
                raise ProviderUnavailable("A current task lease_token is required for invocation checkpoints.")
            try:
                checkpoint = self.invocation_store.prepare(
                    request.task_id, request.lease_token, request.step_key, request.task_kind
                )
            except Exception as exc:
                raise ProviderUnavailable("Could not prepare a durable task-step invocation; provider was not called.") from exc
            request = replace(request, request_id=checkpoint["request_id"], invocation_id=checkpoint["invocation_id"])
        if not isinstance(request.request_id, str) or not request.request_id.strip():
            raise ProviderUnavailable(
                "A stable request_id is required; persist it with the task step before inference."
            )
        try:
            request_uuid = uuid.UUID(request.request_id)
        except (ValueError, TypeError, AttributeError):
            raise ProviderUnavailable("request_id must be a valid UUID.") from None
        if str(request_uuid) != request.request_id.lower():
            raise ProviderUnavailable("request_id must use canonical UUID form.")
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
            # Route-scoped deterministic identities make a retry of the same
            # logical request return the original D1 reservation even if the first
            # HTTP response was lost after commit. A different route gets a distinct
            # reservation if an explicit HTTP 429 permits safe route rotation.
            identity_scope = f"raawaa-engine:{request.request_id}:{profile.route_id}"
            idempotency_key = str(uuid.uuid5(uuid.NAMESPACE_URL, identity_scope + ":idem"))
            reservation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity_scope + ":reservation"))
            try:
                monetary_reservation_id = self.usage_ledger.reserve(
                    profile.provider, profile.model, request.task_kind,
                    estimated_cost_usd, self.daily_budget_usd, self.monthly_budget_usd,
                    task_id=request.task_id,
                    per_task_budget_usd=self.per_task_budget_usd,
                    idempotency_key=idempotency_key,
                    reservation_id=reservation_id,
                )
            except BudgetExceeded as exc:
                last_error = exc
                self._audit(
                    "live_request_blocked", request, profile,
                    reason="monetary_budget_exceeded",
                )
                continue

            if self.invocation_store is not None:
                try:
                    self.invocation_store.start(request.task_id, request.lease_token, request.invocation_id, profile.route_id)
                except Exception as exc:
                    self.usage_ledger.release(monetary_reservation_id)
                    raise ProviderUnavailable("Could not durably start the task-step invocation; provider was not called.") from exc

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
                if self.invocation_store is not None:
                    self.invocation_store.reject(request.task_id, request.lease_token, request.invocation_id, profile.route_id, "quota_capacity_unavailable")
                continue
            except Exception:
                # The token reservation did not return, so no provider request was sent.
                self.usage_ledger.release(monetary_reservation_id)
                if self.invocation_store is not None:
                    self.invocation_store.reject(request.task_id, request.lease_token, request.invocation_id, profile.route_id, "quota_reservation_error")
                raise

            self._audit(
                "quota_reserved", request, profile,
                estimated_tokens=estimated_tokens,
            )
            try:
                self.usage_ledger.mark_started(monetary_reservation_id)
            except Exception as exc:
                self._audit(
                    "monetary_start_failed", request, profile,
                    error_type=type(exc).__name__, reason="provider_not_called",
                )
                # The start response may have been lost. Leave the reservation counted;
                # do not call the provider and do not attempt an unsafe release.
                raise ProviderUnavailable(
                    "Could not confirm durable monetary reservation start; provider was not called."
                ) from exc
            failure_stage = "adapter_generate"
            try:
                response = adapter.generate(profile.model, request)
                failure_stage = "response_validation"
                if response.provider != profile.provider or response.model != profile.model:
                    raise ValueError("adapter response route does not match reserved profile")
                if not isinstance(response.text, str) or not response.text.strip():
                    raise ValueError("adapter response text must be a non-empty string")
                if response.mocked is not False:
                    raise ValueError("live adapter returned a mocked completion")
                if (type(response.input_tokens) is not int or type(response.output_tokens) is not int
                        or response.input_tokens < 0 or response.output_tokens < 0):
                    raise ValueError("adapter response token counts are invalid")
                reported_cost_usd = response.cost_usd
                if (reported_cost_usd is not None
                        and (type(reported_cost_usd) not in (int, float)
                             or not math.isfinite(reported_cost_usd)
                             or reported_cost_usd < 0)):
                    raise ValueError("adapter response cost is invalid")
            except LiveAdapterError as exc:
                last_error = exc
                self._audit(
                    "provider_error", request, profile,
                    http_status=exc.status_code or 0,
                    error_type=type(exc).__name__,
                    error_code=exc.error_code,
                )
                # The provider may have processed the request before a timeout,
                # transport failure, or malformed response. Retain the full token
                # reservation conservatively; request count is already consumed.
                self.quota_manager.ledger.settle(reservation_id, estimated_tokens)
                if exc.status_code == 429:
                    # A 429 is after provider I/O began, so it is settled at zero rather
                    # than released. Release is reserved strictly for pre-I/O failures.
                    self.usage_ledger.settle(monetary_reservation_id, 0, 0, 0.0)
                    if self.invocation_store is not None:
                        self.invocation_store.reject(request.task_id, request.lease_token, request.invocation_id, profile.route_id, "http_429")
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
                if (exc.status_code == 403 and exc.error_code in {
                    "permissions_error",
                    "model_permission_blocked_org",
                    "model_permission_blocked_project",
                }):
                    # Groq documents these machine codes as model-permission denials.
                    # The model request was rejected before inference. Settle zero,
                    # quarantine this exact route, and try another verified-free model.
                    self.usage_ledger.settle(monetary_reservation_id, 0, 0, 0.0)
                    if self.invocation_store is not None:
                        self.invocation_store.reject(request.task_id, request.lease_token, request.invocation_id, profile.route_id, "provider_permission_denied")
                    self.quota_manager.ledger.cool_down(
                        profile.route_id, self.cooldown_seconds,
                        "provider_model_permission_denied",
                    )
                    self._audit(
                        "provider_route_denied", request, profile,
                        reason="provider_model_permission_denied",
                        http_status=403,
                        error_code=exc.error_code,
                        cooldown_seconds=self.cooldown_seconds,
                    )
                    continue
                # A timeout, transport failure, malformed response, 5xx, or
                # unclassified HTTP error may follow provider-side processing. Never
                # replay or release the reservation in this case.
                self.usage_ledger.mark_ambiguous(monetary_reservation_id)
                if self.invocation_store is not None:
                    self.invocation_store.mark_ambiguous(request.task_id, request.lease_token, request.invocation_id)
                self._audit(
                    "provider_failure_stopped", request, profile,
                    reason="non_429_no_route_rotation",
                    http_status=exc.status_code or 0,
                    error_code=exc.error_code,
                )
                raise ProviderUnavailable(
                    "Provider error was not HTTP 429; route rotation stopped to avoid replay."
                ) from exc
            except Exception as exc:
                # Do not silently rotate after an unknown adapter/programming error.
                # Unknown adapter failures have ambiguous remote execution status.
                # Keep the reservation rather than making possibly-used quota reusable.
                self.quota_manager.ledger.settle(reservation_id, estimated_tokens)
                self.usage_ledger.mark_ambiguous(monetary_reservation_id)
                if self.invocation_store is not None:
                    self.invocation_store.mark_ambiguous(request.task_id, request.lease_token, request.invocation_id)
                self._audit(
                    "adapter_failure", request, profile,
                    error_type=type(exc).__name__, failure_stage=failure_stage,
                    reason="ambiguous_adapter_failure",
                )
                raise ProviderUnavailable(
                    "Live adapter failed unexpectedly; route rotation was stopped."
                ) from exc

            actual_tokens = response.input_tokens + response.output_tokens
            profile_estimated_cost_usd = (
                response.input_tokens * input_price
                + response.output_tokens * output_price
            ) / 1_000_000
            if reported_cost_usd is None:
                settled_cost_usd = profile_estimated_cost_usd
                cost_basis = "verified_profile_estimate"
            else:
                settled_cost_usd = max(profile_estimated_cost_usd, reported_cost_usd)
                cost_basis = (
                    "provider_reported"
                    if reported_cost_usd >= profile_estimated_cost_usd
                    else "verified_profile_floor"
                )
            # Persist the provider-reported amount or verified-profile estimate
            # first so a later token-ledger failure cannot lose cost evidence.
            self.usage_ledger.settle(
                monetary_reservation_id,
                response.input_tokens,
                response.output_tokens,
                settled_cost_usd,
            )
            self.quota_manager.ledger.settle(reservation_id, actual_tokens)
            if self.invocation_store is not None:
                self.invocation_store.settle(request.task_id, request.lease_token, request.invocation_id)
            self._audit(
                "completion_received", request, profile,
                actual_tokens=actual_tokens,
                cost_usd=settled_cost_usd,
                cost_basis=cost_basis,
                profile_estimated_cost_usd=profile_estimated_cost_usd,
                provider_reported_cost_available=reported_cost_usd is not None,
                provider_reported_cost_usd=reported_cost_usd,
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
            if settled_cost_usd > 0:
                # A route advertised as free has contradicted its verified profile.
                # Preserve the provider-reported amount or profile estimate, quarantine
                # the route, and fail closed rather than returning a result or rotating.
                self.quota_manager.ledger.cool_down(
                    profile.route_id, self.cooldown_seconds, "unexpected_nonzero_cost"
                )
                self._audit(
                    "route_quarantined", request, profile,
                    reason="unexpected_nonzero_cost",
                    cooldown_seconds=self.cooldown_seconds,
                    cost_usd=settled_cost_usd,
                    cost_basis=cost_basis,
                )
                raise ProviderUnavailable(
                    "Provider or profile non-zero cost detected for a verified-free route; route quarantined."
                )
            budget_status = self.usage_ledger.budget_status(
                self.daily_budget_usd, self.monthly_budget_usd,
                task_id=request.task_id, per_task_budget_usd=self.per_task_budget_usd,
            )
            if (budget_status["daily_over_budget"] or budget_status["monthly_over_budget"]
                    or budget_status["per_task_over_budget"]):
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
                "Provider adapters may not expose invoice-level billing. When cost_usd is null, "
                "the gateway estimates cost from actual token counts and the verified route price; "
                "the audit event records cost_basis. This is not an invoice-level measurement, so "
                "provider billing must be reconciled externally. Only verified zero-cost profiles "
                "are eligible and no paid fallback is allowed; any reported or profile-estimated "
                "non-zero cost is recorded and quarantines the route."
            ),
        }
