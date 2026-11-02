from __future__ import annotations

import json
import math
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


DEFAULT_BASE_URL = "https://api.bybit.com"
_ALLOWED_HOSTS = {"api.bybit.com", "api-testnet.bybit.com"}
_CATEGORIES = {"spot", "linear", "inverse", "option"}
_MAX_RESPONSE_BYTES = 1_000_000


class BybitMarketDataError(RuntimeError):
    """A safe, read-only Bybit public market-data request failed."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


@dataclass(frozen=True)
class OrderBookSnapshot:
    source: str
    category: str
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    exchange_timestamp_ms: int
    matching_engine_timestamp_ms: int | None
    fetched_at: datetime
    update_id: int
    cross_sequence: int


@dataclass(frozen=True)
class TickerSnapshot:
    source: str
    category: str
    symbol: str
    last_price: Decimal | None
    bid_price: Decimal | None
    bid_size: Decimal | None
    ask_price: Decimal | None
    ask_size: Decimal | None
    volume_24h: Decimal | None
    turnover_24h: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    open_interest: Decimal | None
    funding_rate: Decimal | None
    basis: Decimal | None
    exchange_timestamp_ms: int
    fetched_at: datetime


def _decimal(value: object, field: str, *, positive: bool = True) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise BybitMarketDataError(f"invalid {field}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BybitMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise BybitMarketDataError(f"invalid {field}")
    return parsed


def _optional_decimal(
    value: object, field: str, *, positive: bool = False, nonnegative: bool = False,
) -> Decimal | None:
    if value is None or value == "":
        return None
    parsed = _decimal(value, field, positive=positive)
    if nonnegative and parsed < 0:
        raise BybitMarketDataError(f"invalid {field}")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise BybitMarketDataError(f"invalid {field}")
    return value


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not 2 <= len(value) <= 40:
        raise ValueError("symbol must contain 2 to 40 characters")
    if not all(char.isascii() and (char.isalnum() or char in "-_") for char in value):
        raise ValueError("symbol must use ASCII letters, digits, hyphen or underscore")
    if value != value.upper():
        raise ValueError("symbol must be uppercase")
    return value


class BybitPublicMarketData:
    """Small public REST adapter; no API key, account, order or write endpoint support."""

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
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be an allowlisted HTTPS Bybit API host")
        if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        if not isinstance(min_interval_seconds, (int, float)) or not math.isfinite(min_interval_seconds) or not 0 <= min_interval_seconds <= 60:
            raise ValueError("min_interval_seconds must be between 0 and 60")
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
                    raise BybitMarketDataError("Bybit response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            raise BybitMarketDataError(f"Bybit public API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BybitMarketDataError(f"Bybit public API transport failed: {type(exc).__name__}") from None

    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next_request_at = max(now, self._next_request_at) + self.min_interval_seconds

    def _get(self, path: str, params: dict[str, str]) -> dict:
        self._throttle()
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        raw = self._transport(url, self.timeout_seconds)
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise BybitMarketDataError("invalid or oversized Bybit response")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BybitMarketDataError("Bybit returned malformed JSON") from exc
        if (
            not isinstance(payload, dict)
            or type(payload.get("retCode")) is not int
            or payload.get("retCode") != 0
        ):
            raise BybitMarketDataError("Bybit returned an unsuccessful market-data response")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BybitMarketDataError("Bybit response omitted result object")
        return payload

    @staticmethod
    def _validate_category(category: str) -> str:
        if category not in _CATEGORIES:
            raise ValueError("category must be spot, linear, inverse or option")
        return category

    def fetch_instrument(self, *, symbol: str, category: str = "spot") -> InstrumentMetadata:
        """Fetch one public spot instrument specification; never infers asset identity."""
        category = self._validate_category(category)
        if category != "spot":
            raise ValueError("fetch_instrument currently supports spot instruments only")
        symbol = _symbol(symbol)
        payload = self._get("/v5/market/instruments-info", {
            "category": category,
            "symbol": symbol,
        })
        result = payload["result"]
        if result.get("category") != category:
            raise BybitMarketDataError("Bybit instrument category did not match request")
        items = result.get("list")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise BybitMarketDataError("Bybit did not return exactly one instrument")
        item = items[0]
        if item.get("symbol") != symbol:
            raise BybitMarketDataError("Bybit instrument symbol did not match request")
        base_code = item.get("baseCoin")
        quote_code = item.get("quoteCoin")
        status = item.get("status")
        lot_filter = item.get("lotSizeFilter")
        price_filter = item.get("priceFilter")
        if not all(isinstance(value, str) and value for value in (base_code, quote_code, status)):
            raise BybitMarketDataError("Bybit instrument omitted asset codes or status")
        if not isinstance(lot_filter, dict) or not isinstance(price_filter, dict):
            raise BybitMarketDataError("Bybit instrument omitted precision filters")
        min_quote = lot_filter.get("minOrderAmt")
        return InstrumentMetadata(
            venue_id="bybit",
            instrument_id=symbol,
            market_family="cex_spot",
            base_asset_code=base_code,
            quote_asset_code=quote_code,
            base_step_size=lot_filter.get("basePrecision"),
            price_tick_size=price_filter.get("tickSize"),
            status=status,
            fetched_at=datetime.now(timezone.utc),
            min_quote_notional=min_quote if min_quote not in (None, "") else None,
        )

    def fetch_orderbook(
        self,
        *,
        category: str,
        symbol: str,
        limit: int = 50,
    ) -> OrderBookSnapshot:
        category = self._validate_category(category)
        symbol = _symbol(symbol)
        maximum = 25 if category == "option" else 1000
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError(f"limit must be between 1 and {maximum} for {category}")
        payload = self._get("/v5/market/orderbook", {
            "category": category, "symbol": symbol, "limit": str(limit),
        })
        result = payload["result"]
        if result.get("s") != symbol:
            raise BybitMarketDataError("Bybit order book symbol did not match request")

        def levels(value: object, field: str) -> tuple[tuple[Decimal, Decimal], ...]:
            if not isinstance(value, list) or not value or len(value) > limit:
                raise BybitMarketDataError(f"invalid {field} levels")
            parsed = []
            for level in value:
                if not isinstance(level, list) or len(level) != 2:
                    raise BybitMarketDataError(f"invalid {field} level")
                parsed.append((
                    _decimal(level[0], f"{field} price"),
                    _decimal(level[1], f"{field} size"),
                ))
            return tuple(parsed)

        bids = levels(result.get("b"), "bid")
        asks = levels(result.get("a"), "ask")
        if any(bids[i][0] < bids[i + 1][0] for i in range(len(bids) - 1)):
            raise BybitMarketDataError("bid levels are not sorted descending")
        if any(asks[i][0] > asks[i + 1][0] for i in range(len(asks) - 1)):
            raise BybitMarketDataError("ask levels are not sorted ascending")
        if bids[0][0] >= asks[0][0]:
            raise BybitMarketDataError("crossed or locked order book")
        cts = result.get("cts")
        if cts is not None:
            cts = _positive_int(cts, "matching engine timestamp")
        return OrderBookSnapshot(
            source="bybit",
            category=category,
            symbol=symbol,
            bids=bids,
            asks=asks,
            exchange_timestamp_ms=_positive_int(result.get("ts"), "exchange timestamp"),
            matching_engine_timestamp_ms=cts,
            fetched_at=datetime.now(timezone.utc),
            update_id=_positive_int(result.get("u"), "order book update ID"),
            cross_sequence=_positive_int(result.get("seq"), "order book sequence"),
        )

    def fetch_tickers(self, *, category: str, symbol: str | None = None) -> tuple[TickerSnapshot, ...]:
        category = self._validate_category(category)
        params = {"category": category}
        if symbol is not None:
            params["symbol"] = _symbol(symbol)
        payload = self._get("/v5/market/tickers", params)
        result = payload["result"]
        if result.get("category") not in (None, category):
            raise BybitMarketDataError("Bybit ticker category did not match request")
        rows = result.get("list")
        if not isinstance(rows, list):
            raise BybitMarketDataError("Bybit ticker response omitted list")
        if symbol is not None and len(rows) != 1:
            raise BybitMarketDataError("Bybit ticker response did not return exactly one requested symbol")
        snapshots = []
        for row in rows:
            if not isinstance(row, dict):
                raise BybitMarketDataError("invalid ticker row")
            row_symbol = row.get("symbol")
            try:
                row_symbol = _symbol(row_symbol)
            except ValueError as exc:
                raise BybitMarketDataError("invalid ticker symbol") from exc
            if symbol is not None and row_symbol != symbol:
                raise BybitMarketDataError("Bybit ticker symbol did not match request")
            snapshots.append(TickerSnapshot(
                source="bybit",
                category=category,
                symbol=row_symbol,
                last_price=_optional_decimal(row.get("lastPrice"), "last price", positive=True),
                bid_price=_optional_decimal(row.get("bid1Price"), "best bid", positive=True),
                bid_size=_optional_decimal(row.get("bid1Size"), "best bid size", positive=True),
                ask_price=_optional_decimal(row.get("ask1Price"), "best ask", positive=True),
                ask_size=_optional_decimal(row.get("ask1Size"), "best ask size", positive=True),
                volume_24h=_optional_decimal(row.get("volume24h"), "24h volume", nonnegative=True),
                turnover_24h=_optional_decimal(row.get("turnover24h"), "24h turnover", nonnegative=True),
                mark_price=_optional_decimal(row.get("markPrice"), "mark price", positive=True),
                index_price=_optional_decimal(row.get("indexPrice"), "index price", positive=True),
                open_interest=_optional_decimal(row.get("openInterest"), "open interest", nonnegative=True),
                funding_rate=_optional_decimal(row.get("fundingRate"), "funding rate"),
                basis=_optional_decimal(row.get("basis"), "basis"),
                exchange_timestamp_ms=_positive_int(payload.get("time"), "exchange response timestamp"),
                fetched_at=datetime.now(timezone.utc),
            ))
        return tuple(snapshots)
