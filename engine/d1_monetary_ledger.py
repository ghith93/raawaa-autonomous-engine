"""Durable monetary reservations backed by the authenticated control Worker.

Amounts are integer nano-USD. Transport/settlement ambiguity is fail-closed:
this adapter never falls back to ephemeral SQLite and never releases after I/O.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from engine.model_gateway import BudgetExceeded


class D1MonetaryLedgerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class D1MonetaryLedger:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: int = 15, environ=None):
        source = os.environ if environ is None else environ
        self.base_url = (base_url or source.get("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or source.get("RAAWAA_ENGINE_API_TOKEN", "")
        self.access_id = source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        self.access_secret = source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        self.timeout = timeout
        if not self.base_url.startswith("https://") or not self.token:
            raise D1MonetaryLedgerError("RAAWAA_ENGINE_API_URL (HTTPS) and RAAWAA_ENGINE_API_TOKEN are required")
        if bool(self.access_id) != bool(self.access_secret):
            raise D1MonetaryLedgerError("Cloudflare Access service-token secrets must be configured as a pair")

    @staticmethod
    def money_nanos(value: float | int) -> int:
        if type(value) not in (int, float):
            raise ValueError("USD amount must be numeric")
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount < 0:
                raise ValueError("USD amount must be finite and non-negative")
            nanos = int((amount * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING))
        except (InvalidOperation, OverflowError) as exc:
            raise ValueError("USD amount is invalid") from exc
        if nanos > 9_000_000_000_000_000:
            raise ValueError("USD amount exceeds the safe monetary ledger range")
        return nanos

    @staticmethod
    def _reservation_id(value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("reservation_id must be a UUID") from exc

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        if self.access_id and self.access_secret:
            headers["CF-Access-Client-Id"] = self.access_id
            headers["CF-Access-Client-Secret"] = self.access_secret
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = None
            try:
                body = json.loads(exc.read().decode("utf-8"))
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    code = body["error"]
            except Exception:
                pass
            if code == "budget_exceeded":
                raise BudgetExceeded("D1 monetary budget reservation was rejected by the durable budget policy") from None
            raise D1MonetaryLedgerError(
                f"monetary Worker returned HTTP {exc.code}" + (f" ({code})" if code else ""),
                status_code=exc.code, error_code=code,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise D1MonetaryLedgerError(
                f"monetary Worker transport failed: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1MonetaryLedgerError(
                f"monetary Worker returned invalid JSON: {type(exc).__name__}"
            ) from None
        if not isinstance(result, dict):
            raise D1MonetaryLedgerError("monetary Worker returned an invalid response")
        if isinstance(result.get("error"), str):
            code = result["error"]
            if code == "budget_exceeded":
                raise BudgetExceeded("D1 monetary budget reservation was rejected by the durable budget policy")
            raise D1MonetaryLedgerError(f"monetary Worker rejected request: {code}", error_code=code)
        return result

    def reserve(self, provider: str, model: str, task_kind: str,
                estimated_cost_usd: float, daily_budget_usd: float,
                monthly_budget_usd: float, now=None, task_id: str | None = None,
                per_task_budget_usd: float = 0.0) -> str:
        for name, value, maximum in (("provider", provider, 100), ("model", model, 200),
                                     ("task_kind", task_kind, 100), ("task_id", task_id, 200)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{name} is required and must be at most {maximum} characters")
        reservation_id = str(uuid.uuid4())
        result = self._request("POST", "/v1/monetary-ledger", {
            "action": "reserve",
            "reservation_id": reservation_id,
            "provider": provider,
            "model": model,
            "task_kind": task_kind,
            "task_id": task_id,
            "estimated_cost_nanos": self.money_nanos(estimated_cost_usd),
            "daily_cap_nanos": self.money_nanos(daily_budget_usd),
            "monthly_cap_nanos": self.money_nanos(monthly_budget_usd),
            "per_task_cap_nanos": self.money_nanos(per_task_budget_usd),
        })
        returned = result.get("reservation_id")
        if returned != reservation_id:
            raise D1MonetaryLedgerError("monetary Worker returned a mismatched reservation ID")
        return reservation_id

    def mark_started(self, reservation_id: str) -> None:
        self._request("POST", "/v1/monetary-ledger", {
            "action": "mark_started", "reservation_id": self._reservation_id(reservation_id),
        })

    def mark_ambiguous(self, reservation_id: str, reason: str = "transport_error") -> dict:
        allowed = {"provider_timeout", "transport_error", "provider_5xx", "invalid_response", "adapter_error", "settlement_uncertain"}
        if reason not in allowed:
            raise ValueError("unsupported ambiguous reservation reason")
        return self._request("POST", "/v1/monetary-ledger", {
            "action": "mark_ambiguous", "reservation_id": self._reservation_id(reservation_id), "reason": reason,
        })

    def settle(self, reservation_id: str, input_tokens: int, output_tokens: int,
               actual_cost_usd: float, now=None, cost_basis: str = "provider_reported") -> dict:
        if type(input_tokens) is not int or input_tokens < 0 or type(output_tokens) is not int or output_tokens < 0:
            raise ValueError("token counts must be non-negative integers")
        if cost_basis not in {"provider_reported", "verified_profile_estimate", "verified_profile_floor", "provider_rejected_429"}:
            raise ValueError("unsupported monetary cost basis")
        return self._request("POST", "/v1/monetary-ledger", {
            "action": "settle",
            "reservation_id": self._reservation_id(reservation_id),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "actual_cost_nanos": self.money_nanos(actual_cost_usd),
            "cost_basis": cost_basis,
        })

    def release(self, reservation_id: str, reason: str = "pre_provider_validation_failed") -> dict:
        allowed = {"quota_reservation_failed", "pre_provider_validation_failed", "provider_not_called"}
        if reason not in allowed:
            raise ValueError("unsupported pre-provider release reason")
        return self._request("POST", "/v1/monetary-ledger", {
            "action": "release", "reservation_id": self._reservation_id(reservation_id), "reason": reason,
        })

    def status(self, task_id: str | None = None) -> dict:
        query = "" if task_id is None else "?task_id=" + urllib.parse.quote(task_id, safe="")
        return self._request("GET", "/v1/monetary-ledger" + query)

    def totals(self) -> dict[str, Any]:
        status = self.status()
        return {
            "daily_usd": status["daily"]["settled_usd"],
            "monthly_usd": status["monthly"]["settled_usd"],
            "daily_reserved_usd": status["daily"]["pending_usd"],
            "monthly_reserved_usd": status["monthly"]["pending_usd"],
            "pending_reservations": status["pending"]["count"],
            "ambiguous_reservations": status["pending"]["ambiguous_count"],
            "daily_total_nanos": status["daily"]["total_nanos"],
            "monthly_total_nanos": status["monthly"]["total_nanos"],
        }

    def budget_status(self, daily_budget_usd: float, monthly_budget_usd: float,
                      task_id: str | None = None, per_task_budget_usd: float | None = None) -> dict[str, Any]:
        status = self.status(task_id)
        expected = [self.money_nanos(daily_budget_usd), self.money_nanos(monthly_budget_usd)]
        actual = [status["caps"]["daily_nanos"], status["caps"]["monthly_nanos"]]
        if per_task_budget_usd is not None:
            expected.append(self.money_nanos(per_task_budget_usd))
            actual.append(status["caps"]["per_task_nanos"])
        if expected != actual:
            raise D1MonetaryLedgerError("local monetary policy does not match the immutable D1 budget policy")
        per_task = status.get("per_task")
        return {
            "daily_over_budget": status["daily"]["over_budget"],
            "monthly_over_budget": status["monthly"]["over_budget"],
            "per_task_over_budget": bool(per_task and per_task["over_budget"]),
            "daily_total_nanos": status["daily"]["total_nanos"],
            "monthly_total_nanos": status["monthly"]["total_nanos"],
            "task_total_nanos": per_task["total_nanos"] if per_task else 0,
            "pending_reservations": status["pending"]["count"],
            "ambiguous_reservations": status["pending"]["ambiguous_count"],
        }

    def task_totals(self, task_id: str) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        return self.status(task_id).get("per_task") or {}

    def pending_reservations(self) -> int:
        return int(self.status()["pending"]["count"])

