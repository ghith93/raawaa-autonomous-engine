from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .paper_ledger import METADATA_FIELDS, PaperTradingLedger

RECORD_FIELDS = (*METADATA_FIELDS, "payload_json")
MAX_EVENTS = 500


class D1PaperTradingLedger:
    """Narrow HTTPS client for the append-only D1 paper-event API.

    It intentionally exposes only append and chain verification; there is no SQL or
    arbitrary Worker endpoint support. Network/configuration errors fail closed.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout_seconds: float = 10.0,
        transport: Callable[..., tuple[int, bytes]] | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("D1 paper ledger endpoint must be a clean HTTPS URL")
        if not isinstance(token, str) or not token.strip() or any(ch.isspace() for ch in token):
            raise ValueError("D1 paper ledger bearer token is required")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport or self._urlopen

    def _urlopen(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, response.read(32_768 + 1)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(32_768 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("D1 paper ledger transport failed") from exc

    def _request(self, method: str, *, query: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.endpoint + "/v1/paper-ledger"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, url, headers, body)
        if len(raw) > 32_768:
            raise RuntimeError("D1 paper ledger response exceeded the size limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("D1 paper ledger returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("D1 paper ledger returned an invalid response")
        if status < 200 or status >= 300:
            # Do not include response bodies: they may contain sensitive server details.
            raise RuntimeError(f"D1 paper ledger request failed with HTTP {status}")
        return data

    @staticmethod
    def _record_from_args(**kwargs: Any) -> dict[str, Any]:
        occurred_at = kwargs.get("occurred_at")
        if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        timestamp = occurred_at.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        timestamp = timestamp.replace("+00:00", "+00:00")
        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        record = {
            "candidate_id": kwargs["candidate_id"],
            "event_id": kwargs["event_id"],
            "event_type": kwargs["event_type"],
            "occurred_at": timestamp,
            "chain_id": kwargs.get("chain_id"),
            "block_number": kwargs.get("block_number"),
            "block_hash": kwargs.get("block_hash"),
            "strategy_id": kwargs.get("strategy_id"),
            "source_pool": kwargs.get("source_pool"),
            "destination_pool": kwargs.get("destination_pool"),
            "token_in": kwargs.get("token_in"),
            "token_out": kwargs.get("token_out"),
            "gross_profit_raw": kwargs.get("gross_profit_raw"),
            "gas_used": kwargs.get("gas_used"),
            "gas_price_wei": kwargs.get("gas_price_wei"),
            "gas_cost_profit_raw": kwargs.get("gas_cost_profit_raw"),
            "net_profit_raw": kwargs.get("net_profit_raw"),
            "payload": payload,
        }
        return record

    def append_event(self, **kwargs: Any) -> bool:
        record = self._record_from_args(**kwargs)
        # The Worker requires raw integer economics as decimal strings.
        for field in (
            "gross_profit_raw", "gas_used", "gas_price_wei",
            "gas_cost_profit_raw", "net_profit_raw",
        ):
            value = record[field]
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{field} must be an integer")
                record[field] = str(value)
        data = self._request("POST", payload={"action": "append", "event": record})
        if data.get("event_id") != record["event_id"] or not isinstance(data.get("event_hash"), str):
            raise RuntimeError("D1 paper ledger append response did not match the requested event")
        if data.get("replay") not in (True, False):
            raise RuntimeError("D1 paper ledger append response omitted replay status")
        return not data["replay"]

    def verify_chain(self, candidate_id: str) -> bool:
        data = self._request(
            "GET",
            query={"candidate_id": candidate_id, "limit": str(MAX_EVENTS)},
        )
        events = data.get("events")
        checkpoints = data.get("checkpoints")
        if not isinstance(events, list) or not isinstance(checkpoints, list):
            raise RuntimeError("D1 paper ledger verification response is malformed")
        if len(events) != len(checkpoints) or len(events) > MAX_EVENTS:
            return False
        previous_hash = "0" * 64
        for sequence, (event, checkpoint) in enumerate(zip(events, checkpoints, strict=True), start=1):
            if not isinstance(event, dict) or not isinstance(checkpoint, dict):
                return False
            if event.get("sequence") != sequence:
                return False
            if event.get("previous_hash") != previous_hash:
                return False
            if (
                checkpoint.get("sequence") != sequence
                or checkpoint.get("event_id") != event.get("event_id")
                or checkpoint.get("event_hash") != event.get("event_hash")
            ):
                return False
            record = {key: event.get(key) for key in RECORD_FIELDS}
            expected = PaperTradingLedger._hash_record({"sequence": sequence, **record}, previous_hash)
            if event.get("event_hash") != expected:
                return False
            previous_hash = expected
        if data.get("head_sequence") != len(events) or data.get("event_count") != len(events):
            return False
        if data.get("checkpoint_count") != len(checkpoints) or data.get("head_hash") != previous_hash:
            return False
        return True


def create_paper_ledger(local_path: str):
    """Select the local default or explicitly configured D1 backend; never auto-fallback."""
    backend = os.environ.get("RAAWAA_PAPER_LEDGER_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        return PaperTradingLedger(local_path)
    if backend != "d1":
        raise ValueError("RAAWAA_PAPER_LEDGER_BACKEND must be 'sqlite' or 'd1'")
    endpoint = os.environ.get("RAAWAA_PAPER_LEDGER_URL", "")
    token = os.environ.get("RAAWAA_PAPER_LEDGER_TOKEN", "")
    return D1PaperTradingLedger(endpoint, token)
