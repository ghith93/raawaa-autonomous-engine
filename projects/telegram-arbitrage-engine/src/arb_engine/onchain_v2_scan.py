from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from .dexscreener import DexPairCandidate, DexScreenerClient
from .rpc import (
    EvmBlockContext,
    EthereumV2PoolReader,
    PoolFactoryMismatch,
)
from .venues import Venue, get_venue_by_dexscreener_id


def _address(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        raise ValueError(f"{label} must be a 20-byte EVM address")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a hexadecimal EVM address") from exc
    normalized = value.lower()
    if normalized == "0x" + "0" * 40:
        raise ValueError(f"{label} cannot be the zero address")
    return normalized


def _token_decimals(snapshot: Any, token: str) -> int:
    if token == snapshot.state.token0.lower():
        return snapshot.token0_decimals
    if token == snapshot.state.token1.lower():
        return snapshot.token1_decimals
    raise ValueError("Token is not present in the verified V2 pool")


def _human(raw: int, decimals: int) -> str:
    with localcontext() as decimal_context:
        decimal_context.prec = 100
        return format(Decimal(raw) / (Decimal(10) ** decimals), "f")


def _capital_decimal(value: str | Decimal) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("Capital amount must be a decimal string or Decimal, not a float")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Capital amount must be a valid decimal value") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Capital amount must be finite and positive")
    return amount


def _capital_to_raw(amount: Decimal, decimals: int) -> int:
    with localcontext() as decimal_context:
        decimal_context.prec = max(
            100, len(amount.as_tuple().digits) + abs(amount.as_tuple().exponent) + decimals + 5,
        )
        scaled = amount * (Decimal(10) ** decimals)
        if scaled != scaled.to_integral_value():
            raise ValueError("Capital amount has more fractional digits than the quote token supports")
        raw = int(scaled)
    if not 0 < raw < 2**256:
        raise ValueError("Capital amount must fit a positive uint256 raw-unit value")
    return raw


def _candidate_record(candidate: DexPairCandidate, venue: Venue, *, verified: bool, reason: str | None = None) -> dict:
    return {
        "dexscreener_id": candidate.dex,
        "venue_id": venue.id,
        "pair_address": candidate.pair_address,
        "base_token": candidate.base_address,
        "quote_token": candidate.quote_address,
        "base_symbol_metadata_only": candidate.base_symbol,
        "quote_symbol_metadata_only": candidate.quote_symbol,
        "source_url": candidate.source_url,
        "factory_verified": verified,
        "rejection_reason": reason,
    }


def scan_onchain_v2_candidates(
    *,
    token_address: str,
    quote_token_address: str,
    capital_quote: str | Decimal,
    rpc_url: str = "https://ethereum-rpc.publicnode.com",
    max_candidate_pairs: int = 4,
    max_rpc_requests: int = 32,
    dex_client: DexScreenerClient | None = None,
    reader: EthereumV2PoolReader | None = None,
) -> dict[str, Any]:
    """Discover with DEX Screener, then verify and quote both V2 routes on one pinned block.

    DEX Screener contributes candidate pair addresses and display metadata only.
    All pool identity, decimals, and swap amounts are read from Ethereum at one block.
    This is a read-only gross router quote, not a flash-swap settlement or net-profit claim.
    """
    token = _address(token_address, "token_address")
    quote_token = _address(quote_token_address, "quote_token_address")
    if token == quote_token:
        raise ValueError("Base token and explicit quote token must be different")
    if (
        not isinstance(max_candidate_pairs, int) or isinstance(max_candidate_pairs, bool)
        or not 1 <= max_candidate_pairs <= 4
    ):
        raise ValueError("max_candidate_pairs must be an integer from 1 to 4")
    if (
        not isinstance(max_rpc_requests, int) or isinstance(max_rpc_requests, bool)
        or not 1 <= max_rpc_requests <= 64
    ):
        raise ValueError("max_rpc_requests must be an integer from 1 to 64")
    capital_amount = _capital_decimal(capital_quote)

    dex_client = dex_client or DexScreenerClient()
    reader = reader or EthereumV2PoolReader(
        rpc_url, expected_chain_id=1, max_rpc_requests=max_rpc_requests,
    )
    discovered = dex_client.token_pair_candidates("ethereum", token)
    filtered: list[tuple[DexPairCandidate, Venue]] = []
    seen_pairs: set[str] = set()
    for candidate in discovered:
        if candidate.chain != "ethereum":
            continue
        if {candidate.base_address, candidate.quote_address} != {token, quote_token}:
            continue
        try:
            venue = get_venue_by_dexscreener_id(candidate.dex, 1)
            pair_address = _address(candidate.pair_address, "candidate pair address")
        except ValueError:
            continue
        if pair_address in seen_pairs:
            continue
        seen_pairs.add(pair_address)
        filtered.append((candidate, venue))

    if len(filtered) > max_candidate_pairs:
        raise ValueError(
            f"Filtered candidate count {len(filtered)} exceeds the configured limit "
            f"of {max_candidate_pairs}; narrow the token/quote pair or explicitly raise the bound"
        )
    if not filtered:
        return {
            "mode": "read_only_onchain_v2_candidate_scan",
            "status": "no_registered_venue_candidates",
            "chain_id": 1,
            "token_address": token,
            "quote_token_address": quote_token,
            "capital_quote_human": str(capital_amount),
            "candidate_pairs_discovered": len(discovered),
            "candidate_pairs_matched": 0,
            "candidate_pairs_factory_verified": 0,
            "routes": [],
            "candidate_verification": [],
            "live_execution": False,
            "transaction_submitted": False,
            "warning": "No DEX Screener candidates matched the explicit token pair and trusted venue registry.",
        }

    context: EvmBlockContext = reader.latest_block_context()
    if context.chain_id != 1:
        raise ValueError("On-chain candidate scan is restricted to Ethereum mainnet (chain ID 1)")
    candidate_verification: list[dict] = []
    verified_by_venue: dict[str, tuple[DexPairCandidate, Venue]] = {}
    for candidate, venue in filtered:
        try:
            reader.verify_v2_pool_factory(
                candidate.pair_address, venue.factory, token, quote_token, context.block_number,
            )
        except PoolFactoryMismatch:
            candidate_verification.append(
                _candidate_record(candidate, venue, verified=False, reason="v2_factory_mismatch"),
            )
            continue
        if venue.id in verified_by_venue:
            raise ValueError(f"Multiple factory-verified candidate pairs for venue {venue.id}")
        verified_by_venue[venue.id] = (candidate, venue)
        candidate_verification.append(_candidate_record(candidate, venue, verified=True))

    required_venues = {"uniswap_v2_ethereum", "sushiswap_v2_ethereum"}
    if set(verified_by_venue) != required_venues:
        reader.verify_canonical_block(context.block_number, context.block_hash)
        return {
            "mode": "read_only_onchain_v2_candidate_scan",
            "status": "insufficient_factory_verified_venues",
            "chain_id": context.chain_id,
            "block_number": context.block_number,
            "block_hash": context.block_hash,
            "block_timestamp": context.timestamp,
            "token_address": token,
            "quote_token_address": quote_token,
            "candidate_verification": candidate_verification,
            "candidate_pairs_discovered": len(discovered),
            "candidate_pairs_matched": len(filtered),
            "candidate_pairs_factory_verified": len(verified_by_venue),
            "verified_venues": sorted(verified_by_venue),
            "routes": [],
            "rpc_requests_used": getattr(reader, "rpc_request_count", None),
            "rpc_requests_budget": max_rpc_requests,
            "live_execution": False,
            "transaction_submitted": False,
            "warning": "Both trusted V2 venues must be factory-verified at the same pinned block before route quotes are attempted.",
        }

    ordered = [
        verified_by_venue["uniswap_v2_ethereum"],
        verified_by_venue["sushiswap_v2_ethereum"],
    ]
    snapshots = reader.read_pools(
        [(candidate.pair_address, venue.swap_fee_bps) for candidate, venue in ordered],
        block_context=context,
    )
    if len(snapshots) != 2:
        raise ValueError("RPC reader returned an incomplete verified pool snapshot set")
    for snapshot in snapshots:
        state = snapshot.state
        if (
            state.chain_id != context.chain_id
            or state.block_number != context.block_number
            or state.block_hash.lower() != context.block_hash.lower()
            or {state.token0.lower(), state.token1.lower()} != {token, quote_token}
        ):
            raise ValueError("Pool snapshot does not match the verified token pair and pinned block")

    base_decimals = _token_decimals(snapshots[0], token)
    quote_decimals = _token_decimals(snapshots[0], quote_token)
    for snapshot in snapshots[1:]:
        if _token_decimals(snapshot, token) != base_decimals:
            raise ValueError("Base-token decimals disagree between verified venues")
        if _token_decimals(snapshot, quote_token) != quote_decimals:
            raise ValueError("Quote-token decimals disagree between verified venues")
    capital_raw = _capital_to_raw(capital_amount, quote_decimals)

    for (_candidate, venue) in ordered:
        reader.verify_v2_router_factory(venue.router02, venue.factory, context.block_number)

    route_specs = (
        (0, 1),
        (1, 0),
    )
    routes = []
    for buy_index, sell_index in route_specs:
        buy_candidate, buy_venue = ordered[buy_index]
        sell_candidate, sell_venue = ordered[sell_index]
        buy_amounts = reader.quote_v2_router_exact_input(
            buy_venue.router02, capital_raw, [quote_token, token], context.block_number,
        )
        base_bought_raw = buy_amounts[-1]
        sell_amounts = reader.quote_v2_router_exact_input(
            sell_venue.router02, base_bought_raw, [token, quote_token], context.block_number,
        )
        quote_received_raw = sell_amounts[-1]
        gross_surplus_raw = quote_received_raw - capital_raw
        with localcontext() as decimal_context:
            decimal_context.prec = 100
            gross_return_bps = Decimal(gross_surplus_raw) * Decimal(10_000) / Decimal(capital_raw)
        routes.append({
            "buy_venue_id": buy_venue.id,
            "sell_venue_id": sell_venue.id,
            "buy_pair_address": buy_candidate.pair_address,
            "sell_pair_address": sell_candidate.pair_address,
            "quote_input_raw": str(capital_raw),
            "quote_input_human": str(capital_amount),
            "base_bought_raw": str(base_bought_raw),
            "base_bought_human": _human(base_bought_raw, base_decimals),
            "quote_received_raw": str(quote_received_raw),
            "quote_received_human": _human(quote_received_raw, quote_decimals),
            "gross_quote_surplus_raw": str(gross_surplus_raw),
            "gross_quote_surplus_human": _human(gross_surplus_raw, quote_decimals),
            "positive_gross_surplus": gross_surplus_raw > 0,
            "gross_return_bps": str(gross_return_bps),
            "buy_router_amounts_raw": [str(value) for value in buy_amounts],
            "sell_router_amounts_raw": [str(value) for value in sell_amounts],
            "classification": "same_block_router_round_trip_quote_gross_only",
        })

    reader.verify_canonical_block(context.block_number, context.block_hash)
    best_route = max(routes, key=lambda route: int(route["gross_quote_surplus_raw"]))
    return {
        "mode": "read_only_onchain_v2_candidate_scan",
        "status": "quoted",
        "chain_id": context.chain_id,
        "block_number": context.block_number,
        "block_hash": context.block_hash,
        "block_timestamp": context.timestamp,
        "token_address": token,
        "base_token_decimals": base_decimals,
        "quote_token_address": quote_token,
        "quote_token_decimals": quote_decimals,
        "capital_quote_raw": str(capital_raw),
        "capital_quote_human": str(capital_amount),
        "candidate_verification": candidate_verification,
        "candidate_pairs_discovered": len(discovered),
        "candidate_pairs_matched": len(filtered),
        "candidate_pairs_factory_verified": len(verified_by_venue),
        "venues": [
            {
                "venue_id": venue.id,
                "name": venue.name,
                "pair_address": candidate.pair_address,
                "factory": venue.factory,
                "router02": venue.router02,
                "swap_fee_bps_assumed": venue.swap_fee_bps,
                "dexscreener_source_url": candidate.source_url,
            }
            for candidate, venue in ordered
        ],
        "routes": routes,
        "best_route": best_route,
        "best_route_positive_gross_surplus": best_route["positive_gross_surplus"],
        "rpc_requests_used": getattr(reader, "rpc_request_count", None),
        "rpc_requests_budget": max_rpc_requests,
        "gas_included": False,
        "net_profit_claim": False,
        "live_execution": False,
        "transaction_submitted": False,
        "wallet_or_signer_used": False,
        "warning": (
            "DEX Screener was used only for pair discovery. Router quotes are on-chain and pinned "
            "to one block. Gross quote-token surplus excludes gas, flash-swap repayment mechanics, "
            "MEV, inclusion risk, transfer taxes, revert risk, and execution overhead; no transaction "
            "was built, signed, or submitted."
        ),
    }
