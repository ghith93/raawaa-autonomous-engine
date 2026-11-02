"""Authenticated D1-backed paper-event ledger client; never falls back to local SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


class D1PaperLedgerError(RuntimeError):
    pass


MAX_SAFE_JSON_INTEGER = (2**53) - 1
MAX_RAW_INTEGER = (2**256) - 1
RAW_FIELDS = {
    "gross_profit_raw": True,
    "gas_used": False,
    "gas_price_wei": False,
    "gas_cost_profit_raw": False,
    "net_profit_raw": True,
}


def _validate_payload(value: Any, *, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("payload nesting exceeds 32 levels")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("payload integers must be JavaScript-safe; store large raw values as strings")
        return
    if isinstance(value, float):
        raise ValueError("floating-point payload values are not supported; use decimal strings")
    if isinstance(value, list):
        for item in value:
            _validate_payload(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload object keys must be strings")
            _validate_payload(item, depth=depth + 1)
        return
    raise ValueError("payload contains a non-JSON value")


def _normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if not re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", value, re.IGNORECASE):
            raise ValueError("occurred_at string must include an explicit timezone")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be a valid ISO-8601 timestamp") from exc
    else:
        raise ValueError("occurred_at must be a timezone-aware datetime or ISO string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _normalize_raw_fields(event: dict[str, Any]) -> None:
    for name, signed in RAW_FIELDS.items():
        value = event.get(name)
        if value is None:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            raise ValueError(f"{name} must be an integer or canonical decimal string")
        pattern = r"-?(0|[1-9][0-9]*)" if signed else r"(0|[1-9][0-9]*)"
        if not re.fullmatch(pattern, text):
            raise ValueError(f"{name} must be a canonical decimal integer")
        number = int(text)
        if abs(number) > MAX_RAW_INTEGER or (not signed and number < 0):
            raise ValueError(f"{name} is outside the supported 256-bit raw-unit range")
        if name == "gas_used" and number <= 0:
            raise ValueError("gas_used must be positive")
        event[name] = text


class D1PaperTradingLedger:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int = 15):
        self.base_url = (base_url or os.getenv("RAAWAA_ENGINE_API_URL", "")).rstrip("/")
        self.token = token or os.getenv("RAAWAA_ENGINE_API_TOKEN", "")
        self.timeout = timeout
        if not self.base_url.startswith("https://") or not self.token:
            raise D1PaperLedgerError("RAAWAA_ENGINE_API_URL (HTTPS) and RAAWAA_ENGINE_API_TOKEN are required")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        data = None if payload is None else json.dumps(
            payload, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        access_id = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID", "")
        access_secret = os.getenv("RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET", "")
        if bool(access_id) != bool(access_secret):
            raise D1PaperLedgerError("Cloudflare Access service-token secrets must be configured as a pair")
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
            raise D1PaperLedgerError(f"paper ledger Worker returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise D1PaperLedgerError(f"paper ledger Worker transport failed: {type(exc).__name__}") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1PaperLedgerError(f"paper ledger Worker returned invalid JSON: {type(exc).__name__}") from None
        if not isinstance(result, dict):
            raise D1PaperLedgerError("paper ledger Worker returned an invalid response")
        if "error" in result:
            raise D1PaperLedgerError(str(result["error"]))
        return result

    def append_event(self, **event: Any) -> dict[str, Any]:
        normalized = dict(event)
        if "occurred_at" not in normalized:
            raise ValueError("occurred_at is required")
        normalized["occurred_at"] = _normalize_timestamp(normalized["occurred_at"])
        payload = normalized.pop("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        _validate_payload(payload)
        _normalize_raw_fields(normalized)
        return self._request("POST", "/v1/paper-ledger", {
            "action": "append", "event": {**normalized, "payload": payload},
        })

    def events(self, candidate_id: str, limit: int = 500) -> list[dict[str, Any]]:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id is required")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query = urllib.parse.urlencode({"candidate_id": candidate_id, "limit": limit})
        events = self._request("GET", "/v1/paper-ledger?" + query).get("events", [])
        if not isinstance(events, list):
            raise D1PaperLedgerError("paper ledger Worker returned an invalid event list")
        return events

    def verify_chain(self, candidate_id: str, limit: int = 500) -> bool:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id is required")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query = urllib.parse.urlencode({"candidate_id": candidate_id, "limit": limit})
        result = self._request("GET", "/v1/paper-ledger?" + query)
        events = result.get("events", [])
        checkpoints = result.get("checkpoints", [])
        head_sequence = result.get("head_sequence")
        if (
            not isinstance(events, list) or not isinstance(checkpoints, list)
            or len(events) != len(checkpoints)
            or type(head_sequence) is not int
            or result.get("event_count") != head_sequence
            or result.get("checkpoint_count") != head_sequence
            or len(events) != head_sequence
        ):
            return False
        fields = (
            "candidate_id", "event_id", "event_type", "occurred_at", "chain_id",
            "block_number", "block_hash", "strategy_id", "source_pool",
            "destination_pool", "token_in", "token_out", "gross_profit_raw",
            "gas_used", "gas_price_wei", "gas_cost_profit_raw", "net_profit_raw",
            "payload_json",
        )
        previous_hash = "0" * 64
        for sequence, (event, checkpoint) in enumerate(zip(events, checkpoints, strict=True), start=1):
            if not isinstance(event, dict) or not isinstance(checkpoint, dict):
                return False
            if event.get("sequence") != sequence or event.get("candidate_id") != candidate_id:
                return False
            if event.get("previous_hash") != previous_hash:
                return False
            if (
                checkpoint.get("sequence") != sequence
                or checkpoint.get("event_id") != event.get("event_id")
                or checkpoint.get("event_hash") != event.get("event_hash")
            ):
                return False
            record = {key: event.get(key) for key in fields}
            envelope = {"previous_hash": previous_hash, "record": {"sequence": sequence, **record}}
            canonical = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
            ).encode("utf-8")
            expected = hashlib.sha256(canonical).hexdigest()
            if event.get("event_hash") != expected:
                return False
            previous_hash = expected
        return previous_hash == result.get("head_hash")
