"""Authenticated D1-backed paper-event ledger client; never falls back to local SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


class D1PaperLedgerError(RuntimeError):
    pass


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
        occurred_at = event.get("occurred_at")
        if isinstance(occurred_at, datetime):
            if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                raise ValueError("occurred_at must be timezone-aware")
            event["occurred_at"] = occurred_at.astimezone(timezone.utc).isoformat()
        elif not isinstance(occurred_at, str):
            raise ValueError("occurred_at must be a timezone-aware datetime or ISO string")
        payload = event.pop("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return self._request("POST", "/v1/paper-ledger", {
            "action": "append", "event": {**event, "payload": payload},
        })

    def events(self, candidate_id: str, limit: int = 500) -> list[dict[str, Any]]:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id is required")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query = urllib.parse.urlencode({"candidate_id": candidate_id, "limit": limit})
        return self._request("GET", "/v1/paper-ledger?" + query).get("events", [])

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
            or len(events) >= limit
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
