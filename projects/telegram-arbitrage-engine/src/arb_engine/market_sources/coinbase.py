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


DEFAULT_BASE_URL = "https://api.exchange.coinbase.com"
_PRODUCT_ID = re.compile(r"^[A-Z0-9]{2,20}-[A-Z0-9]{2,20}$")
_MAX_RESPONSE_BYTES = 1_000_000


class CoinbaseMarketDataError(RuntimeError):
    """A safe, public Coinbase Exchange market-data request failed."""


class CoinbaseRateLimited(CoinbaseMarketDataError):
    """Coinbase public REST rate limit was reached; the shared limiter was deferred."""

    def __init__(self, retry_after_seconds: float = 2.0):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Coinbase public API rate limited; deferred for {retry_after_seconds:g}s"
        )


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _RateLimiter:
    """Thread-safe per-process pacing for Coinbase public REST requests."""

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
class CoinbaseOrderBookSnapshot:
    source: str
    category: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    sequence: int
    fetched_at: datetime
    exchange_timestamp_ms: int | None
    bid_order_counts: tuple[int, ...]
    ask_order_counts: tuple[int, ...]


def _product_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("product_id must be an uppercase Coinbase product ID such as BTC-USD")
    normalized = value.upper()
    if not _PRODUCT_ID.fullmatch(normalized):
        raise ValueError("product_id must use the BASE-QUOTE format")
    return normalized


def _decimal(value: object, field: str, *, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int)):
        raise CoinbaseMarketDataError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise CoinbaseMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise CoinbaseMarketDataError(f"invalid {field}")
    return parsed


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


def _iso_timestamp_ms(value: object) -> int | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise CoinbaseMarketDataError("invalid Coinbase order-book time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoinbaseMarketDataError("invalid Coinbase order-book time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CoinbaseMarketDataError("Coinbase order-book time must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    delta = utc - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


class CoinbaseExchangePublicMarketData:
    """Dependency-free Coinbase Exchange public REST adapter; no API key or private methods."""

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
            or parsed.hostname != "api.exchange.coinbase.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official Coinbase Exchange HTTPS API host")
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
            headers={
                "Accept": "application/json",
                "User-Agent": "RaawaaResearchEngine/1.0",
            },
            method="GET",
        )
        opener = urllib.request.build_opener(_RejectRedirects())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise CoinbaseMarketDataError("Coinbase response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise CoinbaseRateLimited(_retry_after_seconds(exc.headers)) from None
            raise CoinbaseMarketDataError(
                f"Coinbase public API returned HTTP {exc.code}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CoinbaseMarketDataError(
                f"Coinbase public API transport failed: {type(exc).__name__}"
            ) from None

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        self._rate_limiter.acquire(self.min_interval_seconds)
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        try:
            raw = self._transport(url, self.timeout_seconds)
        except CoinbaseRateLimited as exc:
            self._rate_limiter.defer(exc.retry_after_seconds)
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise CoinbaseMarketDataError("invalid or oversized Coinbase response")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoinbaseMarketDataError("Coinbase returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise CoinbaseMarketDataError("Coinbase returned an invalid response object")
        if isinstance(payload.get("message"), str):
            message = payload["message"][:200]
            if "rate limit" in message.lower() or "too many requests" in message.lower():
                self._rate_limiter.defer(2.0)
                raise CoinbaseRateLimited(2.0)
            raise CoinbaseMarketDataError(f"Coinbase public API error: {message}")
        return payload

    def fetch_instrument(self, *, product_id: str) -> InstrumentMetadata:
        product_id = _product_id(product_id)
        item = self._get(f"/products/{urllib.parse.quote(product_id, safe='-')}")
        if item.get("id") != product_id:
            raise CoinbaseMarketDataError("Coinbase product ID did not match the requested product")
        base_code = item.get("base_currency")
        quote_code = item.get("quote_currency")
        if not all(isinstance(code, str) and code for code in (base_code, quote_code)):
            raise CoinbaseMarketDataError("Coinbase product omitted base or quote currency")
        base_step = _decimal(item.get("base_increment"), "base_increment")
        price_tick = _decimal(item.get("quote_increment"), "quote_increment")
        min_base = item.get("base_min_size")
        min_quote = item.get("min_market_funds")
        raw_status = item.get("status")
        flags = (
            "trading_disabled",
            "cancel_only",
            "post_only",
            "limit_only",
            "auction_mode",
        )
        for flag in flags:
            if flag in item and type(item[flag]) is not bool:
                raise CoinbaseMarketDataError(f"invalid Coinbase product flag: {flag}")
        restricted = any(item.get(flag) is True for flag in flags)
        status = (
            "AUCTION" if item.get("auction_mode") is True
            else "RESTRICTED" if restricted
            else "TRADING" if raw_status == "online"
            else str(raw_status or "UNKNOWN").upper()
        )
        return InstrumentMetadata(
            venue_id="coinbase",
            instrument_id=product_id,
            market_family="cex_spot",
            base_asset_code=base_code,
            quote_asset_code=quote_code,
            base_step_size=base_step,
            price_tick_size=price_tick,
            status=status,
            fetched_at=datetime.now(timezone.utc),
            min_base_quantity=(
                _decimal(min_base, "base_min_size") if min_base not in (None, "") else None
            ),
            min_quote_notional=(
                _decimal(min_quote, "min_market_funds")
                if min_quote not in (None, "")
                else None
            ),
        )

    def fetch_orderbook(
        self, *, product_id: str, level: int = 2
    ) -> CoinbaseOrderBookSnapshot:
        product_id = _product_id(product_id)
        if type(level) is not int or level not in (1, 2):
            raise ValueError("level must be 1 or 2; level 3 has a different order-ID schema")
        item = self._get(
            f"/products/{urllib.parse.quote(product_id, safe='-')}/book",
            {"level": str(level)},
        )
        sequence = item.get("sequence")
        if type(sequence) is not int or sequence <= 0:
            raise CoinbaseMarketDataError("invalid Coinbase order-book sequence")
        bids, bid_counts = self._levels(item.get("bids"), "bids")
        asks, ask_counts = self._levels(item.get("asks"), "asks")
        if any(bids[index][0] < bids[index + 1][0] for index in range(len(bids) - 1)):
            raise CoinbaseMarketDataError("Coinbase bids were not sorted highest-first")
        if any(asks[index][0] > asks[index + 1][0] for index in range(len(asks) - 1)):
            raise CoinbaseMarketDataError("Coinbase asks were not sorted lowest-first")
        return CoinbaseOrderBookSnapshot(
            source="coinbase",
            category="spot",
            symbol=product_id,
            bids=bids,
            asks=asks,
            sequence=sequence,
            fetched_at=datetime.now(timezone.utc),
            exchange_timestamp_ms=_iso_timestamp_ms(item.get("time")),
            bid_order_counts=bid_counts,
            ask_order_counts=ask_counts,
        )

    @staticmethod
    def _levels(
        value: object, field: str
    ) -> tuple[tuple[tuple[Decimal, Decimal], ...], tuple[int, ...]]:
        if not isinstance(value, list):
            raise CoinbaseMarketDataError(f"Coinbase omitted {field}")
        levels: list[tuple[Decimal, Decimal]] = []
        order_counts: list[int] = []
        for item in value:
            if not isinstance(item, list) or len(item) != 3:
                raise CoinbaseMarketDataError(f"invalid Coinbase {field} level")
            price = _decimal(item[0], f"{field} price")
            quantity = _decimal(item[1], f"{field} quantity")
            count = item[2]
            if type(count) is not int or count <= 0:
                raise CoinbaseMarketDataError(f"invalid Coinbase {field} order count")
            levels.append((price, quantity))
            order_counts.append(count)
        return tuple(levels), tuple(order_counts)
