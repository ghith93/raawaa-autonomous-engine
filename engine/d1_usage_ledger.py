"""Durable monetary usage ledger backed by the authenticated control Worker.

Ambiguous transport failures never release reservations or fall back to local SQLite.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

from engine.model_gateway import BudgetExceeded, _money_nanos


class D1UsageLedgerError(RuntimeError):
    pass


class D1UsageLedger:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int = 15,
    ) -> None:
        self.base_url = (base_url or os.getenv("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or os.getenv("RAAWAA_ENGINE_API_TOKEN", "")
        self.timeout = timeout
        if not self.base_url.startswith("https://") or not self.token:
            raise D1UsageLedgerError(
                "RAAWAA_ENGINE_API_URL (HTTPS) and RAAWAA_ENGINE_API_TOKEN are required"
            )
        if type(timeout) is not int or not 1 <= timeout <= 30:
            raise ValueError("timeout must be an integer between 1 and 30")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        budget_conflict: bool = False,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        access_id = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        access_secret = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        if bool(access_id) != bool(access_secret):
            raise D1UsageLedgerError("Cloudflare Access service-token secrets must be configured as a pair")
        if access_id and access_secret:
            headers["CF-Access-Client-Id"] = access_id
            headers["CF-Access-Client-Secret"] = access_secret
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and budget_conflict:
                raise BudgetExceeded("Estimated request cost exceeds a durable monetary spend budget.") from None
            if exc.code == 404:
                raise KeyError("durable monetary reservation not found") from None
            if exc.code == 409:
                raise RuntimeError("durable monetary reservation state conflict") from None
            raise D1UsageLedgerError(f"usage Worker returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise D1UsageLedgerError(
                f"usage Worker transport failed: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1UsageLedgerError(
                f"usage Worker returned invalid JSON: {type(exc).__name__}"
            ) from None
        if not isinstance(result, dict):
            raise D1UsageLedgerError("usage Worker returned an invalid response")
        if "error" in result:
            if budget_conflict and result["error"] == "budget_exceeded":
                raise BudgetExceeded("Estimated request cost exceeds a durable monetary spend budget.")
            raise D1UsageLedgerError(str(result["error"]))
        return result

    @staticmethod
    def _non_negative_int(value: Any, name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    def totals(self, now=None) -> dict[str, float | int]:
        # The Worker owns time boundaries; callers cannot backdate cloud spend.
        result = self._request("GET", "/v1/usage")
        daily = int(result["daily_actual_nanos"])
        monthly = int(result["monthly_actual_nanos"])
        pending = int(result["pending_nanos"])
        return {
            "daily_usd": daily / 1_000_000_000,
            "monthly_usd": monthly / 1_000_000_000,
            "daily_reserved_usd": pending / 1_000_000_000,
            "monthly_reserved_usd": pending / 1_000_000_000,
            "daily_total_nanos": daily + pending,
            "monthly_total_nanos": monthly + pending,
            "pending_reservations": int(result["pending_count"]),
        }

    def pending_reservations(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/v1/usage?include_pending=true")
        return result.get("pending_reservations", [])

    def task_totals(self, task_id: str) -> dict[str, float | int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required for task spend totals")
        result = self._request("GET", f"/v1/usage?task_id={quote(task_id, safe='')}")
        actual = int(result["task_actual_nanos"])
        reserved = int(result["task_reserved_nanos"])
        return {
            "actual_usd": actual / 1_000_000_000,
            "reserved_usd": reserved / 1_000_000_000,
            "total_nanos": actual + reserved,
            "pending_reservations": int(result["task_pending_count"]),
        }

    def task_summaries(self, per_task_budget_usd: float = 0.0) -> list[dict[str, Any]]:
        cap = _money_nanos(per_task_budget_usd, round_up=False)
        result = self._request("GET", f"/v1/usage?include_tasks=true&task_cap_nanos={cap}")
        return result.get("task_summaries", [])

    def budget_status(
        self,
        daily_budget_usd: float,
        monthly_budget_usd: float,
        now=None,
        task_id: str | None = None,
        per_task_budget_usd: float = 0.0,
    ) -> dict[str, bool]:
        daily_cap = _money_nanos(daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(monthly_budget_usd, round_up=False)
        task_cap = _money_nanos(per_task_budget_usd, round_up=False)
        if task_cap > 0 and task_id is None:
            raise ValueError("task_id is required when a per-task monetary budget is configured")
        totals = self.totals()
        task_total = self.task_totals(task_id)["total_nanos"] if task_id is not None else 0
        return {
            "daily_over_budget": totals["daily_total_nanos"] > daily_cap,
            "monthly_over_budget": totals["monthly_total_nanos"] > monthly_cap,
            "per_task_over_budget": task_total > task_cap,
        }

    def reserve(
        self,
        provider: str,
        model: str,
        task_kind: str,
        estimated_cost_usd: float,
        daily_budget_usd: float,
        monthly_budget_usd: float,
        now=None,
        task_id: str | None = None,
        per_task_budget_usd: float = 0.0,
    ) -> int:
        if not all(isinstance(value, str) and value.strip() for value in (provider, model, task_kind)):
            raise ValueError("provider, model and task_kind are required")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a non-empty string when supplied")
        estimate = _money_nanos(estimated_cost_usd, round_up=True)
        daily_cap = _money_nanos(daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(monthly_budget_usd, round_up=False)
        task_cap = _money_nanos(per_task_budget_usd, round_up=False)
        if (estimate > 0 or task_cap > 0) and task_id is None:
            raise ValueError("task_id is required for non-zero-cost or per-task reservations")
        result = self._request("POST", "/v1/usage", {
            "action": "reserve",
            "provider": provider,
            "model": model,
            "task_kind": task_kind,
            "task_id": task_id,
            "estimated_cost_nanos": estimate,
            "daily_budget_nanos": daily_cap,
            "monthly_budget_nanos": monthly_cap,
            "per_task_budget_nanos": task_cap,
        }, budget_conflict=True)
        reservation_id = result.get("reservation_id")
        if type(reservation_id) is not int or reservation_id < 1:
            raise D1UsageLedgerError("usage Worker returned an invalid reservation ID")
        return reservation_id

    def release(self, reservation_id: int) -> None:
        self._non_negative_int(reservation_id, "reservation_id")
        if reservation_id < 1:
            raise ValueError("reservation_id must be positive")
        self._request("POST", "/v1/usage", {
            "action": "release", "reservation_id": reservation_id,
        })

    def settle(
        self,
        reservation_id: int,
        input_tokens: int,
        output_tokens: int,
        actual_cost_usd: float,
        now=None,
    ) -> dict[str, Any]:
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError("reservation_id must be positive")
        self._non_negative_int(input_tokens, "input_tokens")
        self._non_negative_int(output_tokens, "output_tokens")
        actual = _money_nanos(actual_cost_usd, round_up=True)
        result = self._request("POST", "/v1/usage", {
            "action": "settle",
            "reservation_id": reservation_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "actual_cost_nanos": actual,
        })
        return {
            "daily_usd": int(result["daily_actual_nanos"]) / 1_000_000_000,
            "monthly_usd": int(result["monthly_actual_nanos"]) / 1_000_000_000,
            "daily_reserved_usd": int(result["pending_nanos"]) / 1_000_000_000,
            "monthly_reserved_usd": int(result["pending_nanos"]) / 1_000_000_000,
            "daily_total_nanos": int(result["daily_actual_nanos"]) + int(result["pending_nanos"]),
            "monthly_total_nanos": int(result["monthly_actual_nanos"]) + int(result["pending_nanos"]),
        }

    def record(
        self,
        provider: str,
        model: str,
        task_kind: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        task_id: str | None = None,
    ) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (provider, model, task_kind)):
            raise ValueError("provider, model and task_kind are required")
        self._non_negative_int(input_tokens, "input_tokens")
        self._non_negative_int(output_tokens, "output_tokens")
        cost = _money_nanos(cost_usd, round_up=True)
        if cost > 0 and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id is required to record non-zero provider cost")
        self._request("POST", "/v1/usage", {
            "action": "record",
            "provider": provider,
            "model": model,
            "task_kind": task_kind,
            "task_id": task_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_nanos": cost,
        })
