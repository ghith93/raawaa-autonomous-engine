from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
import re
from typing import Any

from .dexscreener import DexPair
from .rpc import EthereumV2PoolReader, EvmPoolSnapshot, RpcVerificationError
from .venues import Venue, get_venue

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
DEX_VENUES = {
    "uniswap": "uniswap_v2_ethereum",
    "uniswap_v2": "uniswap_v2_ethereum",
    "sushiswap": "sushiswap_v2_ethereum",
    "sushiswap_v2": "sushiswap_v2_ethereum",
}


@dataclass(frozen=True, slots=True)
class DiscoveredPool:
    pair: DexPair
    venue: Venue


def _address(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
        raise ValueError(f"{label} must be a 20-byte EVM address")
    if int(value[2:], 16) == 0:
        raise ValueError(f"{label} cannot be the zero address")
    return value.lower()


def _human(raw: int, decimals: int) -> str:
    with localcontext() as ctx:
        ctx.prec = 100
        return format(Decimal(raw) / (Decimal(10) ** decimals), "f")


def _raw_amount(value: Decimal, decimals: int) -> int:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("capital_quote must be a finite positive Decimal")
    with localcontext() as ctx:
        ctx.prec = max(100, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + decimals + 5)
        raw = value * (Decimal(10) ** decimals)
        if raw != raw.to_integral_value():
            raise ValueError("capital_quote exceeds quote-token decimal precision")
        result = int(raw)
    if not 0 < result < 2**256:
        raise ValueError("capital_quote raw amount must fit uint256")
    return result


def discover_pool_candidates(
    pairs: list[DexPair],
    *,
    quote_token: str,
    min_liquidity_usd: Decimal = Decimal("10000"),
    max_candidates: int = 5,
) -> list[tuple[DiscoveredPool, DiscoveredPool]]:
    """Use DEX Screener only for candidate discovery; exact token addresses define identity."""
    quote = _address(quote_token, "quote_token")
    if not isinstance(pairs, list):
        raise ValueError("pairs must be a list")
    if (
        not isinstance(min_liquidity_usd, Decimal)
        or not min_liquidity_usd.is_finite()
        or min_liquidity_usd < 0
    ):
        raise ValueError("min_liquidity_usd must be a finite non-negative Decimal")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 10:
        raise ValueError("max_candidates must be between 1 and 10")

    groups: dict[tuple[str, str], dict[str, list[DiscoveredPool]]] = {}
    for pair in pairs:
        if (
            not isinstance(pair, DexPair)
            or not isinstance(pair.chain, str)
            or not isinstance(pair.dex, str)
            or pair.chain.casefold() != "ethereum"
            or not isinstance(pair.liquidity_usd, Decimal)
            or not pair.liquidity_usd.is_finite()
            or pair.liquidity_usd <= 0
            or pair.liquidity_usd < min_liquidity_usd
        ):
            continue
        venue_id = DEX_VENUES.get(pair.dex.casefold())
        if venue_id is None:
            continue
        try:
            _address(pair.pair_address, "pair_address")
            base = _address(pair.base_address, "base_address")
            quote_address = _address(pair.quote_address, "pair_quote_address")
            if quote not in {base, quote_address} or base == quote_address:
                continue
            venue = get_venue(venue_id, 1)
        except ValueError:
            continue
        # Normalize base/quote ordering to the requested quote token, not symbols.
        base_token = quote_address if base == quote else base
        pool = DiscoveredPool(pair=pair, venue=venue)
        key = (base_token, quote)
        venue_pools = groups.setdefault(key, {}).setdefault(venue.id, [])
        if not any(
            item.pair.pair_address.casefold() == pair.pair_address.casefold()
            for item in venue_pools
        ):
            venue_pools.append(pool)
            venue_pools.sort(key=lambda item: item.pair.liquidity_usd, reverse=True)
            del venue_pools[max_candidates:]

    candidates = []
    for pools in groups.values():
        uniswap_pools = pools.get("uniswap_v2_ethereum", [])
        sushiswap_pools = pools.get("sushiswap_v2_ethereum", [])
        for first in uniswap_pools:
            for second in sushiswap_pools:
                if first.pair.pair_address.casefold() != second.pair.pair_address.casefold():
                    candidates.append((first, second))
    candidates.sort(
        key=lambda item: min(item[0].pair.liquidity_usd, item[1].pair.liquidity_usd),
        reverse=True,
    )
    return candidates[:max_candidates]


def _quote_route(
    reader: EthereumV2PoolReader,
    buy: EvmPoolSnapshot,
    sell: EvmPoolSnapshot,
    buy_venue: Venue,
    sell_venue: Venue,
    *,
    quote_token: str,
    base_token: str,
    capital_raw: int,
) -> dict[str, Any]:
    block = buy.state.block_number
    buy_amounts = reader.quote_v2_router_exact_input(
        buy_venue.router02, capital_raw, [quote_token, base_token], block,
    )
    sell_amounts = reader.quote_v2_router_exact_input(
        sell_venue.router02, buy_amounts[-1], [base_token, quote_token], block,
    )
    final_raw = sell_amounts[-1]
    gross_raw = final_raw - capital_raw
    with localcontext() as ctx:
        ctx.prec = 100
        gross_bps = Decimal(gross_raw) * Decimal(10000) / Decimal(capital_raw)
    return {
        "buy_venue": buy_venue.name,
        "sell_venue": sell_venue.name,
        "buy_pair": buy.state.pair_address,
        "sell_pair": sell.state.pair_address,
        "capital_quote_raw": str(capital_raw),
        "buy_base_out_raw": str(buy_amounts[-1]),
        "final_quote_raw": str(final_raw),
        "gross_profit_quote_raw": str(gross_raw),
        "gross_profit_quote": _human(gross_raw, buy.token0_decimals if buy.state.token0 == quote_token else buy.token1_decimals),
        "gross_return_bps": str(gross_bps),
        "gross_positive_before_gas": gross_raw > 0,
        "classification": "same_block_router_eth_call_gross_surplus_excludes_gas_and_execution",
    }


def scan_onchain_v2(
    query: str,
    *,
    quote_token: str,
    capital_quote: Decimal,
    min_liquidity_usd: Decimal = Decimal("10000"),
    max_candidates: int = 5,
    rpc_url: str,
    reader: EthereumV2PoolReader | None = None,
    pairs: list[DexPair] | None = None,
) -> dict[str, Any]:
    """Discover pairs via DEX Screener then verify and quote them at one canonical block."""
    from .dexscreener import DexScreenerClient

    quote = _address(quote_token, "quote_token")
    if not isinstance(query, str) or not query.strip() or len(query) > 120:
        raise ValueError("query must contain 1..120 characters")
    if not isinstance(capital_quote, Decimal) or not capital_quote.is_finite() or capital_quote <= 0:
        raise ValueError("capital_quote must be a finite positive Decimal")
    if (
        not isinstance(min_liquidity_usd, Decimal)
        or not min_liquidity_usd.is_finite()
        or min_liquidity_usd < 0
    ):
        raise ValueError("min_liquidity_usd must be a finite non-negative Decimal")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 10:
        raise ValueError("max_candidates must be between 1 and 10")
    if pairs is not None and not isinstance(pairs, list):
        raise ValueError("pairs must be a list")
    discovered = pairs if pairs is not None else DexScreenerClient().search_pairs(query)
    candidates = discover_pool_candidates(
        discovered, quote_token=quote,
        min_liquidity_usd=min_liquidity_usd, max_candidates=max_candidates,
    )
    rpc = reader or EthereumV2PoolReader(rpc_url, expected_chain_id=1)
    results = []
    rejected_candidates = []
    for first, second in candidates:
        snapshots = rpc.read_pools([
            (first.pair.pair_address, first.venue.swap_fee_bps),
            (second.pair.pair_address, second.venue.swap_fee_bps),
        ])
        if len(snapshots) != 2:
            raise ValueError("RPC reader returned an incomplete pool snapshot set")
        a, b = snapshots
        if a.state.chain_id != 1 or b.state.chain_id != 1:
            raise ValueError("Only Ethereum mainnet chain ID 1 is supported by this discovery path")
        if (
            a.state.block_number != b.state.block_number
            or a.state.block_hash.casefold() != b.state.block_hash.casefold()
        ):
            raise ValueError("Candidate pools are not from one canonical block")
        expected_tokens = {
            first.pair.base_address.casefold(), first.pair.quote_address.casefold(),
        }
        if expected_tokens != {a.state.token0.casefold(), a.state.token1.casefold()}:
            raise ValueError("On-chain source pair tokens differ from discovered token addresses")
        if expected_tokens != {b.state.token0.casefold(), b.state.token1.casefold()}:
            raise ValueError("On-chain destination pair tokens differ from discovered token addresses")
        base = next(token for token in expected_tokens if token != quote)
        if quote not in expected_tokens or base == quote:
            raise ValueError("Discovered candidate does not contain the requested quote token")
        decimals = {}
        for snapshot in (a, b):
            current = {
                snapshot.state.token0.casefold(): snapshot.token0_decimals,
                snapshot.state.token1.casefold(): snapshot.token1_decimals,
            }
            for token, value in current.items():
                if token in decimals and decimals[token] != value:
                    raise ValueError("Token decimals disagree between candidate pools")
                decimals[token] = value
        if set(decimals) != expected_tokens:
            raise ValueError("On-chain token decimal metadata is incomplete")
        quote_decimals = decimals[quote]
        capital_raw = _raw_amount(capital_quote, quote_decimals)

        try:
            for pool, venue in ((a, first.venue), (b, second.venue)):
                rpc.verify_v2_pool_factory(
                    pool.state.pair_address, venue.factory,
                    pool.state.token0, pool.state.token1, pool.state.block_number,
                )
                rpc.verify_v2_router_factory(
                    venue.router02, venue.factory, pool.state.block_number,
                )
        except RpcVerificationError as exc:
            # A DEX label may point at another AMM version; skip only identity mismatches.
            # Transport/provider failures remain fatal and are never hidden as candidate rejection.
            rejected_candidates.append({
                "source_pair": first.pair.pair_address,
                "destination_pair": second.pair.pair_address,
                "reason": str(exc),
                "classification": "candidate_rejected_protocol_identity_mismatch",
            })
            continue
        directions = [
            _quote_route(rpc, a, b, first.venue, second.venue,
                         quote_token=quote, base_token=base, capital_raw=capital_raw),
            _quote_route(rpc, b, a, second.venue, first.venue,
                         quote_token=quote, base_token=base, capital_raw=capital_raw),
        ]
        rpc.verify_canonical_block(a.state.block_number, a.state.block_hash)
        best = max(directions, key=lambda route: int(route["gross_profit_quote_raw"]))
        results.append({
            "chain_id": 1,
            "base_token": base,
            "quote_token": quote,
            "base_decimals": decimals[base],
            "quote_decimals": quote_decimals,
            "block_number": a.state.block_number,
            "block_hash": a.state.block_hash,
            "block_timestamp": a.block_timestamp,
            "source_pairs": [
                {"venue": first.venue.name, "pair": a.state.pair_address,
                 "discovery_url": first.pair.source_url,
                 "liquidity_usd": str(first.pair.liquidity_usd)},
                {"venue": second.venue.name, "pair": b.state.pair_address,
                 "discovery_url": second.pair.source_url,
                 "liquidity_usd": str(second.pair.liquidity_usd)},
            ],
            "directions": directions,
            "best_direction": best,
            "classification": "verified_same_block_v2_router_quotes_not_net_profit_or_executable_trade",
        })
    return {
        "mode": "read_only_onchain_v2_discovery",
        "discovery_source": "DEX Screener candidate metadata only",
        "quote_source": "Ethereum JSON-RPC eth_call to verified V2 routers",
        "query": query,
        "quote_token": quote,
        "capital_quote": str(capital_quote),
        "candidate_pairs_verified": len(results),
        "candidate_pairs_discovered": len(candidates),
        "candidate_pairs_rejected": len(rejected_candidates),
        "rejected_candidates": rejected_candidates,
        "opportunities": results,
        "live_execution": False,
        "wallet_or_signer_used": False,
        "transaction_submitted": False,
        "warning": (
            "Gross same-block router quote surplus only. Excludes gas, MEV, inclusion, "
            "state changes after the pinned block, token transfer taxes, and execution. "
            "No trade or paper order is created."
        ),
    }
