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


DEFAULT_BASE_URL = "https://api.jup.ag"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_UINT64 = 2**64 - 1
_MAX_PRICE_MINTS = 50
_MAX_PRICE_DECIMALS = 18


class _KeylessRateLimiter:
    """Thread-safe host-wide pacing and Retry-After deferral for Jupiter keyless calls."""

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


_SHARED_KEYLESS_RATE_LIMITER = _KeylessRateLimiter()


class JupiterQuoteError(RuntimeError):
    """A safe, quote-only Jupiter public API request failed."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


@dataclass(frozen=True)
class JupiterQuote:
    source: str
    network: str
    input_mint: str
    output_mint: str
    input_amount_raw: int
    output_amount_raw: int
    other_amount_threshold_raw: int | None
    fee_bps: int | None
    fee_mint: str | None
    price_impact_pct: Decimal | None
    router: str
    route_plan: tuple[dict, ...]
    request_id: str | None
    context_slot: int | None
    fetched_at: datetime
    transaction_present: bool = False


def _mint(value: str, label: str) -> str:
    if not isinstance(value, str) or not _BASE58.fullmatch(value):
        raise ValueError(f"{label} must be a valid 32-byte Solana base58 mint address")
    number = 0
    for character in value:
        number = number * 58 + _BASE58_ALPHABET.index(character)
    leading_zeroes = len(value) - len(value.lstrip("1"))
    decoded = (b"\x00" * leading_zeroes) + (
        number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    )
    if len(decoded) != 32:
        raise ValueError(f"{label} must decode to exactly 32-byte public key data")
    return value


def _raw_amount(value: object, field: str) -> int:
    if not isinstance(value, str) or not value or len(value) > 20 or not value.isascii() or not value.isdigit():
        raise JupiterQuoteError(f"invalid {field}")
    parsed = int(value)
    if not 0 < parsed <= _MAX_UINT64:
        raise JupiterQuoteError(f"invalid {field}")
    return parsed


def _optional_raw_amount(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    return _raw_amount(value, field)


def _optional_price_impact(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise JupiterQuoteError("invalid priceImpactPct")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise JupiterQuoteError("invalid priceImpactPct") from exc
    if not parsed.is_finite() or parsed < 0:
        raise JupiterQuoteError("invalid priceImpactPct")
    return parsed


class JupiterQuoteOnlyClient:
    """Jupiter Swap API V2 quote-only client; never requests or handles a transaction."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 8.0,
        min_interval_seconds: float = 2.0,
        transport: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rate_limiter: _KeylessRateLimiter | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.jup.ag"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official Jupiter HTTPS API host")
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
            or not 2 <= min_interval_seconds <= 60
        ):
            raise ValueError("min_interval_seconds must be between 2 and 60 for keyless 0.5 RPS access")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self._transport = transport or self._fetch
        self._rate_limiter = rate_limiter or (
            _KeylessRateLimiter(clock=clock, sleeper=sleeper)
            if clock is not time.monotonic or sleeper is not time.sleep
            else _SHARED_KEYLESS_RATE_LIMITER
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
                    raise JupiterQuoteError("Jupiter response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            raise JupiterQuoteError(f"Jupiter public API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JupiterQuoteError(f"Jupiter public API transport failed: {type(exc).__name__}") from None

    def _throttle(self) -> None:
        self._rate_limiter.acquire(self.min_interval_seconds)

    def quote(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount_raw: int | str,
    ) -> JupiterQuote:
        input_mint = _mint(input_mint, "input_mint")
        output_mint = _mint(output_mint, "output_mint")
        if input_mint == output_mint:
            raise ValueError("input_mint and output_mint must be different")
        if isinstance(amount_raw, bool) or isinstance(amount_raw, float):
            raise ValueError("amount_raw must be a positive uint64 integer or decimal string")
        if isinstance(amount_raw, str):
            if not amount_raw or len(amount_raw) > 20 or not amount_raw.isascii() or not amount_raw.isdigit():
                raise ValueError("amount_raw must contain decimal digits only")
            amount = int(amount_raw)
        elif isinstance(amount_raw, int):
            amount = amount_raw
        else:
            raise ValueError("amount_raw must be a positive uint64 integer or decimal string")
        if not 0 < amount <= _MAX_UINT64:
            raise ValueError("amount_raw must fit a positive uint64")

        # No taker, API key, wallet, build, execute, or submit parameters are ever sent.
        params = urllib.parse.urlencode({
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
        })
        self._throttle()
        raw = self._transport(f"{self.base_url}/swap/v2/order?{params}", self.timeout_seconds)
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise JupiterQuoteError("invalid or oversized Jupiter response")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JupiterQuoteError("Jupiter returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise JupiterQuoteError("Jupiter returned an invalid quote object")
        if payload.get("error") or payload.get("errorCode"):
            raise JupiterQuoteError("Jupiter quote request was unsuccessful")
        # The documented no-taker quote response has transaction=null. Fail closed
        # if API behavior changes and a transaction or taker-dependent response appears.
        if "transaction" not in payload or payload["transaction"] is not None:
            raise JupiterQuoteError("Jupiter did not return a quote-only response with transaction=null")
        if (
            payload.get("inputMint", input_mint) != input_mint
            or payload.get("outputMint", output_mint) != output_mint
        ):
            raise JupiterQuoteError("Jupiter quote mint identity did not match request")
        input_amount = _raw_amount(payload.get("inAmount"), "inAmount")
        if input_amount != amount:
            raise JupiterQuoteError("Jupiter quote input amount did not match request")
        output_amount = _raw_amount(payload.get("outAmount"), "outAmount")
        threshold = _optional_raw_amount(payload.get("otherAmountThreshold"), "otherAmountThreshold")

        fee_bps = payload.get("feeBps")
        if fee_bps is not None and (type(fee_bps) is not int or not 0 <= fee_bps <= 10_000):
            raise JupiterQuoteError("invalid feeBps")
        fee_mint = payload.get("feeMint")
        if fee_mint is not None:
            try:
                fee_mint = _mint(fee_mint, "feeMint")
            except ValueError as exc:
                raise JupiterQuoteError("invalid feeMint") from exc
        router = payload.get("router")
        if not isinstance(router, str) or not router or len(router) > 64:
            raise JupiterQuoteError("invalid router")
        route_plan = payload.get("routePlan", [])
        if not isinstance(route_plan, list) or len(route_plan) > 100 or any(not isinstance(item, dict) for item in route_plan):
            raise JupiterQuoteError("invalid routePlan")
        request_id = payload.get("requestId")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128):
            raise JupiterQuoteError("invalid requestId")
        context_slot = payload.get("contextSlot")
        if context_slot is not None and (type(context_slot) is not int or context_slot <= 0):
            raise JupiterQuoteError("invalid contextSlot")

        return JupiterQuote(
            source="jupiter",
            network="solana",
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount_raw=input_amount,
            output_amount_raw=output_amount,
            other_amount_threshold_raw=threshold,
            fee_bps=fee_bps,
            fee_mint=fee_mint,
            price_impact_pct=_optional_price_impact(payload.get("priceImpactPct")),
            router=router,
            route_plan=tuple(route_plan),
            request_id=request_id,
            context_slot=context_slot,
            fetched_at=datetime.now(timezone.utc),
            transaction_present=False,
        )


class JupiterPriceError(JupiterQuoteError):
    """A safe, read-only Jupiter Price API V3 request failed."""


class JupiterPriceRateLimited(JupiterPriceError):
    """Jupiter returned HTTP 429; the shared keyless limiter has been deferred."""

    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Jupiter Price API rate limited; deferred for {retry_after_seconds:g}s")


@dataclass(frozen=True)
class JupiterPriceObservation:
    source: str
    network: str
    mint: str
    usd_price: Decimal
    decimals: int
    block_id: int
    price_change_24h: Decimal | None
    liquidity_usd: Decimal | None
    created_at: datetime | None
    fetched_at: datetime
    data_class: str = "indicative_price"
    source_url: str = "https://api.jup.ag/price/v3"


@dataclass(frozen=True)
class JupiterPriceBatch:
    requested_mints: tuple[str, ...]
    observations: tuple[JupiterPriceObservation, ...]
    missing_mints: tuple[str, ...]
    fetched_at: datetime
    source: str = "jupiter"
    network: str = "solana"
    data_class: str = "indicative_price"


def _price_decimal(
    value: object, field: str, *, allow_zero: bool = False, allow_negative: bool = False
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
        raise JupiterPriceError(f"invalid {field}")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise JupiterPriceError(f"invalid {field}") from exc
    if not parsed.is_finite():
        raise JupiterPriceError(f"invalid {field}")
    if not allow_negative and (parsed < 0 if allow_zero else parsed <= 0):
        raise JupiterPriceError(f"invalid {field}")
    return parsed


def _price_created_at(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise JupiterPriceError("invalid createdAt")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JupiterPriceError("invalid createdAt") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JupiterPriceError("createdAt must include a timezone")
    return parsed.astimezone(timezone.utc)


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
    return min(max(parsed, 2.0), 300.0)


class JupiterPriceV3Client:
    """Keyless, read-only Jupiter Price API V3 client for exact Solana mint addresses."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 8.0,
        min_interval_seconds: float = 2.0,
        transport: Callable[[str, float], bytes] | None = None,
        rate_limiter: _KeylessRateLimiter | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.jup.ag"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the official Jupiter HTTPS API host")
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
            or not 2 <= min_interval_seconds <= 60
        ):
            raise ValueError("min_interval_seconds must be between 2 and 60 for keyless 0.5 RPS access")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.min_interval_seconds = float(min_interval_seconds)
        self._transport = transport or self._fetch
        self._rate_limiter = rate_limiter or _SHARED_KEYLESS_RATE_LIMITER

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
                    raise JupiterPriceError("Jupiter Price API response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                delay = _retry_after_seconds(exc.headers)
                raise JupiterPriceRateLimited(delay) from None
            raise JupiterPriceError(f"Jupiter Price API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JupiterPriceError(
                f"Jupiter Price API transport failed: {type(exc).__name__}"
            ) from None

    def fetch_prices(self, mints: list[str] | tuple[str, ...]) -> JupiterPriceBatch:
        if not isinstance(mints, (list, tuple)) or not 1 <= len(mints) <= _MAX_PRICE_MINTS:
            raise ValueError("mints must contain between 1 and 50 Solana mint addresses")
        requested = tuple(_mint(mint, "mint") for mint in mints)
        if len(set(requested)) != len(requested):
            raise ValueError("mints must not contain duplicates")
        params = urllib.parse.urlencode({"ids": ",".join(requested)})
        self._rate_limiter.acquire(self.min_interval_seconds)
        try:
            raw = self._transport(f"{self.base_url}/price/v3?{params}", self.timeout_seconds)
        except JupiterPriceRateLimited as exc:
            self._rate_limiter.defer(exc.retry_after_seconds)
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise JupiterPriceError("invalid or oversized Jupiter Price API response")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JupiterPriceError("Jupiter Price API returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise JupiterPriceError("Jupiter Price API returned an invalid object")
        unexpected = set(payload) - set(requested)
        if unexpected:
            raise JupiterPriceError("Jupiter Price API returned an unrequested mint key")
        fetched_at = datetime.now(timezone.utc)
        observations: list[JupiterPriceObservation] = []
        missing: list[str] = []
        for mint in requested:
            item = payload.get(mint)
            if item is None:
                missing.append(mint)
                continue
            if not isinstance(item, dict):
                raise JupiterPriceError("Jupiter Price API returned an invalid mint observation")
            # Jupiter omits unreliable prices; a present-but-unpriced record is also missing.
            if "usdPrice" not in item or item.get("usdPrice") is None:
                missing.append(mint)
                continue
            usd_price = _price_decimal(item.get("usdPrice"), "usdPrice")
            decimals = item.get("decimals")
            if type(decimals) is not int or not 0 <= decimals <= _MAX_PRICE_DECIMALS:
                raise JupiterPriceError("invalid decimals")
            block_id = item.get("blockId")
            if type(block_id) is not int or not 0 < block_id <= _MAX_UINT64:
                raise JupiterPriceError("invalid blockId")
            change = item.get("priceChange24h")
            if change is not None:
                change = _price_decimal(
                    change, "priceChange24h", allow_zero=True, allow_negative=True
                )
            liquidity = item.get("liquidity")
            if liquidity is not None:
                liquidity = _price_decimal(liquidity, "liquidity", allow_zero=True)
            observations.append(JupiterPriceObservation(
                source="jupiter",
                network="solana",
                mint=mint,
                usd_price=usd_price,
                decimals=decimals,
                block_id=block_id,
                price_change_24h=change,
                liquidity_usd=liquidity,
                created_at=_price_created_at(item.get("createdAt")),
                fetched_at=fetched_at,
            ))
        return JupiterPriceBatch(
            requested_mints=requested,
            observations=tuple(observations),
            missing_mints=tuple(missing),
            fetched_at=fetched_at,
        )
