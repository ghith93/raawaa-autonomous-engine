"""Durable monetary budget ledger backed by the authenticated control Worker.

All spend is integer nano-USD in D1. Network failures never fall back to local SQLite.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import quote

from engine.model_gateway import BudgetExceeded, _money_nanos


class D1MonetaryLedgerError(RuntimeError):
    pass


class D1MonetaryLedger:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: int = 15):
        self.base_url = (base_url or os.getenv("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or os.getenv("RAAWAA_ENGINE_API_TOKEN", "")
        self.timeout = timeout
        if not self.base_url.startswith("https://") or not self.token:
            raise D1MonetaryLedgerError(
                "RAAWAA_ENGINE_API_URL (HTTPS) and RAAWAA_ENGINE_API_TOKEN are required"
            )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        data = None if payload is None else json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        access_id = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        access_secret = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        if bool(access_id) != bool(access_secret):
            raise D1MonetaryLedgerError("Cloudflare Access service-token secrets must be configured as a pair")
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
            error_code = None
            if exc.code == 409:
                try:
                    parsed = json.loads(exc.read().decode("utf-8"))
                    error_code = parsed.get("error") if isinstance(parsed, dict) else None
                except Exception:
                    error_code = None
            if error_code == "budget_exceeded":
                raise BudgetExceeded("Durable D1 monetary budget is exhausted.") from None
            raise D1MonetaryLedgerError(f"monetary Worker returned HTTP {exc.code}") from None
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
        if "error" in result:
            if result["error"] == "budget_exceeded":
                raise BudgetExceeded("Durable D1 monetary budget is exhausted.")
            raise D1MonetaryLedgerError(str(result["error"]))
        return result

    def _snapshot(self, task_id: str | None = None) -> dict:
        suffix = "" if task_id is None else "?task_id=" + quote(task_id, safe="")
        result = self._request("GET", "/v1/monetary-ledger" + suffix)
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, dict):
            raise D1MonetaryLedgerError("monetary Worker omitted its spend snapshot")
        return snapshot

    def totals(self) -> dict[str, float | int]:
        snapshot = self._snapshot()
        daily = int(snapshot["daily_spent_nanos"])
        monthly = int(snapshot["monthly_spent_nanos"])
        reserved = int(snapshot["pending_reserved_nanos"])
        return {
            "daily_usd": daily / 1_000_000_000,
            "monthly_usd": monthly / 1_000_000_000,
            "daily_reserved_usd": reserved / 1_000_000_000,
            "monthly_reserved_usd": reserved / 1_000_000_000,
            "daily_total_nanos": int(snapshot["daily_total_nanos"]),
            "monthly_total_nanos": int(snapshot["monthly_total_nanos"]),
            "pending_reservations": int(snapshot["pending_count"]),
        }

    def pending_reservations(self) -> list[dict[str, Any]]:
        return self._snapshot().get("unresolved", [])

    def task_totals(self, task_id: str) -> dict[str, float | int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required for task spend totals")
        snapshot = self._snapshot(task_id)
        actual = int(snapshot["task_spent_nanos"])
        reserved = int(snapshot["task_reserved_nanos"])
        return {
            "actual_usd": actual / 1_000_000_000,
            "reserved_usd": reserved / 1_000_000_000,
            "total_nanos": int(snapshot["task_total_nanos"]),
            "pending_reservations": int(snapshot["task_pending_count"]),
        }

    def budget_status(self, daily_budget_usd: float, monthly_budget_usd: float,
                      now=None, task_id: str | None = None,
                      per_task_budget_usd: float = 0.0) -> dict[str, bool]:
        daily_cap = _money_nanos(daily_budget_usd, round_up=False)
        monthly_cap = _money_nanos(monthly_budget_usd, round_up=False)
        task_cap = _money_nanos(per_task_budget_usd, round_up=False)
        if task_cap > 0 and task_id is None:
            raise ValueError("task_id is required when a per-task monetary budget is configured")
        totals = self.totals()
        task_total = self.task_totals(task_id)["total_nanos"] if task_id else 0
        return {
            "daily_over_budget": totals["daily_total_nanos"] > daily_cap,
            "monthly_over_budget": totals["monthly_total_nanos"] > monthly_cap,
            "per_task_over_budget": task_total > task_cap,
        }

    def reserve(self, provider: str, model: str, task_kind: str,
                estimated_cost_usd: float, daily_budget_usd: float,
                monthly_budget_usd: float, now=None, task_id: str | None = None,
                per_task_budget_usd: float = 0.0) -> str:
        if not task_id or not isinstance(task_id, str):
            raise ValueError("task_id is required for every monetary reservation")
        reservation_id = str(uuid.uuid4())
        result = self._request("POST", "/v1/monetary-ledger", {
            "action": "reserve",
            "reservation_id": reservation_id,
            "idempotency_key": str(uuid.uuid4()),
            "provider": provider,
            "model": model,
            "task_kind": task_kind,
            "task_id": task_id,
            "estimated_cost_nanos": _money_nanos(estimated_cost_usd, round_up=True),
            "daily_budget_nanos": _money_nanos(daily_budget_usd, round_up=False),
            "monthly_budget_nanos": _money_nanos(monthly_budget_usd, round_up=False),
            "per_task_budget_nanos": _money_nanos(per_task_budget_usd, round_up=False),
        })
        returned = result.get("reservation_id")
        if returned != reservation_id:
            raise D1MonetaryLedgerError("monetary Worker returned a mismatched reservation ID")
        return reservation_id

    def mark_started(self, reservation_id: str) -> None:
        self._request("POST", "/v1/monetary-ledger", {
            "action": "start", "reservation_id": reservation_id
        })

    def mark_ambiguous(self, reservation_id: str) -> None:
        self._request("POST", "/v1/monetary-ledger", {
            "action": "ambiguous", "reservation_id": reservation_id
        })

    def release(self, reservation_id: str) -> None:
        self._request("POST", "/v1/monetary-ledger", {
            "action": "release", "reservation_id": reservation_id
        })

    def settle(self, reservation_id: str, input_tokens: int, output_tokens: int,
               actual_cost_usd: float, now=None) -> dict[str, Any]:
        if type(input_tokens) is not int or input_tokens < 0 or type(output_tokens) is not int or output_tokens < 0:
            raise ValueError("token counts must be non-negative integers")
        return self._request("POST", "/v1/monetary-ledger", {
            "action": "settle",
            "reservation_id": reservation_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "actual_cost_nanos": _money_nanos(actual_cost_usd, round_up=True),
        })
