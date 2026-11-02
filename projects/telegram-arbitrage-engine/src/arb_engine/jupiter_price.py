from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable


DEFAULT_BASE_URL = "https://api.jup.ag"
_MAX_MINTS = 50
_MAX_RESPONSE_BYTES = 256_000
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {char: index for index, char in enumerate(_BASE58_ALPHABET)}
_ALLOWED_HOSTS = {"api.jup.ag"}


class JupiterPriceAPIError(RuntimeError):
    """A bounded read-only Jupiter Price API V3 request failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class JupiterPriceObservation:
    source_id: str
    market_family: str
    data_type: str
    mint: str
    asset_id: str
    usd_price: Decimal
    decimals: int
    block_id: int
    price_change_24h: Decimal | None
    liquidity_usd: Decimal | None
    created_at: str | None
    fetched_at_ms: int
    current_slot: int | None
    slot_age: int | None
    freshness_status: str


@dataclass(frozen=True, slots=True)
class JupiterPriceBatch:
    source_id: str
    data_type: str
    requested_mints: tuple[str, ...]
    observations: tuple[JupiterPriceObservation, ...]
    missing_mints: tuple[str, ...]
    fetched_at_ms: int


def validate_solana_mint(value: str) -> str:
    """Validate a canonical base58-encoded 32-byte Solana public key."""
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        raise ValueError("Solana mint must be a 32-byte base58 public key")
    number = 0
    for char in value:
        digit = _BASE58_INDEX.get(char)
        if digit is None:
            raise ValueError("Solana mint contains a non-base58 character")
        number = number * 58 + digit
    decoded = (
        number.to_bytes((number.bit_length() + 7) // 8, "big")
        if number
        else b""
    )
    leading_zeroes = len(value) - len(value.lstrip("1"))
    decoded = (b"\x00" * leading_zeroes) + decoded
    if len(decoded) != 32:
        raise ValueError("Solana mint must decode to exactly 32 bytes")
    return value


def _decimal(
    value: object,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise JupiterPriceAPIError(f"Jupiter returned an invalid {field}")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (str, int)):
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise JupiterPriceAPIError(f"Jupiter returned an invalid {field}") from exc
    else:
        raise JupiterPriceAPIError(f"Jupiter returned an invalid {field}")
    if (
        not parsed.is_finite()
        or len(parsed.as_tuple().digits) > 80
        or abs(parsed.adjusted()) > 100
        or (positive and parsed <= 0)
        or (nonnegative and parsed < 0)
    ):
        raise JupiterPriceAPIError(f"Jupiter returned an invalid {field}")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise JupiterPriceAPIError(f"Jupiter returned an invalid {field}")
    return value


def _parse_constant(value: str) -> None:
    raise JupiterPriceAPIError(f"Jupiter returned non-standard JSON number {value}")


class JupiterRateLimiter:
    """Thread-safe shared limiter. The keyless Price API budget is 0.5 requests/second."""

    def __init__(
        self,
        interval_seconds: float = 2.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or not 0.0 <= interval_seconds <= 3600.0
        ):
            raise ValueError("interval_seconds must be between 0 and 3600")
        self.interval_seconds = float(interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next_request_at - now
            if delay > 0:
                self._sleeper(delay)
                now = self._clock()
            self._next_request_at = max(now, self._next_request_at) + self.interval_seconds

    def defer(self, seconds: float) -> None:
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0.0 <= seconds <= 3600.0
        ):
            seconds = self.interval_seconds
        with self._lock:
            self._next_request_at = max(self._next_request_at, self._clock() + float(seconds))


_DEFAULT_RATE_LIMITER = JupiterRateLimiter(interval_seconds=2.0)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class JupiterPriceClient:
    """Dependency-free, keyless and read-only Jupiter Price API V3 client."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 8.0,
        rate_limiter: JupiterRateLimiter | None = None,
        transport: Callable[[str, float], bytes] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the allowlisted HTTPS Jupiter API host")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 1.0 <= timeout_seconds <= 30.0
        ):
            raise ValueError("timeout_seconds must be between 1 and 30")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.rate_limiter = rate_limiter or _DEFAULT_RATE_LIMITER
        self._transport = transport or self._fetch
        self._wall_clock = wall_clock

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
                    raise JupiterPriceAPIError("Jupiter response exceeded the size limit")
                return raw
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                retry_seconds = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_seconds = None
            raise JupiterPriceAPIError(
                f"Jupiter Price API returned HTTP {exc.code}",
                status_code=exc.code,
                retry_after_seconds=retry_seconds,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JupiterPriceAPIError(
                f"Jupiter Price API transport failed: {type(exc).__name__}",
            ) from None

    def get_prices(
        self,
        mints: list[str] | tuple[str, ...],
        *,
        current_slot: int | None = None,
        max_age_slots: int = 150,
    ) -> JupiterPriceBatch:
        if not isinstance(mints, (list, tuple)) or not 1 <= len(mints) <= _MAX_MINTS:
            raise ValueError("mints must contain between 1 and 50 Solana mint addresses")
        requested = tuple(validate_solana_mint(mint) for mint in mints)
        if len(set(requested)) != len(requested):
            raise ValueError("mints must not contain duplicates")
        if current_slot is not None and (type(current_slot) is not int or current_slot <= 0):
            raise ValueError("current_slot must be a positive integer when supplied")
        if type(max_age_slots) is not int or not 1 <= max_age_slots <= 100_000:
            raise ValueError("max_age_slots must be between 1 and 100000")

        query = urllib.parse.urlencode({"ids": ",".join(requested)})
        url = f"{self.base_url}/price/v3?{query}"
        self.rate_limiter.acquire()
        try:
            raw = self._transport(url, self.timeout_seconds)
        except JupiterPriceAPIError as exc:
            if exc.status_code == 429:
                self.rate_limiter.defer(
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else self.rate_limiter.interval_seconds
                )
            raise
        if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
            raise JupiterPriceAPIError("Jupiter response was invalid or exceeded the size limit")
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_parse_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JupiterPriceAPIError("Jupiter returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise JupiterPriceAPIError("Jupiter response must be a top-level mint map")
        requested_set = set(requested)
        unexpected = set(payload) - requested_set
        if unexpected:
            raise JupiterPriceAPIError("Jupiter returned an unrequested mint address")

        fetched_at_ms = int(self._wall_clock() * 1000)
        observations = []
        for mint in requested:
            record = payload.get(mint)
            if record is None:
                continue
            if not isinstance(record, dict):
                raise JupiterPriceAPIError("Jupiter mint price entry must be an object")
            price = _decimal(record.get("usdPrice"), "USD price", positive=True)
            decimals = record.get("decimals")
            if type(decimals) is not int or not 0 <= decimals <= 38:
                raise JupiterPriceAPIError("Jupiter returned invalid token decimals")
            block_id = _positive_int(record.get("blockId"), "Solana block ID")
            price_change = (
                _decimal(record["priceChange24h"], "24-hour price change")
                if record.get("priceChange24h") is not None
                else None
            )
            liquidity = (
                _decimal(record["liquidity"], "liquidity", nonnegative=True)
                if record.get("liquidity") is not None
                else None
            )
            created_at = record.get("createdAt")
            if created_at is not None:
                if not isinstance(created_at, str) or len(created_at) > 80:
                    raise JupiterPriceAPIError("Jupiter returned invalid createdAt metadata")
                try:
                    parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise JupiterPriceAPIError("Jupiter returned invalid createdAt metadata") from exc
                if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
                    raise JupiterPriceAPIError("Jupiter createdAt metadata must include a timezone")
            slot_age = current_slot - block_id if current_slot is not None else None
            if slot_age is not None and slot_age < 0:
                raise JupiterPriceAPIError("Jupiter price block is ahead of the supplied current slot")
            freshness = (
                "unverified_without_current_slot"
                if slot_age is None
                else ("fresh" if slot_age <= max_age_slots else "stale")
            )
            observations.append(JupiterPriceObservation(
                source_id="jupiter_price_v3",
                market_family="solana",
                data_type="indicative_price",
                mint=mint,
                asset_id=f"solana:{mint}",
                usd_price=price,
                decimals=decimals,
                block_id=block_id,
                price_change_24h=price_change,
                liquidity_usd=liquidity,
                created_at=created_at,
                fetched_at_ms=fetched_at_ms,
                current_slot=current_slot,
                slot_age=slot_age,
                freshness_status=freshness,
            ))

        observed_mints = {item.mint for item in observations}
        missing = tuple(mint for mint in requested if mint not in observed_mints)
        return JupiterPriceBatch(
            source_id="jupiter_price_v3",
            data_type="indicative_price",
            requested_mints=requested,
            observations=tuple(observations),
            missing_mints=missing,
            fetched_at_ms=fetched_at_ms,
        )
