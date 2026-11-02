from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

BASE_URL = "https://api.dexscreener.com"
USER_AGENT = "telegram-arbitrage-engine/0.14 (+read-only research)"
MAX_RESPONSE_BYTES = 2_000_000
MAX_TOKEN_PAIR_CANDIDATES = 200
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class MarketDataError(RuntimeError):
    """Raised when the public market-data source cannot provide valid data."""


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


@dataclass(frozen=True, slots=True)
class DexPairCandidate:
    """DEX Screener discovery metadata only; no market metrics are trusted for execution."""

    chain: str
    dex: str
    pair_address: str
    base_address: str
    base_symbol: str | None
    quote_address: str
    quote_symbol: str | None
    fetched_at: datetime
    pair_created_at_ms: int | None
    source_url: str | None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DexPair:
    chain: str
    dex: str
    pair_address: str
    base_address: str
    base_symbol: str
    quote_address: str
    quote_symbol: str
    price_usd: Decimal
    liquidity_usd: Decimal
    volume_24h_usd: Decimal
    fetched_at: datetime
    pair_created_at_ms: int | None
    source_url: str


class DexScreenerClient:
    """Read-only public client; deliberately exposes no wallet or order methods."""

    def __init__(self, timeout_seconds: float = 10.0, min_request_interval: float = 1.0):
        if timeout_seconds <= 0 or min_request_interval < 0:
            raise ValueError("Invalid HTTP client timing configuration")
        self.timeout_seconds = timeout_seconds
        self.min_request_interval = min_request_interval
        self._last_request = 0.0

    def token_pair_candidates(self, chain: str, token_address: str) -> list[DexPairCandidate]:
        """Discover pair addresses for a token; all price/reserve decisions remain on-chain."""
        if not isinstance(chain, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", chain.strip().lower()):
            raise ValueError("Chain slug must contain 1..32 lowercase alphanumeric/hyphen characters")
        chain = chain.strip().lower()
        if (
            not isinstance(token_address, str) or not _ADDRESS.fullmatch(token_address)
            or token_address.lower() == "0x" + "0" * 40
        ):
            raise ValueError("Token address must be a non-zero 20-byte EVM address")
        payload = self._get(f"/token-pairs/v1/{chain}/{token_address}", {})
        if not isinstance(payload, list):
            raise MarketDataError("Unexpected DEX Screener token-pairs response")
        if len(payload) > MAX_TOKEN_PAIR_CANDIDATES:
            raise MarketDataError(
                f"Token-pairs response exceeded {MAX_TOKEN_PAIR_CANDIDATES} candidate safety limit"
            )
        fetched_at = datetime.now(timezone.utc)
        return [
            candidate for row in payload
            if (candidate := self._parse_candidate(row, fetched_at)) is not None
        ]

    @staticmethod
    def _parse_candidate(row: Any, fetched_at: datetime) -> DexPairCandidate | None:
        if not isinstance(row, dict):
            return None
        base = row.get("baseToken")
        quote = row.get("quoteToken")
        if not isinstance(base, dict) or not isinstance(quote, dict):
            return None
        chain = row.get("chainId")
        dex = row.get("dexId")
        pair = row.get("pairAddress")
        base_address = base.get("address")
        quote_address = quote.get("address")
        if not all(isinstance(value, str) and value.strip() for value in (
            chain, dex, pair, base_address, quote_address,
        )):
            return None
        if (
            not _ADDRESS.fullmatch(pair)
            or not _ADDRESS.fullmatch(base_address)
            or not _ADDRESS.fullmatch(quote_address)
            or pair.lower() == "0x" + "0" * 40
            or base_address.lower() == "0x" + "0" * 40
            or quote_address.lower() == "0x" + "0" * 40
            or base_address.lower() == quote_address.lower()
        ):
            return None
        source_url = row.get("url")
        if isinstance(source_url, str):
            try:
                parsed_url = urlsplit(source_url)
                port = parsed_url.port
            except ValueError:
                source_url = None
            else:
                if (
                    parsed_url.scheme != "https"
                    or parsed_url.hostname not in {"dexscreener.com", "www.dexscreener.com"}
                    or parsed_url.username or parsed_url.password
                    or port not in (None, 443)
                    or parsed_url.query or parsed_url.fragment
                    or len(source_url) > 2048
                ):
                    source_url = None
        else:
            source_url = None
        labels = row.get("labels")
        normalized_labels = tuple(
            label.strip().lower() for label in labels
            if isinstance(label, str) and label.strip()
        ) if isinstance(labels, list) else ()
        created = row.get("pairCreatedAt")
        created_ms = created if isinstance(created, int) and not isinstance(created, bool) and created > 0 else None
        base_symbol = base.get("symbol") if isinstance(base.get("symbol"), str) else None
        quote_symbol = quote.get("symbol") if isinstance(quote.get("symbol"), str) else None
        return DexPairCandidate(
            chain=chain.lower(), dex=dex.lower(), pair_address=pair.lower(),
            base_address=base_address.lower(), base_symbol=base_symbol,
            quote_address=quote_address.lower(), quote_symbol=quote_symbol,
            fetched_at=fetched_at, pair_created_at_ms=created_ms,
            source_url=source_url, labels=normalized_labels,
        )

    def search_pairs(self, query: str) -> list[DexPair]:
        if not isinstance(query, str):
            raise ValueError("Search query must be text")
        query = query.strip()
        if not query or len(query) > 120:
            raise ValueError("Search query must contain 1..120 characters")
        payload = self._get("/latest/dex/search", {"q": query})
        return self.parse_pairs(payload, datetime.now(timezone.utc))

    @classmethod
    def parse_pairs(cls, payload: Any, fetched_at: datetime | None = None) -> list[DexPair]:
        """Parse API/fixture payload. fetched_at is retrieval time, not on-chain quote time."""
        rows = payload.get("pairs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise MarketDataError("Unexpected DEX Screener response: 'pairs' array missing")
        retrieved = fetched_at or datetime.now(timezone.utc)
        if not isinstance(retrieved, datetime) or retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        result = []
        for row in rows:
            parsed = cls._parse_pair(row, retrieved)
            if parsed is not None:
                result.append(parsed)
        return result

    def _get(self, path: str, params: dict[str, str]) -> Any:
        wait = self.min_request_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        url = f"{BASE_URL}{path}?{urlencode(params)}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("Market-data response exceeded 2 MB safety limit")
                payload = json.loads(body)
        except HTTPError as exc:
            if exc.code == 429:
                raise MarketDataError("DEX Screener rate limit reached; retry later") from exc
            raise MarketDataError(f"DEX Screener HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"DEX Screener request failed: {type(exc).__name__}") from exc
        finally:
            self._last_request = time.monotonic()
        return payload

    @staticmethod
    def _parse_pair(row: Any, fetched_at: datetime) -> DexPair | None:
        if not isinstance(row, dict):
            return None
        base = row.get("baseToken")
        quote = row.get("quoteToken")
        liquidity = row.get("liquidity")
        volume = row.get("volume")
        if not all(isinstance(value, dict) for value in (base, quote, liquidity, volume)):
            return None

        price = _decimal(row.get("priceUsd"))
        liquidity_usd = _decimal(liquidity.get("usd"))
        volume_usd = _decimal(volume.get("h24"))
        fields = (
            row.get("chainId"), row.get("dexId"), row.get("pairAddress"),
            base.get("address"), base.get("symbol"), quote.get("address"),
            quote.get("symbol"), row.get("url"),
        )
        if not all(isinstance(value, str) and value.strip() for value in fields):
            return None
        try:
            parsed_url = urlsplit(fields[7])
            port = parsed_url.port
        except ValueError:
            return None
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in {"dexscreener.com", "www.dexscreener.com"}
            or parsed_url.username or parsed_url.password
            or port not in (None, 443)
            or parsed_url.query or parsed_url.fragment
            or len(fields[7]) > 2048
        ):
            return None
        if (
            price is None or price <= 0
            or liquidity_usd is None or liquidity_usd <= 0
            or volume_usd is None or volume_usd < 0
        ):
            return None
        created = row.get("pairCreatedAt")
        created_ms = created if isinstance(created, int) and not isinstance(created, bool) and created > 0 else None
        return DexPair(
            chain=fields[0].lower(), dex=fields[1], pair_address=fields[2],
            base_address=fields[3].lower(), base_symbol=fields[4],
            quote_address=fields[5].lower(), quote_symbol=fields[6],
            price_usd=price, liquidity_usd=liquidity_usd, volume_24h_usd=volume_usd,
            fetched_at=fetched_at, pair_created_at_ms=created_ms, source_url=fields[7],
        )
