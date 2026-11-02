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


DEFAULT_BASE_URL = "https://api.kraken.com"
_PAIR = re.compile(r"^[A-Z0-9/]{2,32}$")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_PRECISION = 18


class KrakenMarketDataError(RuntimeError):
    """A safe, public Kraken Spot market-data request failed."""


class KrakenRateLimited(KrakenMarketDataError):
    """Kraken public REST rate limit was reached; the shared limiter was deferred."""

    def __init__(self, retry_after_seconds: float = 2.0):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Kraken public API rate limited; deferred for {retry_after_seconds:g}s")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _RateLimiter:
    """Thread-safe per-process pacing for Kraken's public, IP-limited REST endpoints."""

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
            self._next_request_at = max(now, self._next_request_at) + interval_seconds

    def defer(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            return
        with self._lock:
            self._next_request_at = max(
                self._next_request_at,
                self._clock() + min(seconds, 300.0),
            )


_SHARED_RATE_LIMITER = _RateLimiter()


@dataclass(frozen=True)
class KrakenOrderBookSnapshot:
    source: str
    category: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    fetched_at: datetime
    exchange_timestamp_ms: int | None
    bid_level_timestamps_ms: tuple[int, ...]
    ask_level_timestamps_ms: tuple[int, ...]


def _pair(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("pair must be an uppercase Kraken REST pair identifier")
    normalized = value.upper()
    if not _PAIR.fullmatch(normalized):
        raise ValueError("pair must contain 2 to 32 uppercase ASCII letters, digits or slashes")
    return normalized


def _decimal(value: object, field: str, *, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int)):
        raise KrakenMarketDataError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise KrakenMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise KrakenMarketDataError(f"invalid {field}")
    return parsed


def _precision(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_PRECISION:
        raise KrakenMarketDataError(f"invalid {field}")
    return value


def _retry_after_seconds(headers: object) -> float:
    value = None
    if headers is not None:
        try:
            value = headers.get("Retry-After")
        except AttributeError:
            value = None
    try:
        parsed = float(value) if value is not None else 2.0
    except (TypeError, ValueError):
        parsed = 2.0
    if not math.isfinite(parsed):
        parsed = 2.0
    return min(max(parsed, 1.0), 300.0)


def _timestamp_ms(value: object) -> int:
    timestamp = _decimal(value, "order-book level timestamp")
    milliseconds = timestamp * 1000
    if milliseconds != milliseconds.to_integral_value():
        milliseconds = milliseconds.to_integral_value()
    parsed = int(milliseconds)
    if parsed <= 0:
        raise KrakenMarketDataError("invalid order-book level timestamp")
    return parsed


class KrakenPublicMarketData:
    """Dependency-free Kraken Spot public REST adapter; no private/authenticated methods."""

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
            parsed.scheme != "https"
            or parsed.hostname != "api.kraken.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official Kraken public HTTPS API host")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between 1 and 30")
        if (
            not isinstance(min_interval_seconds, (int, float))
            or isinstance(min_interval_seconds, bool)
            or not math.isfinite(min_interval_seconds)
            or not 1 <= min_interval_seconds <= 60
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
                    raise KrakenMarketDataError("Kraken response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise KrakenRateLimited(_retry_after_seconds(exc.headers)) from None
            raise KrakenMarketDataError(f"Kraken public API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KrakenMarketDataError(
                f"Kraken public API transport failed: {type(exc).__name__}"
            ) from None

    def _get(self, path: str, params: dict[str, str]) -> dict:
        self._rate_limiter.acquire(self.min_interval_seconds)
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            raw = self._transport(url, self.timeout_seconds)
        except KrakenRateLimited as exc:
            self._rate_limiter.defer(exc.retry_after_seconds)
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise KrakenMarketDataError("invalid or oversized Kraken response")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KrakenMarketDataError("Kraken returned malformed JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), list):
            raise KrakenMarketDataError("Kraken returned an invalid response envelope")
        errors = payload["error"]
        if errors:
            message = "; ".join(str(item)[:160] for item in errors)
            if "rate limit" in message.lower() or "too many requests" in message.lower():
                self._rate_limiter.defer(2.0)
                raise KrakenRateLimited(2.0)
            raise KrakenMarketDataError(f"Kraken public API error: {message}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise KrakenMarketDataError("Kraken response omitted its result object")
        return result

    def fetch_instrument(self, *, pair: str) -> InstrumentMetadata:
        """Resolve an alias to Kraken's canonical REST pair ID and verified lot/tick rules."""
        pair = _pair(pair)
        result = self._get("/0/public/AssetPairs", {"pair": pair})
        if len(result) != 1:
            raise KrakenMarketDataError("Kraken did not return exactly one instrument")
        instrument_id, item = next(iter(result.items()))
        if not isinstance(instrument_id, str) or not isinstance(item, dict):
            raise KrakenMarketDataError("Kraken returned malformed instrument metadata")
        aliases = {
            instrument_id.upper(),
            str(item.get("altname", "")).upper(),
            str(item.get("wsname", "")).upper(),
        }
        if pair not in aliases:
            raise KrakenMarketDataError("Kraken instrument did not match the requested pair")
        base_code = item.get("base")
        quote_code = item.get("quote")
        if not all(isinstance(code, str) and code for code in (base_code, quote_code)):
            raise KrakenMarketDataError("Kraken instrument omitted internal base/quote asset codes")
        lot_decimals = _precision(item.get("lot_decimals"), "lot_decimals")
        pair_decimals = _precision(item.get("pair_decimals"), "pair_decimals")
        lot_multiplier = _decimal(item.get("lot_multiplier", "1"), "lot_multiplier")
        base_step = lot_multiplier * Decimal(1).scaleb(-lot_decimals)
        tick_raw = item.get("tick_size")
        price_tick = (
            _decimal(tick_raw, "tick_size")
            if tick_raw not in (None, "")
            else Decimal(1).scaleb(-pair_decimals)
        )
        order_min = item.get("ordermin")
        cost_min = item.get("costmin")
        raw_status = item.get("status")
        if raw_status is None:
            # AssetPairs is Kraken's public list of tradable markets; the API does not
            # include a status field on every response version.
            status = "TRADING"
        elif str(raw_status).lower() == "online":
            status = "TRADING"
        else:
            status = str(raw_status).upper()
        return InstrumentMetadata(
            venue_id="kraken",
            instrument_id=instrument_id,
            market_family="cex_spot",
            base_asset_code=base_code,
            quote_asset_code=quote_code,
            base_step_size=base_step,
            price_tick_size=price_tick,
            status=status,
            fetched_at=datetime.now(timezone.utc),
            min_base_quantity=(
                _decimal(order_min, "ordermin") if order_min not in (None, "") else None
            ),
            min_quote_notional=(
                _decimal(cost_min, "costmin") if cost_min not in (None, "") else None
            ),
        )

    def fetch_orderbook(self, *, pair: str, count: int = 100) -> KrakenOrderBookSnapshot:
        """Fetch a depth snapshot using Kraken's canonical REST pair ID from fetch_instrument."""
        pair = _pair(pair)
        if type(count) is not int or not 1 <= count <= 500:
            raise ValueError("count must be an integer between 1 and 500")
        result = self._get("/0/public/Depth", {"pair": pair, "count": str(count)})
        if len(result) != 1:
            raise KrakenMarketDataError("Kraken did not return exactly one order book")
        symbol, item = next(iter(result.items()))
        if symbol.upper() != pair:
            raise KrakenMarketDataError(
                "Kraken order-book key did not match; pass the canonical instrument_id from fetch_instrument"
            )
        if not isinstance(item, dict):
            raise KrakenMarketDataError("Kraken returned malformed order-book data")
        bids, bid_times = self._levels(item.get("bids"), "bids")
        asks, ask_times = self._levels(item.get("asks"), "asks")
        if any(bids[index][0] < bids[index + 1][0] for index in range(len(bids) - 1)):
            raise KrakenMarketDataError("Kraken bids were not sorted highest-first")
        if any(asks[index][0] > asks[index + 1][0] for index in range(len(asks) - 1)):
            raise KrakenMarketDataError("Kraken asks were not sorted lowest-first")
        timestamps = bid_times + ask_times
        return KrakenOrderBookSnapshot(
            source="kraken",
            category="spot",
            symbol=symbol,
            bids=bids,
            asks=asks,
            fetched_at=datetime.now(timezone.utc),
            exchange_timestamp_ms=max(timestamps) if timestamps else None,
            bid_level_timestamps_ms=bid_times,
            ask_level_timestamps_ms=ask_times,
        )

    @staticmethod
    def _levels(value: object, field: str) -> tuple[tuple[tuple[Decimal, Decimal], ...], tuple[int, ...]]:
        if not isinstance(value, list):
            raise KrakenMarketDataError(f"Kraken omitted {field}")
        levels: list[tuple[Decimal, Decimal]] = []
        timestamps: list[int] = []
        for item in value:
            if not isinstance(item, list) or len(item) != 3:
                raise KrakenMarketDataError(f"invalid Kraken {field} level")
            price = _decimal(item[0], f"{field} price")
            quantity = _decimal(item[1], f"{field} quantity")
            timestamp = _timestamp_ms(item[2])
            levels.append((price, quantity))
            timestamps.append(timestamp)
        return tuple(levels), tuple(timestamps)
