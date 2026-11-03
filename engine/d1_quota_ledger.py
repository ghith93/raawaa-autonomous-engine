"""Durable quota ledger backed by the authenticated Raawaa control Worker.

Network/ambiguous failures raise and never fall back to a temporary local ledger.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


class D1QuotaLedgerError(RuntimeError):
    pass


class D1QuotaLedger:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: int = 15, environ=None):
        source = os.environ if environ is None else environ
        self.base_url = (base_url or source.get("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or source.get("RAAWAA_ENGINE_API_TOKEN", "")
        self.access_id = source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        self.access_secret = source.get("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        self.timeout = timeout
        if not self.base_url.startswith("https://") or not self.token:
            raise D1QuotaLedgerError(
                "RAAWAA_ENGINE_API_URL (HTTPS) and RAAWAA_ENGINE_API_TOKEN are required"
            )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        # workers.dev Browser Integrity Check has rejected urllib's default
        # Python User-Agent in this project. Match the already-verified task
        # client identity so the new quota endpoint uses the same edge path.
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        access_id = self.access_id
        access_secret = self.access_secret
        if bool(access_id) != bool(access_secret):
            raise D1QuotaLedgerError("Cloudflare Access service-token secrets must be configured as a pair")
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
            # Do not expose response bodies: they are untrusted and may echo data.
            raise D1QuotaLedgerError(f"quota Worker returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # In particular, a lost settle response is ambiguous. The caller must
            # stop rather than release/recreate a reservation or use local SQLite.
            raise D1QuotaLedgerError(
                f"quota Worker transport failed: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1QuotaLedgerError(f"quota Worker returned invalid JSON: {type(exc).__name__}") from None
        if not isinstance(result, dict):
            raise D1QuotaLedgerError("quota Worker returned an invalid response")
        if "error" in result:
            raise D1QuotaLedgerError(str(result["error"]))
        return result

    @staticmethod
    def _stamp(now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

    def _snapshot(self, route_id: str, task_id: str = "read-only") -> dict:
        from urllib.parse import quote
        return self._request("GET", f"/v1/quota?route_id={quote(route_id)}&task_id={quote(task_id)}")

    def usage_today(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        return self._snapshot(route_id)["usage"]["daily"]

    def usage_minute(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        return self._snapshot(route_id)["usage"]["minute"]

    def usage_month(self, route_id: str, now: datetime | None = None) -> dict[str, int]:
        return self._snapshot(route_id)["usage"]["monthly"]

    def usage_for_task(self, task_id: str) -> dict[str, int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        # Aggregate task usage across all routes through the dedicated task view.
        from urllib.parse import quote
        result = self._request("GET", f"/v1/quota?route_id=__no_route__&task_id={quote(task_id)}")
        return result["usage"]["task"]

    def reserve(self, route_id: str, estimated_tokens: int, request_limit: int,
                token_limit: int, now: datetime | None = None,
                monthly_request_limit: int | None = None,
                monthly_token_limit: int | None = None,
                task_id: str | None = None,
                per_task_request_limit: int | None = None,
                per_task_token_limit: int | None = None,
                minute_request_limit: int | None = None,
                minute_token_limit: int | None = None) -> int:
        required = (monthly_request_limit, monthly_token_limit, task_id,
                    per_task_request_limit, per_task_token_limit,
                    minute_request_limit, minute_token_limit)
        if any(value is None for value in required):
            raise ValueError("all daily, monthly, minute, and per-task limits plus task_id are required")
        result = self._request("POST", "/v1/quota", {
            "action": "reserve", "route_id": route_id, "estimated_tokens": estimated_tokens,
            "daily_request_limit": request_limit, "daily_token_limit": token_limit,
            "monthly_request_limit": monthly_request_limit,
            "monthly_token_limit": monthly_token_limit, "task_id": task_id,
            "per_task_request_limit": per_task_request_limit,
            "per_task_token_limit": per_task_token_limit,
            "minute_request_limit": minute_request_limit,
            "minute_token_limit": minute_token_limit,
        })
        return int(result["reservation_id"])

    def settle(self, reservation_id: int, actual_tokens: int) -> None:
        if type(reservation_id) is not int or reservation_id < 1 or type(actual_tokens) is not int or actual_tokens < 0:
            raise ValueError("invalid reservation settlement")
        self._request("POST", "/v1/quota", {
            "action": "settle", "reservation_id": reservation_id, "actual_tokens": actual_tokens
        })

    def record(self, route_id: str, tokens: int, now: datetime | None = None) -> None:
        if type(tokens) is not int or tokens < 0:
            raise ValueError("tokens cannot be negative")
        self._request("POST", "/v1/quota", {"action": "record", "route_id": route_id, "tokens": tokens})

    def cool_down(self, route_id: str, seconds: int, reason: str = "rate_limited",
                  now: datetime | None = None) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ValueError("cooldown must be positive")
        self._request("POST", "/v1/quota", {
            "action": "cool_down", "route_id": route_id, "seconds": seconds, "reason": reason
        })

    def cooldown_active(self, route_id: str, now: datetime | None = None) -> bool:
        result = self._snapshot(route_id)
        cooldown = result.get("cooldown")
        return bool(cooldown and datetime.fromisoformat(cooldown["until_at"]) > (now or datetime.now(timezone.utc)).astimezone(timezone.utc))

    def audit_event(self, event: str, route_id: str | None = None,
                    task_kind: str | None = None, details: dict | None = None,
                    now: datetime | None = None) -> int:
        result = self._request("POST", "/v1/quota", {
            "action": "audit_event", "event": event, "route_id": route_id,
            "task_kind": task_kind, "details": details or {},
        })
        return int(result["id"])

    def audit_events(self, limit: int = 100) -> list[dict]:
        result = self._request("GET", f"/v1/quota?limit={max(1, min(int(limit), 500))}")
        return result.get("audit", [])
