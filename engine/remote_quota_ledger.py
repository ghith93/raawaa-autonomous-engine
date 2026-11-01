"""Authenticated D1-backed quota ledger shared by all scheduled workers.

The server owns timestamps and atomic reservations. This adapter never sends
prompts, completions, or API-key values to the control Worker.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

from engine.remote_task_store import RemoteTaskStore


class RemoteQuotaLedgerError(RuntimeError):
    """Remote quota API failure with a sanitized message."""


class RemoteQuotaLedger(RemoteTaskStore):
    def _quota_request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)",
        }
        if self.access_client_id and self.access_client_secret:
            headers["CF-Access-Client-Id"] = self.access_client_id
            headers["CF-Access-Client-Secret"] = self.access_client_secret
        request = Request(self.base_url + path, data=body, method="POST", headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read(64 * 1024 + 1)
                if len(data) > 64 * 1024:
                    raise RemoteQuotaLedgerError("Remote quota API response exceeded limit")
                result = json.loads(data.decode("utf-8"))
                if not isinstance(result, dict):
                    raise RemoteQuotaLedgerError("Remote quota API returned invalid data")
                return result
        except HTTPError as exc:
            if exc.code == 409:
                raise RuntimeError("Remote quota capacity or settlement conflict") from None
            if exc.code == 404:
                raise KeyError("Remote quota reservation not found") from None
            if exc.code == 401:
                raise RemoteQuotaLedgerError("Remote quota API authentication failed") from None
            if exc.code == 429:
                raise RemoteQuotaLedgerError("Remote quota API rate limit reached") from None
            raise RemoteQuotaLedgerError(
                f"Remote quota API returned HTTP {exc.code}"
            ) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise RemoteQuotaLedgerError(
                "Remote quota API is unavailable or returned invalid JSON"
            ) from None

    @staticmethod
    def _usage(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise RemoteQuotaLedgerError("Remote quota API omitted a usage window")
        requests, tokens = value.get("requests"), value.get("tokens")
        if type(requests) is not int or type(tokens) is not int or min(requests, tokens) < 0:
            raise RemoteQuotaLedgerError("Remote quota API returned invalid usage counts")
        return {"requests": requests, "tokens": tokens}

    def usage_snapshot(self, route_id: str, now=None) -> dict[str, Any]:
        # The D1 Worker owns time; client clocks cannot move or reset quota windows.
        result = self._quota_request("/v1/quota/usage-snapshot", {"route_id": route_id})
        cooldown = result.get("cooldown_active")
        if type(cooldown) is not bool:
            raise RemoteQuotaLedgerError("Remote quota API returned invalid cooldown state")
        return {
            "minute": self._usage(result.get("minute")),
            "daily": self._usage(result.get("daily")),
            "monthly": self._usage(result.get("monthly")),
            "cooldown_active": cooldown,
        }

    def usage_minute(self, route_id: str, now=None) -> dict[str, int]:
        return self.usage_snapshot(route_id)["minute"]

    def usage_today(self, route_id: str, now=None) -> dict[str, int]:
        return self.usage_snapshot(route_id)["daily"]

    def usage_month(self, route_id: str, now=None) -> dict[str, int]:
        return self.usage_snapshot(route_id)["monthly"]

    def cooldown_active(self, route_id: str, now=None) -> bool:
        return self.usage_snapshot(route_id)["cooldown_active"]

    def reserve(
        self, route_id: str, estimated_tokens: int, request_limit: int,
        token_limit: int, now=None, monthly_request_limit: int | None = None,
        monthly_token_limit: int | None = None, task_id: str | None = None,
        per_task_request_limit: int | None = None,
        per_task_token_limit: int | None = None,
        minute_request_limit: int | None = None,
        minute_token_limit: int | None = None,
    ) -> str:
        result = self._quota_request("/v1/quota/reserve", {
            "route_id": route_id,
            "estimated_tokens": estimated_tokens,
            "daily_request_limit": request_limit,
            "daily_token_limit": token_limit,
            "monthly_request_limit": monthly_request_limit,
            "monthly_token_limit": monthly_token_limit,
            "task_id": task_id,
            "per_task_request_limit": per_task_request_limit,
            "per_task_token_limit": per_task_token_limit,
            "minute_request_limit": minute_request_limit,
            "minute_token_limit": minute_token_limit,
        })
        reservation_id = result.get("reservation_id")
        if not isinstance(reservation_id, str) or not reservation_id:
            raise RemoteQuotaLedgerError("Remote quota API omitted reservation ID")
        return reservation_id

    def settle(self, reservation_id: int | str, actual_tokens: int) -> None:
        self._quota_request("/v1/quota/settle", {
            "reservation_id": str(reservation_id),
            "actual_tokens": actual_tokens,
        })

    def cool_down(self, route_id: str, seconds: int, reason: str = "rate_limited", now=None) -> None:
        self._quota_request("/v1/quota/cooldown", {
            "route_id": route_id, "seconds": seconds, "reason": reason,
        })

    def audit_event(self, event: str, route_id: str | None = None,
                    task_kind: str | None = None, details: dict | None = None,
                    now=None) -> int:
        result = self._quota_request("/v1/quota/audit", {
            "event": event, "route_id": route_id, "task_kind": task_kind,
            "details": details or {},
        })
        event_id = result.get("id")
        if type(event_id) is not int:
            raise RemoteQuotaLedgerError("Remote quota API omitted audit event ID")
        return event_id
