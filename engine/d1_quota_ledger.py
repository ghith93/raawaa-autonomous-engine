"""Durable provider quota ledger backed by the authenticated control Worker and D1.

Drop-in replacement for ``engine.quota_manager.QuotaLedger``. GitHub Actions
runners are ephemeral, so a local SQLite ledger forgets usage and 429 cooldowns
between scheduled runs. This adapter keeps that state in D1 instead.

Guarantees:
* Reservations are one atomic conditional INSERT inside the Worker; the
  caller never reads-then-writes quota state.
* All timestamps come from the Worker clock. ``now`` arguments are accepted
  only for interface compatibility and are ignored.
* Reservations carry a client-generated idempotency key, so a retry after an
  ambiguous network failure can never reserve twice.
* Every failure is fail-closed: callers see an exception, never a silent grant.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError

_AUDIT_DETAIL_KEYS = {
    "reason", "error_type", "http_status", "estimated_tokens",
    "actual_tokens", "cooldown_seconds", "cost_usd",
}
_RESERVE_LIMIT_FIELDS = (
    ("estimated_tokens", "estimated_tokens"),
    ("request_limit", "request_limit"),
    ("token_limit", "token_limit"),
    ("monthly_request_limit", "monthly_request_limit"),
    ("monthly_token_limit", "monthly_token_limit"),
    ("per_task_request_limit", "per_task_request_limit"),
    ("per_task_token_limit", "per_task_token_limit"),
    ("minute_request_limit", "minute_request_limit"),
    ("minute_token_limit", "minute_token_limit"),
)


class D1QuotaLedger:
    def __init__(self, store: RemoteTaskStore | Any | None = None):
        self.store = store if store is not None else RemoteTaskStore()

    # -- transport -----------------------------------------------------
    def _call(self, path: str, payload: dict[str, Any], attempts: int = 2) -> dict[str, Any]:
        """POST with one retry. Only used for idempotent or read-only endpoints."""
        last: RemoteTaskStoreError | None = None
        for _ in range(attempts):
            try:
                return self.store._request(path, "POST", payload)
            except RemoteTaskStoreError as exc:
                last = exc
        assert last is not None
        raise last

    @staticmethod
    def _route(route_id: str) -> str:
        if not isinstance(route_id, str) or not route_id.strip():
            raise ValueError("route_id is required")
        return route_id

    @staticmethod
    def _window(data: dict[str, Any], key: str) -> dict[str, int]:
        window = data.get(key)
        if (not isinstance(window, dict) or type(window.get("requests")) is not int
                or type(window.get("tokens")) is not int):
            raise RemoteTaskStoreError("Remote quota API returned an invalid usage window")
        return {"requests": window["requests"], "tokens": window["tokens"]}

    def _status(self, route_id: str) -> dict[str, Any]:
        return self._call("/v1/quota/status", {"route_id": self._route(route_id)})

    # -- usage windows -------------------------------------------------
    def usage_today(self, route_id: str, now=None) -> dict[str, int]:
        return self._window(self._status(route_id), "day")

    def usage_minute(self, route_id: str, now=None) -> dict[str, int]:
        return self._window(self._status(route_id), "minute")

    def usage_month(self, route_id: str, now=None) -> dict[str, int]:
        return self._window(self._status(route_id), "month")

    def usage_for_task(self, task_id: str) -> dict[str, int]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id is required")
        data = self._call("/v1/quota/task-usage", {"task_id": task_id})
        if type(data.get("requests")) is not int or type(data.get("tokens")) is not int:
            raise RemoteTaskStoreError("Remote quota API returned invalid task usage")
        return {"requests": data["requests"], "tokens": data["tokens"]}

    # -- reservations --------------------------------------------------
    def reserve(self, route_id: str, estimated_tokens: int, request_limit: int,
                token_limit: int, now=None,
                monthly_request_limit: int | None = None,
                monthly_token_limit: int | None = None,
                task_id: str | None = None,
                per_task_request_limit: int | None = None,
                per_task_token_limit: int | None = None,
                minute_request_limit: int | None = None,
                minute_token_limit: int | None = None) -> int:
        """Atomically reserve one request and its estimated tokens before network I/O."""
        values = {
            "estimated_tokens": estimated_tokens, "request_limit": request_limit,
            "token_limit": token_limit, "monthly_request_limit": monthly_request_limit,
            "monthly_token_limit": monthly_token_limit,
            "per_task_request_limit": per_task_request_limit,
            "per_task_token_limit": per_task_token_limit,
            "minute_request_limit": minute_request_limit,
            "minute_token_limit": minute_token_limit,
        }
        if (any(type(values[name]) is not int or values[name] < 1
                for name, _ in _RESERVE_LIMIT_FIELDS)
                or not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("reservation limits and estimated tokens must be positive integers")
        payload = {"route_id": self._route(route_id), "task_id": task_id,
                   "idempotency_key": str(uuid.uuid4()), **values}
        # The idempotency key makes this retry safe even if the first attempt
        # reached D1 but its response was lost.
        data = self._call("/v1/quota/reserve", payload)
        if data.get("reserved") is True and type(data.get("reservation_id")) is int:
            return data["reservation_id"]
        if data.get("reserved") is False:
            raise RuntimeError("quota reservation unavailable")
        raise RemoteTaskStoreError("Remote quota API returned an invalid reservation")

    def settle(self, reservation_id: int, actual_tokens: int) -> None:
        """Settle once; an identical retry is safe, conflicting retries fail closed."""
        if type(reservation_id) is not int or reservation_id < 1:
            raise ValueError("reservation_id must be a positive integer")
        if type(actual_tokens) is not int or actual_tokens < 0:
            raise ValueError("actual_tokens cannot be negative")
        data = self._call("/v1/quota/settle", {
            "reservation_id": reservation_id, "actual_tokens": actual_tokens,
        })
        if data.get("ok") is True:
            return
        if data.get("error") == "not_found":
            raise KeyError("quota reservation not found")
        if data.get("error") == "conflict":
            raise RuntimeError("quota reservation already settled with different usage")
        raise RemoteTaskStoreError("Remote quota API returned an invalid settlement")

    def record(self, route_id: str, tokens: int, now=None) -> None:
        if type(tokens) is not int or tokens < 0:
            raise ValueError("tokens cannot be negative")
        # One retry could double count a lost response; record() is not
        # idempotent, so it gets a single attempt and fails closed.
        data = self._call("/v1/quota/record", {"route_id": self._route(route_id), "tokens": tokens},
                          attempts=1)
        if data.get("ok") is not True:
            raise RemoteTaskStoreError("Remote quota API rejected the usage record")

    # -- cooldowns -----------------------------------------------------
    def cool_down(self, route_id: str, seconds: int, reason: str = "rate_limited",
                  now=None) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ValueError("cooldown must be positive")
        data = self._call("/v1/quota/cooldown", {
            "route_id": self._route(route_id), "seconds": seconds, "reason": str(reason)[:120] or "rate_limited",
        })
        if data.get("ok") is not True:
            raise RemoteTaskStoreError("Remote quota API rejected the cooldown")

    def cooldown_active(self, route_id: str, now=None) -> bool:
        active = self._status(route_id).get("cooldown_active")
        if type(active) is not bool:
            raise RemoteTaskStoreError("Remote quota API returned an invalid cooldown state")
        return active

    # -- audit ---------------------------------------------------------
    def audit_event(self, event: str, route_id: str | None = None,
                    task_kind: str | None = None, details: dict | None = None,
                    now=None) -> int:
        """Persist metadata-only audit events; never prompts or outputs.

        Best effort: an unreachable audit endpoint must not discard a provider
        response that was already paid for in quota, so failures return 0.
        """
        if not isinstance(event, str) or not event or len(event) > 80:
            raise ValueError("audit event name is invalid")
        safe: dict[str, Any] = {}
        for key, value in (details or {}).items():
            if key not in _AUDIT_DETAIL_KEYS:
                continue
            if isinstance(value, str):
                safe[key] = value[:100]
            elif type(value) in {int, float, bool}:
                safe[key] = value
        payload = {
            "event": event,
            "route_id": route_id[:200] if isinstance(route_id, str) else None,
            "task_kind": task_kind[:100] if isinstance(task_kind, str) else None,
            "details": safe,
        }
        try:
            data = self._call("/v1/quota/audit", payload)
        except RemoteTaskStoreError:
            return 0
        return data.get("id") if type(data.get("id")) is int else 0

    def audit_events(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        data = self._call("/v1/quota/audit/list", {"limit": limit})
        events = data.get("events")
        if not isinstance(events, list):
            raise RemoteTaskStoreError("Remote quota API returned an invalid audit list")
        return events
