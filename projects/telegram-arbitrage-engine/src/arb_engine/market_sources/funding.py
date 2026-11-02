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

_MAX_RESPONSE_BYTES = 1_000_000
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,39}$")
_BASES = {
    "binance": ("https://fapi.binance.com", "fapi.binance.com"),
    "bybit": ("https://api.bybit.com", "api.bybit.com"),
}


class FundingMarketDataError(RuntimeError):
    """A public perpetual funding-rate request or response failed safely."""


class FundingRateLimited(FundingMarketDataError):
    def __init__(self, retry_after_seconds: float = 2.0):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"funding source rate limited; deferred for {retry_after_seconds:g}s")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _RateLimiter:
    def __init__(self, *, clock=time.monotonic, sleeper=time.sleep):
        self._clock, self._sleeper = clock, sleeper
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
        if math.isfinite(seconds) and seconds > 0:
            with self._lock:
                self._next_request_at = max(self._next_request_at, self._clock() + min(seconds, 300.0))


_LIMITERS = {venue: _RateLimiter() for venue in _BASES}


@dataclass(frozen=True)
class FundingRateObservation:
    venue_id: str
    market_family: str
    contract_type: str
    symbol: str
    funding_rate: Decimal
    funding_time_ms: int
    mark_price: Decimal | None
    funding_interval_hours: int | None
    rate_type: str | None
    fetched_at: datetime


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value) or value != value.upper():
        raise ValueError("symbol must be an uppercase ASCII contract symbol")
    return value


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise FundingMarketDataError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise FundingMarketDataError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise FundingMarketDataError(f"invalid {field}")
    return parsed


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise FundingMarketDataError(f"invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FundingMarketDataError(f"invalid {field}") from exc
    if result <= 0 or str(result) != str(value):
        raise FundingMarketDataError(f"invalid {field}")
    return result


def _retry_after(headers: object) -> float:
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        value = float(raw) if raw is not None else 2.0
    except (AttributeError, TypeError, ValueError):
        value = 2.0
    if not math.isfinite(value):
        value = 2.0
    return min(max(value, 1.0), 300.0)


class PublicPerpetualFundingClient:
    """Read-only Binance USD-M and Bybit linear/inverse perpetual funding history."""

    def __init__(
        self,
        *,
        venue_id: str,
        base_url: str | None = None,
        timeout_seconds: float = 8.0,
        min_interval_seconds: float = 1.0,
        transport: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        if venue_id not in _BASES:
            raise ValueError("venue_id must be binance or bybit")
        default_url, allowed_host = _BASES[venue_id]
        base_url = base_url or default_url
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https" or parsed.hostname != allowed_host
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        ):
            raise ValueError("base_url must be the official venue HTTPS API host")
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30
        ):
            raise ValueError("timeout_seconds must be between 1 and 30")
        if (
            isinstance(min_interval_seconds, bool) or not isinstance(min_interval_seconds, (int, float))
            or not math.isfinite(min_interval_seconds) or not 0.5 <= min_interval_seconds <= 60
        ):
            raise ValueError("min_interval_seconds must be between 0.5 and 60")
        self.venue_id = venue_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self._transport = transport or self._fetch
        self._rate_limiter = rate_limiter or (
            _RateLimiter(clock=clock, sleeper=sleeper)
            if clock is not time.monotonic or sleeper is not time.sleep
            else _LIMITERS[venue_id]
        )

    @staticmethod
    def _fetch(url: str, timeout: float) -> bytes:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "RaawaaResearchEngine/1.0"},
            method="GET",
        )
        opener = urllib.request.build_opener(_RejectRedirects())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise FundingMarketDataError("funding response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise FundingRateLimited(_retry_after(exc.headers)) from None
            raise FundingMarketDataError(f"funding API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FundingMarketDataError(f"funding transport failed: {type(exc).__name__}") from None

    def _get(self, path: str, params: dict[str, str]) -> object:
        self._rate_limiter.acquire(self.min_interval_seconds)
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            raw = self._transport(url, self.timeout_seconds)
        except FundingRateLimited as exc:
            self._rate_limiter.defer(exc.retry_after_seconds)
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise FundingMarketDataError("invalid or oversized funding response")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FundingMarketDataError("funding API returned malformed JSON") from exc
        if self.venue_id == "binance":
            if not isinstance(payload, list):
                raise FundingMarketDataError("Binance funding history must be a list")
            return payload
        if isinstance(payload, dict) and payload.get("retCode") in (10006, 10429):
            self._rate_limiter.defer(2.0)
            raise FundingRateLimited(2.0)
        if (
            not isinstance(payload, dict) or type(payload.get("retCode")) is not int
            or payload.get("retCode") != 0 or not isinstance(payload.get("result"), dict)
        ):
            raise FundingMarketDataError("Bybit returned an unsuccessful funding response")
        return payload["result"]

    def fetch_funding_history(
        self, *, symbol: str, contract_type: str = "linear", limit: int = 100
    ) -> tuple[FundingRateObservation, ...]:
        symbol = _symbol(symbol)
        if contract_type not in ("linear", "inverse"):
            raise ValueError("contract_type must be linear or inverse")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200")
        if self.venue_id == "binance":
            if contract_type != "linear":
                raise ValueError("Binance adapter currently supports USD-M linear perpetuals only")
            rows = self._get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": str(limit)})
            intervals: dict[str, int] = {}
            try:
                info = self._get("/fapi/v1/fundingInfo", {})
                for item in info:
                    if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                        hours = item.get("fundingIntervalHours")
                        if type(hours) is int and 1 <= hours <= 24:
                            intervals[item["symbol"]] = hours
            except FundingMarketDataError:
                intervals = {}
        else:
            result = self._get("/v5/market/funding/history", {
                "category": contract_type, "symbol": symbol, "limit": str(limit),
            })
            if result.get("category") != contract_type or not isinstance(result.get("list"), list):
                raise FundingMarketDataError("Bybit funding category/list did not match request")
            rows, intervals = result["list"], {}
        if not isinstance(rows, list) or len(rows) > limit:
            raise FundingMarketDataError("funding response exceeded requested row count")
        fetched_at = datetime.now(timezone.utc)
        observations: list[FundingRateObservation] = []
        seen: set[int] = set()
        for item in rows:
            if not isinstance(item, dict) or item.get("symbol") != symbol:
                raise FundingMarketDataError("funding row symbol did not match request")
            if self.venue_id == "binance":
                rate_raw, time_raw = item.get("fundingRate"), item.get("fundingTime")
                mark_raw, rate_type = item.get("markPrice"), item.get("rateType")
                interval = intervals.get(symbol)
            else:
                rate_raw, time_raw = item.get("fundingRate"), item.get("fundingRateTimestamp")
                mark_raw, rate_type, interval = None, None, None
            rate = _decimal(rate_raw, "fundingRate")
            funding_time = _timestamp(time_raw, "fundingTime")
            if funding_time in seen:
                raise FundingMarketDataError("duplicate funding settlement timestamp")
            seen.add(funding_time)
            mark = _decimal(mark_raw, "markPrice", positive=True) if mark_raw not in (None, "") else None
            if rate_type is not None and rate_type not in ("Regular", "Special"):
                raise FundingMarketDataError("invalid Binance funding rate type")
            observations.append(FundingRateObservation(
                venue_id=self.venue_id, market_family="perpetual",
                contract_type=contract_type, symbol=symbol, funding_rate=rate,
                funding_time_ms=funding_time, mark_price=mark,
                funding_interval_hours=interval, rate_type=rate_type, fetched_at=fetched_at,
            ))
        observations.sort(key=lambda item: item.funding_time_ms)
        return tuple(observations)
