from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import combinations

from .dexscreener import DexPair


@dataclass(frozen=True, slots=True)
class PriceSpread:
    chain: str
    base_symbol: str
    quote_symbol: str
    buy_venue: str
    sell_venue: str
    buy_price_usd: Decimal
    sell_price_usd: Decimal
    gross_spread_bps: Decimal
    min_pool_liquidity_usd: Decimal
    total_24h_volume_usd: Decimal
    data_age_seconds: Decimal
    buy_pair_url: str
    sell_pair_url: str
    classification: str = "indicative_only_not_executable"


def find_spreads(
    pairs: list[DexPair],
    *,
    min_liquidity_usd: Decimal = Decimal("10000"),
    min_volume_24h_usd: Decimal = Decimal("1000"),
    max_data_age_seconds: Decimal = Decimal("20"),
) -> list[PriceSpread]:
    """Compare indicative USD marks for matching pools; never creates an executable quote."""
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
           for v in (min_liquidity_usd, min_volume_24h_usd, max_data_age_seconds)):
        raise ValueError("Scanner thresholds must be finite non-negative Decimals")

    groups: dict[tuple[str, str, str], list[DexPair]] = {}
    now = datetime.now(timezone.utc)
    for pair in pairs:
        if pair.fetched_at.tzinfo is None or pair.fetched_at.utcoffset() is None or pair.fetched_at > now:
            continue
        age = Decimal(str((now - pair.fetched_at).total_seconds()))
        if pair.liquidity_usd < min_liquidity_usd or pair.volume_24h_usd < min_volume_24h_usd:
            continue
        if age > max_data_age_seconds:
            continue
        key = (pair.chain, pair.base_address, pair.quote_address)
        groups.setdefault(key, []).append(pair)

    spreads = []
    for group in groups.values():
        for left, right in combinations(group, 2):
            if left.dex.casefold() == right.dex.casefold():
                continue
            buy, sell = (left, right) if left.price_usd <= right.price_usd else (right, left)
            if buy.price_usd <= 0:
                continue
            gross_bps = (sell.price_usd / buy.price_usd - Decimal("1")) * Decimal("10000")
            if gross_bps <= 0:
                continue
            spreads.append(PriceSpread(
                chain=buy.chain, base_symbol=buy.base_symbol, quote_symbol=buy.quote_symbol,
                buy_venue=buy.dex, sell_venue=sell.dex, buy_price_usd=buy.price_usd,
                sell_price_usd=sell.price_usd, gross_spread_bps=gross_bps,
                min_pool_liquidity_usd=min(buy.liquidity_usd, sell.liquidity_usd),
                total_24h_volume_usd=buy.volume_24h_usd + sell.volume_24h_usd,
                data_age_seconds=max(Decimal("0"), Decimal(str((now - min(buy.fetched_at, sell.fetched_at)).total_seconds()))),
                buy_pair_url=buy.source_url, sell_pair_url=sell.source_url,
            ))
    return sorted(spreads, key=lambda item: item.gross_spread_bps, reverse=True)
