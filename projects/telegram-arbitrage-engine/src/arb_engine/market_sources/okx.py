from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from .instruments import InstrumentMetadata


DEFAULT_BASE_URL = "https://www.okx.com"
_ALLOWED_HOSTS = {"www.okx.com", "us.okx.com", "eea.okx.com"}
_INSTRUMENT_ID = re.compile(r"^[A-Z0-9]{2,20}-[A-Z0-9]{2,20}$")
_ASSET_CODE = re.compile(r"^[A-Z0-9]{1,20}$")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_DEPTH = 400


class OKXMarketDataError(RuntimeError):
    """A safe, public OKX market-data request failed."""


class OKXRateLimited(OKXMarketDataError):
    """OKX public REST rate limit was reached; the shared limiter was deferred."""

    def __init__(self, retry_after_seconds: float = 2.0):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"OKX public API rate limited; deferred for {retry_after_seconds:g}s")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _RateLimiter:
    """Thread-safe process-wide pacing for OKX public IP-limited endpoints."""

    def __init__(self, *, clock=time.monotonic, sleeper=time.sleep):
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def acquire(self, interval_seconds: float) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next_request_at = max(self._next_request_at, now + interval_seconds)

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._next_request_at = max(self._next_request_at, self._clock() + min(seconds, 300.0))


_SHARED_RATE_LIMITER = _RateLimiter()


@dataclass(frozen=True)
class OKXOrderBookSnapshot:
    source: str
    category: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    sequence: int
    fetched_at: datetime
    exchange_timestamp_ms: int
    bid_order_counts: tuple[int, ...]
    ask_order_counts: tuple[int, ...]
    bid_deprecated_field_values: tuple[int, ...]
    ask_deprecated_field_values: tuple[int, ...]


def _instrument_id(value: str) -> str:
    if not isinstance(value, str) or not _INSTRUMENT_ID.fullmatch(value):
        raise ValueError("inst_id must be an uppercase OKX spot instrument ID such as BTC-USDT")
    return value


def _decimal(value: object, field: str, *, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise OKXMarketDataError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OKXMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        raise OKXMarketDataError(f"invalid {field}")
    return parsed


def _integer(value: object, field: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OKXMarketDataError(f"invalid {field}")
    text = str(value)
    if not text.isdigit():
        raise OKXMarketDataError(f"invalid {field}")
    parsed = int(text)
    if parsed < 0 or (not allow_zero and parsed == 0):
        raise OKXMarketDataError(f"invalid {field}")
    return parsed


def _retry_after(headers) -> float:
    raw = headers.get("Retry-After") if headers else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 2.0
    if not math.isfinite(value):
        value = 2.0
    return min(max(value, 2.0), 300.0)


class OKXPublicSpotMarketData:
    """Unauthenticated OKX Spot instrument metadata and aggregated L2 REST snapshots."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 8.0,
        min_interval_seconds: float = 1.0,
        transport: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        ):
            raise ValueError("base_url must be an official OKX regional HTTPS API host")
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between 1 and 30")
        if (
            isinstance(min_interval_seconds, bool) or not isinstance(min_interval_seconds, (int, float))
            or not math.isfinite(min_interval_seconds) or not 1 <= min_interval_seconds <= 60
        ):
            raise ValueError("min_interval_seconds must be between 1 and 60")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self._transport = transport or self._fetch
        self._rate_limiter = rate_limiter or (
            _RateLimiter(clock=clock, sleeper=sleeper)
            if clock is not time.monotonic or sleeper is not time.sleep
            else _SHARED_RATE_LIMITER
        )

    @staticmethod
    def _fetch(url: str, timeout: float) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "RaawaaResearchEngine/1.0"},
            method="GET",
        )
        opener = urllib.request.build_opener(_RejectRedirects())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise OKXMarketDataError("OKX response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise OKXRateLimited(_retry_after(exc.headers)) from None
            raise OKXMarketDataError(f"OKX public API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OKXMarketDataError(f"OKX public API transport failed: {type(exc).__name__}") from None

    def _get(self, path: str, params: dict[str, str]) -> list | dict:
        self._rate_limiter.acquire(self.min_interval_seconds)
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            raw = self._transport(url, self.timeout_seconds)
        except OKXRateLimited as exc:
            self._rate_limiter.defer(exc.retry_after_seconds)
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise OKXMarketDataError("invalid or oversized OKX response")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OKXMarketDataError("OKX returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise OKXMarketDataError("OKX response must be an object")
        code = str(payload.get("code", ""))
        if code == "50011":
            self._rate_limiter.defer(2.0)
            raise OKXRateLimited(2.0)
        if code != "0":
            raise OKXMarketDataError(f"OKX public API returned code {code or 'missing'}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise OKXMarketDataError("OKX response data must be a list")
        return data

    def fetch_instrument(self, *, inst_id: str) -> InstrumentMetadata:
        inst_id = _instrument_id(inst_id)
        data = self._get("/api/v5/public/instruments", {
            "instType": "SPOT", "instId": inst_id,
        })
        if len(data) != 1 or not isinstance(data[0], dict):
            raise OKXMarketDataError("OKX did not return exactly one instrument")
        item = data[0]
        if item.get("instType") != "SPOT" or item.get("instId") != inst_id:
            raise OKXMarketDataError("OKX instrument identity did not match request")
        base = item.get("baseCcy")
        quote = item.get("quoteCcy")
        if (
            not isinstance(base, str) or not _ASSET_CODE.fullmatch(base)
            or not isinstance(quote, str) or not _ASSET_CODE.fullmatch(quote)
        ):
            raise OKXMarketDataError("OKX returned invalid base/quote currency codes")
        state = item.get("state")
        if not isinstance(state, str) or not state:
            raise OKXMarketDataError("OKX returned invalid instrument state")
        lot_size = _decimal(item.get("lotSz"), "lotSz")
        tick_size = _decimal(item.get("tickSz"), "tickSz")
        min_size_raw = item.get("minSz")
        min_size = _decimal(min_size_raw, "minSz") if min_size_raw not in (None, "") else None
        return InstrumentMetadata(
            venue_id="okx",
            instrument_id=inst_id,
            market_family="cex_spot",
            base_asset_code=base,
            quote_asset_code=quote,
            base_step_size=lot_size,
            price_tick_size=tick_size,
            min_base_quantity=min_size,
            min_quote_notional=None,
            status="TRADING" if state == "live" else state.upper(),
            fetched_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _parse_levels(rows: object, field: str, *, descending: bool):
        if not isinstance(rows, list) or not rows or len(rows) > _MAX_DEPTH:
            raise OKXMarketDataError(f"invalid {field} depth")
        levels = []
        order_counts = []
        deprecated_values = []
        previous = None
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                raise OKXMarketDataError(f"invalid {field} level")
            price = _decimal(row[0], f"{field} price")
            size = _decimal(row[1], f"{field} size")
            deprecated_value = _integer(row[2], f"{field} deprecated field value")
            order_count = _integer(row[3], f"{field} order count", allow_zero=False)
            if previous is not None and (price >= previous if descending else price <= previous):
                raise OKXMarketDataError(f"{field} levels are not strictly sorted")
            previous = price
            levels.append((price, size))
            order_counts.append(order_count)
            deprecated_values.append(deprecated_value)
        return tuple(levels), tuple(order_counts), tuple(deprecated_values)

    def fetch_orderbook(self, *, inst_id: str, depth: int = 20) -> OKXOrderBookSnapshot:
        inst_id = _instrument_id(inst_id)
        if type(depth) is not int or not 1 <= depth <= _MAX_DEPTH:
            raise ValueError(f"depth must be an integer from 1 to {_MAX_DEPTH}")
        data = self._get("/api/v5/market/books", {"instId": inst_id, "sz": str(depth)})
        if len(data) != 1 or not isinstance(data[0], dict):
            raise OKXMarketDataError("OKX did not return exactly one order-book snapshot")
        item = data[0]
        returned_inst_id = item.get("instId")
        if returned_inst_id is not None and returned_inst_id != inst_id:
            raise OKXMarketDataError("OKX order-book instrument did not match request")
        bids, bid_counts, bid_deprecated_values = self._parse_levels(
            item.get("bids"), "bids", descending=True,
        )
        asks, ask_counts, ask_deprecated_values = self._parse_levels(
            item.get("asks"), "asks", descending=False,
        )
        if bids[0][0] >= asks[0][0]:
            raise OKXMarketDataError("OKX returned a crossed order book")
        timestamp = _integer(item.get("ts"), "ts", allow_zero=False)
        sequence = _integer(item.get("seqId"), "seqId")
        return OKXOrderBookSnapshot(
            source="okx",
            category="spot",
            symbol=inst_id,
            bids=bids,
            asks=asks,
            sequence=sequence,
            fetched_at=datetime.now(timezone.utc),
            exchange_timestamp_ms=timestamp,
            bid_order_counts=bid_counts,
            ask_order_counts=ask_counts,
            bid_deprecated_field_values=bid_deprecated_values,
            ask_deprecated_field_values=ask_deprecated_values,
        )
