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


DEFAULT_BASE_URL = "https://data-api.binance.vision"
_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")
_MAX_RESPONSE_BYTES = 1_000_000
_DEPTH_LIMITS = {5, 10, 20, 50, 100}


class BinanceMarketDataError(RuntimeError):
    """A safe, public Binance market-data request failed."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


@dataclass(frozen=True)
class BinanceOrderBookSnapshot:
    source: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    last_update_id: int
    fetched_at: datetime
    exchange_timestamp_ms: int | None = None


@dataclass(frozen=True)
class BinanceBookTicker:
    source: str
    symbol: str
    bid_price: Decimal
    bid_size: Decimal
    ask_price: Decimal
    ask_size: Decimal
    fetched_at: datetime
    exchange_timestamp_ms: int | None = None


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError("symbol must be 2 to 30 uppercase ASCII letters or digits")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise BinanceMarketDataError(f"invalid {field}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BinanceMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BinanceMarketDataError(f"invalid {field}")
    return parsed


class BinancePublicMarketData:
    """Dependency-free public Spot REST adapter; no API key or private endpoint support."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 8.0,
        min_interval_seconds: float = 1.0,
        transport: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "data-api.binance.vision"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official Binance market-data-only HTTPS host")
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
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_request_at = 0.0

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
                    raise BinanceMarketDataError("Binance response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            raise BinanceMarketDataError(f"Binance public API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BinanceMarketDataError(f"Binance public API transport failed: {type(exc).__name__}") from None

    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next_request_at = max(now, self._next_request_at) + self.min_interval_seconds

    def _get(self, path: str, params: dict[str, str]) -> object:
        self._throttle()
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        raw = self._transport(url, self.timeout_seconds)
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise BinanceMarketDataError("invalid or oversized Binance response")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BinanceMarketDataError("Binance returned malformed JSON") from exc
        if isinstance(payload, dict) and "code" in payload and payload.get("code") != 0:
            raise BinanceMarketDataError("Binance public API returned an error payload")
        return payload

    def fetch_orderbook(self, *, symbol: str, limit: int = 100) -> BinanceOrderBookSnapshot:
        symbol = _symbol(symbol)
        if type(limit) is not int or limit not in _DEPTH_LIMITS:
            raise ValueError("limit must be one of 5, 10, 20, 50 or 100")
        payload = self._get("/api/v3/depth", {"symbol": symbol, "limit": str(limit)})
        if not isinstance(payload, dict) or payload.get("lastUpdateId") is None:
            raise BinanceMarketDataError("Binance order book omitted lastUpdateId")
        update_id = payload["lastUpdateId"]
        if type(update_id) is not int or update_id <= 0:
            raise BinanceMarketDataError("invalid lastUpdateId")

        def levels(value: object, field: str) -> tuple[tuple[Decimal, Decimal], ...]:
            if not isinstance(value, list) or not value or len(value) > limit:
                raise BinanceMarketDataError(f"invalid {field} levels")
            parsed = []
            for level in value:
                if not isinstance(level, list) or len(level) != 2:
                    raise BinanceMarketDataError(f"invalid {field} level")
                parsed.append((_decimal(level[0], f"{field} price"), _decimal(level[1], f"{field} quantity")))
            return tuple(parsed)

        bids = levels(payload.get("bids"), "bid")
        asks = levels(payload.get("asks"), "ask")
        if any(bids[i][0] < bids[i + 1][0] for i in range(len(bids) - 1)):
            raise BinanceMarketDataError("bid levels are not sorted descending")
        if any(asks[i][0] > asks[i + 1][0] for i in range(len(asks) - 1)):
            raise BinanceMarketDataError("ask levels are not sorted ascending")
        if bids[0][0] >= asks[0][0]:
            raise BinanceMarketDataError("crossed or locked order book")
        return BinanceOrderBookSnapshot(
            source="binance",
            symbol=symbol,
            bids=bids,
            asks=asks,
            last_update_id=update_id,
            fetched_at=datetime.now(timezone.utc),
        )

    def fetch_book_ticker(self, *, symbol: str) -> BinanceBookTicker:
        symbol = _symbol(symbol)
        payload = self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(payload, dict) or payload.get("symbol") != symbol:
            raise BinanceMarketDataError("Binance book ticker symbol did not match request")
        return BinanceBookTicker(
            source="binance",
            symbol=symbol,
            bid_price=_decimal(payload.get("bidPrice"), "bidPrice"),
            bid_size=_decimal(payload.get("bidQty"), "bidQty"),
            ask_price=_decimal(payload.get("askPrice"), "askPrice"),
            ask_size=_decimal(payload.get("askQty"), "askQty"),
            fetched_at=datetime.now(timezone.utc),
        )
