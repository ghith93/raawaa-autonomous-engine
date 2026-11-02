from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from .dexscreener import DexScreenerClient, MarketDataError
from .flash_swap_cli import run_flash_swap_simulation
from .paper_observer import observe_flash_swap_outcome
from .onchain_v2_scan import scan_onchain_v2_candidates
from .fork_sim import AnvilForkSimulator, ForkSimulationError
from .ledger import OpportunityLedger
from .models import MarketQuote, Opportunity
from .market_sources.jupiter import (
    JupiterPriceV3Client, JupiterQuoteOnlyClient, JupiterQuoteError,
)
from .market_sources.bybit import BybitPublicMarketData
from .market_sources.binance import BinancePublicMarketData
from .market_sources.kraken import KrakenPublicMarketData
from .market_sources.coinbase import CoinbaseExchangePublicMarketData
from .market_sources.okx import OKXPublicSpotMarketData
from .market_sources.cross_venue import InstrumentRegistry, compare_cex_spot_orderbooks
from .opportunity_costs import OpportunityCosts
from .risk import validate_opportunity
from .rpc import DEFAULT_ETHEREUM_RPC, EthereumV2PoolReader, RpcReadError
from .scanner import find_spreads
from .v2 import simulate_round_trip
from .venues import list_venues, resolve_venue_config
from .venue_smoke import (
    JsonRpcClient, VenueVerificationError,
    verify_registry_venue_deployments, verify_registry_venues,
)
from .v3_math import get_sqrt_ratio_at_tick
from .v3_rpc import DEFAULT_V3_ETHEREUM_RPC, EvmV3PoolReader
from .v3_simulator import simulate_v3_swap
from .cex_universe import VenueRequest, scan_cex_universe, CexUniverseScanError, SUPPORTED_VENUES


def _fetch_cex_snapshot(venue: str, instrument_id: str):
    """Fetch public instrument metadata and one bounded spot book; never use private APIs."""
    if venue == "bybit":
        client = BybitPublicMarketData()
        metadata = client.fetch_instrument(symbol=instrument_id, category="spot")
        book = client.fetch_orderbook(category="spot", symbol=metadata.instrument_id, limit=50)
    elif venue == "binance":
        client = BinancePublicMarketData()
        metadata = client.fetch_instrument(symbol=instrument_id)
        book = client.fetch_orderbook(symbol=metadata.instrument_id, limit=100)
    elif venue == "kraken":
        client = KrakenPublicMarketData()
        metadata = client.fetch_instrument(pair=instrument_id)
        book = client.fetch_orderbook(pair=metadata.instrument_id, count=100)
    elif venue == "coinbase":
        client = CoinbaseExchangePublicMarketData()
        metadata = client.fetch_instrument(product_id=instrument_id)
        book = client.fetch_orderbook(product_id=metadata.instrument_id, level=2)
    elif venue == "okx":
        client = OKXPublicSpotMarketData()
        metadata = client.fetch_instrument(inst_id=instrument_id)
        book = client.fetch_orderbook(inst_id=metadata.instrument_id, depth=20)
    else:
        raise ValueError("unsupported CEX venue")
    if metadata.status.upper() != "TRADING":
        raise ValueError(f"{venue}:{metadata.instrument_id} is not currently trading")
    return metadata, book


def run_cex_scan(args) -> dict:
    """Compose two public CEX books with explicit identity, depth and cost gates."""
    if args.venue_a == args.venue_b:
        raise ValueError("venue-a and venue-b must be different exchanges")
    metadata_a, book_a = _fetch_cex_snapshot(args.venue_a, args.instrument_a)
    metadata_b, book_b = _fetch_cex_snapshot(args.venue_b, args.instrument_b)
    canonical_ids = {
        (args.venue_a, metadata_a.base_asset_code): args.a_base_asset_id,
        (args.venue_a, metadata_a.quote_asset_code): args.a_quote_currency_id,
        (args.venue_b, metadata_b.base_asset_code): args.b_base_asset_id,
        (args.venue_b, metadata_b.quote_asset_code): args.b_quote_currency_id,
    }
    registry = InstrumentRegistry.from_metadata([metadata_a, metadata_b], canonical_ids)
    costs_by_direction = {
        (args.venue_a, args.venue_b): OpportunityCosts(
            buy_fee_bps=args.fee_a_bps,
            sell_fee_bps=args.fee_b_bps,
            buy_slippage_bps="0",
            sell_slippage_bps="0",
            latency_cost_bps=args.latency_cost_bps,
        ),
        (args.venue_b, args.venue_a): OpportunityCosts(
            buy_fee_bps=args.fee_b_bps,
            sell_fee_bps=args.fee_a_bps,
            buy_slippage_bps="0",
            sell_slippage_bps="0",
            latency_cost_bps=args.latency_cost_bps,
        ),
    }
    opportunities = compare_cex_spot_orderbooks(
        book_a=book_a,
        book_b=book_b,
        registry=registry,
        base_quantity=args.base_quantity,
        costs_by_direction=costs_by_direction,
        now=datetime.now(timezone.utc),
        max_quote_age_seconds=args.max_quote_age_seconds,
        max_quote_skew_seconds=args.max_quote_skew_seconds,
    )

    def evaluation_dict(evaluation):
        if evaluation is None:
            return None
        return {
            "status": evaluation.status,
            "gross_spread_bps": str(evaluation.gross_spread_bps) if evaluation.gross_spread_bps is not None else None,
            "gross_profit_quote": str(evaluation.gross_profit_quote) if evaluation.gross_profit_quote is not None else None,
            "known_cost_quote": str(evaluation.known_cost_quote),
            "net_profit_quote_estimate": str(evaluation.net_profit_quote) if evaluation.net_profit_quote is not None else None,
            "missing_required_costs": list(evaluation.missing_required_costs),
            "max_quote_age_seconds": str(evaluation.max_quote_age_seconds) if evaluation.max_quote_age_seconds is not None else None,
            "execution_verified": evaluation.execution_verified,
        }

    def opportunity_dict(item):
        return {
            "status": item.status,
            "buy_venue": item.buy_venue_id,
            "sell_venue": item.sell_venue_id,
            "base_asset_id": item.base_asset_id,
            "quote_currency_id": item.quote_currency_id,
            "requested_base_quantity": str(item.requested_base_quantity),
            "buy_filled_base_quantity": str(item.buy_filled_base_quantity),
            "sell_filled_base_quantity": str(item.sell_filled_base_quantity),
            "buy_fill_fraction": str(item.buy_fill_fraction),
            "sell_fill_fraction": str(item.sell_fill_fraction),
            "buy_vwap": str(item.buy_vwap) if item.buy_vwap is not None else None,
            "sell_vwap": str(item.sell_vwap) if item.sell_vwap is not None else None,
            "buy_quote_spent": str(item.buy_quote_spent),
            "sell_quote_received": str(item.sell_quote_received),
            "gross_profit_quote": str(item.gross_profit_quote) if item.gross_profit_quote is not None else None,
            "evaluation": evaluation_dict(item.evaluation),
        }

    def instrument_dict(metadata, book):
        return {
            "venue": metadata.venue_id,
            "instrument_id": metadata.instrument_id,
            "base_asset_code": metadata.base_asset_code,
            "quote_asset_code": metadata.quote_asset_code,
            "status": metadata.status,
            "base_step_size": str(metadata.base_step_size),
            "price_tick_size": str(metadata.price_tick_size),
            "min_base_quantity": str(metadata.min_base_quantity) if metadata.min_base_quantity is not None else None,
            "min_quote_notional": str(metadata.min_quote_notional) if metadata.min_quote_notional is not None else None,
            "metadata_fetched_at": metadata.fetched_at.isoformat(),
            "book_fetched_at": book.fetched_at.isoformat(),
            "bid_levels": len(book.bids),
            "ask_levels": len(book.asks),
        }

    return {
        "mode": "read_only_cex_spot_depth_comparison",
        "source": "unauthenticated public exchange REST market-data endpoints",
        "status": "complete",
        "venues": [
            instrument_dict(metadata_a, book_a),
            instrument_dict(metadata_b, book_b),
        ],
        "canonical_mapping": {
            args.venue_a: {
                "base_asset_id": args.a_base_asset_id,
                "quote_currency_id": args.a_quote_currency_id,
            },
            args.venue_b: {
                "base_asset_id": args.b_base_asset_id,
                "quote_currency_id": args.b_quote_currency_id,
            },
        },
        "base_quantity": args.base_quantity,
        "cost_assumptions": {
            "venue_a_taker_fee_bps": args.fee_a_bps,
            "venue_b_taker_fee_bps": args.fee_b_bps,
            "latency_cost_bps": args.latency_cost_bps,
            "separate_slippage_bps": "0 (depth-swept VWAP includes observed book-walk impact)",
            "account_specific_fees_inferred": False,
        },
        "freshness_limits": {
            "max_quote_age_seconds": args.max_quote_age_seconds,
            "max_quote_skew_seconds": args.max_quote_skew_seconds,
        },
        "opportunities": [opportunity_dict(item) for item in opportunities],
        "live_execution": False,
        "orders_submitted": False,
        "net_profit_claim": False,
        "warning": (
            "A positive net value is only a research estimate from public depth snapshots and operator-supplied costs. "
            "It does not verify account fee tiers, inventory availability, custody/rebalancing, coordinated fills, "
            "settlement, latency under execution, or realized PnL. Missing required costs leave the net estimate unset."
        ),
    }


def demo() -> dict:
    now = datetime.now(timezone.utc)
    buy = MarketQuote("dex-a", "ethereum", "TOKEN", "USDC", Decimal("100"), Decimal("10000"), Decimal("25"), Decimal("15"), Decimal("0.04"), now)
    sell = MarketQuote("dex-b", "ethereum", "TOKEN", "USDC", Decimal("101.20"), Decimal("10000"), Decimal("25"), Decimal("15"), Decimal("0.04"), now)
    result = validate_opportunity(Opportunity(Decimal("50"), buy, sell))
    b = result.breakdown
    return {
        "mode": "simulation_only", "accepted": result.accepted, "reason": result.reason,
        "profit": str(b.net_profit_usd) if b else None,
        "net_bps": str(b.net_bps) if b else None, "live_execution": False,
    }


def scan_with_spreads(
    query: str, min_liquidity: Decimal, min_volume: Decimal,
) -> tuple[dict, list]:
    """Collect indicative DEX data without persisting it."""
    pairs = DexScreenerClient().search_pairs(query)
    spreads = find_spreads(pairs, min_liquidity_usd=min_liquidity,
                           min_volume_24h_usd=min_volume)
    return ({
        "mode": "read_only_indicative_scan", "source": "DEX Screener public API",
        "query": query, "pairs_received": len(pairs),
        "cross_dex_comparisons": len(spreads),
        "live_execution": False,
        "warning": (
            "Indicative USD pool marks only; not router quotes or executable prices. "
            "The source does not provide an on-chain quote timestamp here; retrieval age "
            "is not proof that the quoted pool state is fresh."
        ),
        "opportunities": [
            {
                "chain": x.chain, "pair": f"{x.base_symbol}/{x.quote_symbol}",
                "buy_venue": x.buy_venue, "sell_venue": x.sell_venue,
                "buy_price_usd": str(x.buy_price_usd), "sell_price_usd": str(x.sell_price_usd),
                "gross_spread_bps": str(x.gross_spread_bps),
                "min_pool_liquidity_usd": str(x.min_pool_liquidity_usd),
                "combined_24h_volume_usd": str(x.total_24h_volume_usd),
                "retrieval_age_seconds": str(x.retrieval_age_seconds),
                "quote_timestamp_status": x.quote_timestamp_status,
                "buy_pair_url": x.buy_pair_url, "sell_pair_url": x.sell_pair_url,
                "classification": x.classification,
            } for x in spreads[:25]
        ],
    }, spreads)


def scan(query: str, min_liquidity: Decimal, min_volume: Decimal, ledger_path: str) -> dict:
    result, spreads = scan_with_spreads(query, min_liquidity, min_volume)
    result["observations_recorded"] = OpportunityLedger(ledger_path).record(spreads)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulation-only spot arbitrage research engine")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("demo", help="run deterministic synthetic risk demo")
    report_parser = sub.add_parser("report", help="summarize repeated indicative observations from the local ledger")
    report_parser.add_argument("--ledger", default="data/opportunities.sqlite3")
    report_parser.add_argument("--limit", type=int, default=500)
    venues_parser = sub.add_parser("venues", help="list source-documented V2 venue deployments")
    venues_parser.add_argument("--chain-id", type=int, help="filter by EVM chain ID")
    venue_check_parser = sub.add_parser("venue-check", help="verify registered Ethereum V2 deployments using read-only public RPC calls")
    venue_check_parser.add_argument("--chain-id", type=int, default=1)
    venue_check_parser.add_argument("--rpc-url", default=DEFAULT_ETHEREUM_RPC)
    venue_check_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    venue_deployment_parser = sub.add_parser(
        "venue-deployment-check",
        help="verify registered V2 factory/router deployments on any EVM chain using read-only RPC calls",
    )
    venue_deployment_parser.add_argument("--chain-id", type=int, required=True)
    venue_deployment_parser.add_argument("--rpc-url", required=True)
    venue_deployment_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    v2_parser = sub.add_parser("v2-sim", help="read-only same-block Ethereum V2 reserve simulation")
    v2_parser.add_argument("--buy-pair", required=True)
    v2_parser.add_argument("--buy-venue", help="known source-documented venue ID; auto-fills fee/factory/router")
    v2_parser.add_argument("--buy-fee-bps", type=int, help="required when --buy-venue is not supplied")
    v2_parser.add_argument("--sell-pair", required=True)
    v2_parser.add_argument("--sell-venue", help="known source-documented venue ID; auto-fills fee/factory/router")
    v2_parser.add_argument("--sell-fee-bps", type=int, help="required when --sell-venue is not supplied")
    v2_parser.add_argument("--quote-token", required=True)
    v2_parser.add_argument("--base-token", required=True)
    v2_parser.add_argument("--capital-quote", required=True, help="human quote-token amount, e.g. 50")
    v2_parser.add_argument("--rpc-url", default=DEFAULT_ETHEREUM_RPC)
    v2_parser.add_argument("--chain-id", type=int, default=1, help="expected EVM chain ID (default: Ethereum mainnet)")
    v2_parser.add_argument(
        "--slippage-bps", type=int, default=50,
        help="per-leg minimum-output tolerance for router quote reporting (default: 50 bps)",
    )
    v2_parser.add_argument(
        "--gas-units-assumption", type=int,
        help="optional explicit gas-units assumption; reports native-asset cost only, not net profit",
    )
    v2_parser.add_argument(
        "--gas-safety-bps", type=int, default=2000,
        help="extra gas-unit buffer in basis points (default: 2000 = 20%)",
    )
    v2_parser.add_argument("--buy-router", help="optional V2 router contract for same-block getAmountsOut validation")
    v2_parser.add_argument("--sell-router", help="optional V2 router contract for same-block getAmountsOut validation")
    v2_parser.add_argument("--buy-factory", help="expected V2 factory for the buy pair; verifies factory() and getPair()")
    v2_parser.add_argument("--sell-factory", help="expected V2 factory for the sell pair; verifies factory() and getPair()")

    v3_parser = sub.add_parser(
        "v3-sim",
        help="read-only same-block Uniswap V3 snapshot and bounded multi-tick simulation",
    )
    v3_parser.add_argument("--pool", required=True, help="V3 pool contract address")
    v3_parser.add_argument(
        "--direction", required=True, choices=("token0_to_token1", "token1_to_token0"),
        help="swap direction based on on-chain token0/token1 order",
    )
    v3_parser.add_argument(
        "--amount-specified", required=True, type=int,
        help="raw token units: positive for exact input, negative for exact output",
    )
    v3_limit_group = v3_parser.add_mutually_exclusive_group(required=True)
    v3_limit_group.add_argument(
        "--limit-tick", type=int,
        help="absolute hard price boundary tick on the requested side of current price",
    )
    v3_limit_group.add_argument(
        "--limit-ticks", type=int,
        help="positive tick distance from current slot0.tick; direction selects lower or upper limit",
    )
    v3_parser.add_argument("--chain-id", type=int, default=1)
    v3_parser.add_argument("--rpc-url", help="HTTPS read-only RPC; defaults to keyless Nodely for Ethereum only")
    v3_parser.add_argument("--factory", help="expected trusted V3 factory; required outside Ethereum")
    v3_parser.add_argument("--max-rpc-requests", type=int, default=256)
    v3_parser.add_argument("--max-bitmap-words", type=int, default=16)
    v3_parser.add_argument("--max-initialized-ticks", type=int, default=128)
    v3_parser.add_argument("--max-steps", type=int, default=512)
    v3_parser.add_argument("--min-request-interval", type=float, default=0.55)
    v3_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    solana_price_parser = sub.add_parser(
        "solana-price",
        help="read-only Jupiter Price API V3 indicative USD prices for up to 50 Solana mints",
    )
    solana_price_parser.add_argument("--mint", action="append", required=True)
    solana_price_parser.add_argument("--timeout-seconds", type=float, default=8.0)
    solana_quote_parser = sub.add_parser(
        "solana-quote",
        help="read-only Jupiter Swap API V2 quote-only request; never builds or submits a transaction",
    )
    solana_quote_parser.add_argument("--input-mint", required=True)
    solana_quote_parser.add_argument("--output-mint", required=True)
    solana_quote_parser.add_argument("--amount-raw", required=True)
    solana_quote_parser.add_argument("--timeout-seconds", type=float, default=8.0)

    cex_universe_parser = sub.add_parser(
        "cex-universe-scan",
        help="bounded read-only pairwise depth comparison across 2-5 CEX Spot venues",
    )
    cex_universe_parser.add_argument("--venue", action="append", choices=SUPPORTED_VENUES, required=True)
    cex_universe_parser.add_argument("--instrument", action="append", required=True)
    cex_universe_parser.add_argument("--base-asset-id", action="append", required=True)
    cex_universe_parser.add_argument("--quote-currency-id", action="append", required=True)
    cex_universe_parser.add_argument(
        "--fee-bps", action="append", default=[], metavar="VENUE=BPS",
        help="optional operator-supplied taker fee per venue; repeat for each known fee",
    )
    cex_universe_parser.add_argument("--base-quantity", required=True)
    cex_universe_parser.add_argument("--latency-cost-bps")
    cex_universe_parser.add_argument("--max-quote-age-seconds", type=int, default=5)
    cex_universe_parser.add_argument("--max-quote-skew-seconds", type=int, default=2)

    cex_parser = sub.add_parser(
        "cex-scan",
        help="read-only two-exchange Spot depth comparison; requires explicit canonical asset IDs",
    )
    cex_parser.add_argument("--venue-a", choices=("bybit", "binance", "kraken", "coinbase", "okx"), required=True)
    cex_parser.add_argument("--instrument-a", required=True, help="exact public Spot instrument ID or Kraken pair alias")
    cex_parser.add_argument("--a-base-asset-id", required=True, help="operator-approved canonical base asset ID for venue A")
    cex_parser.add_argument("--a-quote-currency-id", required=True, help="operator-approved canonical quote currency ID for venue A")
    cex_parser.add_argument("--fee-a-bps", help="operator-supplied venue A taker fee in basis points; omit to keep net unset")
    cex_parser.add_argument("--venue-b", choices=("bybit", "binance", "kraken", "coinbase", "okx"), required=True)
    cex_parser.add_argument("--instrument-b", required=True, help="exact public Spot instrument ID or Kraken pair alias")
    cex_parser.add_argument("--b-base-asset-id", required=True, help="operator-approved canonical base asset ID for venue B")
    cex_parser.add_argument("--b-quote-currency-id", required=True, help="operator-approved canonical quote currency ID for venue B")
    cex_parser.add_argument("--fee-b-bps", help="operator-supplied venue B taker fee in basis points; omit to keep net unset")
    cex_parser.add_argument("--base-quantity", required=True, help="exact base quantity swept through both books")
    cex_parser.add_argument("--latency-cost-bps", help="operator-supplied combined latency-risk cost; omit to keep net unset")
    cex_parser.add_argument("--max-quote-age-seconds", type=int, default=5)
    cex_parser.add_argument("--max-quote-skew-seconds", type=int, default=2)

    flash_parser = sub.add_parser(
        "flash-swap-sim",
        help="read-only V2 flash-swap reserve simulation with optional local paper-ledger capture",
    )
    flash_parser.add_argument("--source-pair", required=True)
    flash_parser.add_argument("--source-venue", help="source-documented V2 venue ID")
    flash_parser.add_argument("--source-fee-bps", type=int, help="required without --source-venue")
    flash_parser.add_argument("--destination-pair", required=True)
    flash_parser.add_argument("--destination-venue", help="source-documented V2 venue ID")
    flash_parser.add_argument("--destination-fee-bps", type=int, help="required without --destination-venue")
    flash_parser.add_argument("--borrow-token", required=True)
    flash_parser.add_argument("--borrow-amount-raw", required=True, type=int)
    flash_parser.add_argument("--rpc-url", default=DEFAULT_ETHEREUM_RPC)
    flash_parser.add_argument("--chain-id", type=int, default=1)
    flash_parser.add_argument("--gas-used", type=int, help="explicit gas-units scenario; include caller-estimated overhead")
    flash_parser.add_argument("--gas-price-wei", type=int, help="explicit gas-price scenario in wei")
    flash_parser.add_argument("--native-token", help="wrapped native token address used for gas valuation")
    flash_parser.add_argument("--native-quote-pair", help="same-block V2 pair for native/repayment-token valuation")
    flash_parser.add_argument("--native-quote-venue", help="source-documented V2 venue for the native quote pair")
    flash_parser.add_argument("--native-quote-fee-bps", type=int, help="required with --native-quote-pair unless a venue is supplied")
    flash_parser.add_argument("--record-paper", action="store_true", help="opt in to local append-only research event recording")
    flash_parser.add_argument("--ledger", default="data/paper-trading.sqlite3")
    observer_parser = sub.add_parser(
        "paper-observe",
        help="record a read-only forward observation at a later canonical block",
        description="Record a read-only forward observation at a later canonical block.",
    )
    observer_parser.add_argument("--candidate-id", required=True)
    observer_parser.add_argument("--rpc-url", default=DEFAULT_ETHEREUM_RPC)
    observer_parser.add_argument("--ledger", default="data/paper-trading.sqlite3")

    onchain_parser = sub.add_parser(
        "onchain-v2-scan",
        help="discover V2 pairs with DEX Screener and quote two verified V2 venues on an EVM chain",
    )
    onchain_parser.add_argument("--token-address", required=True, help="base-token contract address")
    onchain_parser.add_argument("--quote-token-address", required=True, help="explicit quote-token contract address; never inferred from symbols")
    onchain_parser.add_argument("--capital-quote", required=True, help="input amount in quote-token units, e.g. 1000")
    onchain_parser.add_argument("--chain-id", type=int, default=1)
    onchain_parser.add_argument("--chain-slug", help="DEX Screener chain slug; must match the registered venues")
    onchain_parser.add_argument(
        "--venue-ids", nargs=2,
        default=None,
        help="optional explicit pair of registered V2 venue IDs; defaults to the two registered venues for --chain-id",
    )
    onchain_parser.add_argument("--rpc-url", help="read-only RPC URL; required for non-Ethereum EVM chains")
    onchain_parser.add_argument("--max-candidate-pairs", type=int, default=4)
    onchain_parser.add_argument("--max-rpc-requests", type=int, default=32)
    onchain_parser.add_argument(
        "--min-request-interval", type=float, default=0.55,
        help="minimum seconds between EVM RPC requests (default 0.55)",
    )

    token_parser = sub.add_parser(
        "token-check", help="read-only contract-code and ERC-20 metadata preflight at one pinned block"
    )
    token_parser.add_argument("--token-address", required=True)
    token_parser.add_argument("--rpc-url", default=DEFAULT_ETHEREUM_RPC)
    token_parser.add_argument("--chain-id", type=int, default=1)
    fork_parser = sub.add_parser(
        "fork-call", help="simulate supplied contract calldata against an operator-managed local Anvil fork"
    )
    fork_parser.add_argument("--anvil-rpc", default="http://127.0.0.1:8545")
    fork_parser.add_argument("--chain-id", required=True, type=int)
    fork_parser.add_argument("--fork-block", required=True, type=int)
    fork_parser.add_argument("--to", required=True)
    fork_parser.add_argument("--data", required=True, help="exact contract calldata; no transaction is submitted")
    fork_parser.add_argument("--from-address")
    fork_parser.add_argument("--value-wei", type=int, default=0)
    fork_parser.add_argument("--gas-limit", type=int)
    fork_parser.add_argument("--trace", action="store_true", help="request optional callTracer execution trace")
    scan_parser = sub.add_parser("scan", help="read-only indicative DEX price-spread scan")
    scan_parser.add_argument("query", help="token symbol, pair, or address accepted by DEX Screener")
    scan_parser.add_argument("--min-liquidity", default="10000")
    scan_parser.add_argument("--min-volume", default="1000")
    scan_parser.add_argument("--ledger", default="data/opportunities.sqlite3")
    args = parser.parse_args()
    if args.command in (None, "demo"):
        print(json.dumps(demo(), indent=2))
    elif args.command == "report":
        try:
            if not 1 <= args.limit <= 500:
                raise ValueError("limit must be between 1 and 500")
            ledger = OpportunityLedger(args.ledger)
            print(json.dumps({
                "mode": "read_only_observation_summary",
                "observations": ledger.summarize(args.limit),
                "warning": "Repeated indicative spreads are not executable opportunities, a backtest, or realized PnL.",
            }, indent=2))
        except (ValueError, ArithmeticError) as exc:
            parser.exit(2, f"report failed: {exc}\n")
    elif args.command == "venues":
        try:
            print(json.dumps({
                "mode": "read_only_source_documented_venue_registry",
                "chain_id_filter": args.chain_id,
                "live_verified": False,
                "venues": [venue.public_dict() for venue in list_venues(args.chain_id)],
                "warning": (
                    "Registry entries are sourced from protocol documentation/source code, not live-verified. "
                    "v2-sim must verify pair.factory(), factory.getPair(), and router.factory() at the pinned block."
                ),
            }, indent=2))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            parser.exit(2, f"venue registry failed: {exc}\n")
    elif args.command == "venue-check":
        try:
            venues = list_venues(args.chain_id)
            result = verify_registry_venues(
                JsonRpcClient(args.rpc_url, timeout_seconds=args.timeout_seconds),
                venues,
                expected_chain_id=args.chain_id,
            )
            print(json.dumps(result, indent=2))
        except (VenueVerificationError, ValueError, OSError, json.JSONDecodeError) as exc:
            parser.exit(2, f"venue-check failed: {exc}\n")
    elif args.command == "venue-deployment-check":
        try:
            venues = list_venues(args.chain_id)
            result = verify_registry_venue_deployments(
                JsonRpcClient(args.rpc_url, timeout_seconds=args.timeout_seconds),
                venues,
                expected_chain_id=args.chain_id,
            )
            print(json.dumps(result, indent=2))
        except (VenueVerificationError, ValueError, OSError, json.JSONDecodeError) as exc:
            parser.exit(2, f"venue-deployment-check failed: {exc}\n")
    elif args.command == "v2-sim":
        try:
            args.buy_fee_bps, args.buy_factory, args.buy_router, buy_venue = resolve_venue_config(
                args.buy_venue, args.chain_id, args.buy_fee_bps, args.buy_factory, args.buy_router,
            )
            args.sell_fee_bps, args.sell_factory, args.sell_router, sell_venue = resolve_venue_config(
                args.sell_venue, args.chain_id, args.sell_fee_bps, args.sell_factory, args.sell_router,
            )
            if bool(args.buy_router) != bool(args.sell_router):
                raise ValueError("Supply both --buy-router and --sell-router, or neither")
            if bool(args.buy_factory) != bool(args.sell_factory):
                raise ValueError("Supply both --buy-factory and --sell-factory, or neither")
            if args.buy_router and not args.buy_factory:
                raise ValueError("Router validation requires both matching --buy-factory and --sell-factory values")
            reader = EthereumV2PoolReader(args.rpc_url, expected_chain_id=args.chain_id)
            snapshots = reader.read_pools([
                (args.buy_pair, args.buy_fee_bps),
                (args.sell_pair, args.sell_fee_bps),
            ])
            buy_snapshot, sell_snapshot = snapshots
            if args.buy_factory and args.sell_factory:
                reader.verify_v2_pool_factory(
                    buy_snapshot.state.pair_address, args.buy_factory,
                    buy_snapshot.state.token0, buy_snapshot.state.token1,
                    buy_snapshot.state.block_number,
                )
                reader.verify_v2_pool_factory(
                    sell_snapshot.state.pair_address, args.sell_factory,
                    sell_snapshot.state.token0, sell_snapshot.state.token1,
                    sell_snapshot.state.block_number,
                )
            quote_address = args.quote_token.lower()
            base_address = args.base_token.lower()

            def decimals_for(snapshot, token_address):
                if snapshot.state.token0.lower() == token_address:
                    return snapshot.token0_decimals
                if snapshot.state.token1.lower() == token_address:
                    return snapshot.token1_decimals
                raise ValueError("Token address is not part of the supplied pool")

            quote_decimals = decimals_for(buy_snapshot, quote_address)
            base_decimals = decimals_for(buy_snapshot, base_address)
            if decimals_for(sell_snapshot, quote_address) != quote_decimals or decimals_for(sell_snapshot, base_address) != base_decimals:
                raise ValueError("Token decimals disagree between pools")
            if not 0 <= args.slippage_bps <= 5000:
                raise ValueError("slippage-bps must be between 0 and 5000")
            if args.gas_units_assumption is not None and not 21000 <= args.gas_units_assumption <= 30_000_000:
                raise ValueError("gas-units-assumption must be between 21000 and 30000000")
            if not 0 <= args.gas_safety_bps <= 10000:
                raise ValueError("gas-safety-bps must be between 0 and 10000")
            capital = Decimal(args.capital_quote)
            if not capital.is_finite() or capital <= 0:
                raise ValueError("capital-quote must be finite and positive")
            capital_raw_decimal = capital * (10 ** quote_decimals)
            if capital_raw_decimal != capital_raw_decimal.to_integral_value():
                raise ValueError("capital-quote exceeds token precision")
            result = simulate_round_trip(
                buy_snapshot.state, sell_snapshot.state,
                quote_token=quote_address, base_token=base_address,
                capital_quote_raw=int(capital_raw_decimal),
            )
            def human(raw, decimals):
                return str(Decimal(raw) / (Decimal(10) ** decimals))
            output = {
                "mode": "read_only_same_block_v2_reserve_simulation",
                "source": "EVM JSON-RPC eth_call",
                "rpc_endpoint": args.rpc_url,
                "chain_id": buy_snapshot.state.chain_id,
                "block_number": buy_snapshot.state.block_number,
                "block_hash": buy_snapshot.state.block_hash,
                "block_timestamp": buy_snapshot.block_timestamp,
                "buy_reserve_timestamp_last": buy_snapshot.reserve_timestamp_last,
                "buy_seconds_since_last_reserve_update": buy_snapshot.seconds_since_last_reserve_update,
                "sell_reserve_timestamp_last": sell_snapshot.reserve_timestamp_last,
                "sell_seconds_since_last_reserve_update": sell_snapshot.seconds_since_last_reserve_update,
                "buy_pool_fee_bps_assumed": buy_snapshot.state.fee_bps,
                "sell_pool_fee_bps_assumed": sell_snapshot.state.fee_bps,
                "buy_venue_registry_id": buy_venue.id if buy_venue else None,
                "sell_venue_registry_id": sell_venue.id if sell_venue else None,
                "buy_venue_registry_status": buy_venue.source_status if buy_venue else "manual_assumption",
                "sell_venue_registry_status": sell_venue.source_status if sell_venue else "manual_assumption",
                "buy_deployment_source": buy_venue.deployment_source if buy_venue else None,
                "sell_deployment_source": sell_venue.deployment_source if sell_venue else None,
                "buy_fee_source": buy_venue.fee_source if buy_venue else "user_supplied_fee_bps",
                "sell_fee_source": sell_venue.fee_source if sell_venue else "user_supplied_fee_bps",
                "capital_quote": human(result.capital_quote_raw, quote_decimals),
                "bought_base": human(result.buy.amount_out_raw, base_decimals),
                "final_quote": human(result.final_quote_raw, quote_decimals),
                "gross_profit_quote": human(result.gross_profit_quote_raw, quote_decimals),
                "gross_return_bps": str(result.gross_return_bps),
                "buy_price_impact_bps_excluding_fee": str(result.buy.price_impact_bps),
                "sell_price_impact_bps_excluding_fee": str(result.sell.price_impact_bps),
                "classification": result.classification,
                "live_execution": False,
                "factory_verification": "passed" if args.buy_factory and args.sell_factory else "not_requested",
                "warning": "Excludes gas, transfer taxes, dynamic fees, reverts, MEV, and execution. Not paper PnL or a trade recommendation.",
            }
            if args.gas_units_assumption is not None:
                gas_price_wei = reader.read_gas_price_wei()
                gas_price_retrieved_at = datetime.now(timezone.utc).isoformat()
                buffered_gas_units = (
                    args.gas_units_assumption * (10000 + args.gas_safety_bps) + 9999
                ) // 10000
                output["gas_cost_estimate"] = {
                    "gas_units_assumption": args.gas_units_assumption,
                    "safety_buffer_bps": args.gas_safety_bps,
                    "buffered_gas_units": buffered_gas_units,
                    "gas_price_source": "EVM JSON-RPC eth_gasPrice",
                    "gas_price_retrieved_at": gas_price_retrieved_at,
                    "gas_price_wei": str(gas_price_wei),
                    "estimated_native_asset_cost_wei": str(buffered_gas_units * gas_price_wei),
                    "valuation_in_quote_token": None,
                    "warning": "Indicative node gas-price estimate multiplied by a user-supplied gas-units assumption. Not eth_estimateGas, not transaction simulation, and not deducted from gross profit.",
                }
            if args.buy_router and args.sell_router:
                reader.verify_v2_router_factory(
                    args.buy_router, args.buy_factory, result.buy.block_number,
                )
                reader.verify_v2_router_factory(
                    args.sell_router, args.sell_factory, result.sell.block_number,
                )
                buy_amounts = reader.quote_v2_router_exact_input(
                    args.buy_router, result.capital_quote_raw,
                    [quote_address, base_address], result.buy.block_number,
                )
                buy_min_base_out_raw = (
                    buy_amounts[-1] * (10000 - args.slippage_bps) // 10000
                )
                if buy_min_base_out_raw <= 0:
                    raise ValueError("Buy minimum output rounds to zero at the selected slippage tolerance")
                sell_amounts = reader.quote_v2_router_exact_input(
                    args.sell_router, buy_min_base_out_raw,
                    [base_address, quote_address], result.sell.block_number,
                )
                sell_min_quote_out_raw = (
                    sell_amounts[-1] * (10000 - args.slippage_bps) // 10000
                )
                if sell_min_quote_out_raw <= 0:
                    raise ValueError("Sell minimum output rounds to zero at the selected slippage tolerance")
                reader.verify_canonical_block(
                    result.buy.block_number, result.buy.block_hash,
                )
                output["router_validation"] = {
                    "mode": "read_only_router_eth_call",
                    "buy_router": args.buy_router.lower(),
                    "sell_router": args.sell_router.lower(),
                    "buy_base_out_expected": human(buy_amounts[-1], base_decimals),
                    "buy_min_base_out": human(buy_min_base_out_raw, base_decimals),
                    "sell_quote_out_at_buy_min": human(sell_amounts[-1], quote_decimals),
                    "sell_min_quote_out": human(sell_min_quote_out_raw, quote_decimals),
                    "slippage_tolerance_bps_per_leg": args.slippage_bps,
                    "gross_profit_quote": human(sell_amounts[-1] - result.capital_quote_raw, quote_decimals),
                    "buy_router_vs_reserve_math_raw": buy_amounts[-1] - result.buy.amount_out_raw,
                    "sell_router_vs_reserve_math_raw": sell_amounts[-1] - result.sell.amount_out_raw,
                    "chain_id": buy_snapshot.state.chain_id,
                    "block_number": result.buy.block_number,
                    "block_hash": result.buy.block_hash,
                    "warning": "Router getAmountsOut is still a read-only quote, not a full transaction simulation; it does not prove balance, allowance, gas, transfer-tax, or inclusion success.",
                }
            print(json.dumps(output, indent=2))
        except (RpcReadError, ValueError, ArithmeticError) as exc:
            parser.exit(2, f"v2-sim failed: {exc}\n")
    elif args.command == "v3-sim":
        try:
            if args.chain_id != 1 and not args.rpc_url:
                raise ValueError("--rpc-url is required for non-Ethereum networks")
            if not -(1 << 255) <= args.amount_specified < (1 << 255):
                raise ValueError("--amount-specified must fit int256 raw units")
            if not 1 <= args.max_steps <= 2048:
                raise ValueError("--max-steps must be between 1 and 2048")
            rpc_url = args.rpc_url or DEFAULT_V3_ETHEREUM_RPC
            zero_for_one = args.direction == "token0_to_token1"
            reader = EvmV3PoolReader(
                rpc_url,
                expected_chain_id=args.chain_id,
                timeout_seconds=args.timeout_seconds,
                min_request_interval=args.min_request_interval,
                max_rpc_requests=args.max_rpc_requests,
            )
            block_context = None
            if args.limit_ticks is not None:
                if not 1 <= args.limit_ticks <= 887272:
                    raise ValueError("--limit-ticks must be between 1 and 887272")
                block_context, _, current_tick = reader.read_slot0(args.pool)
                limit_tick = current_tick - args.limit_ticks if zero_for_one else current_tick + args.limit_ticks
            else:
                limit_tick = args.limit_tick
            sqrt_price_limit_x96 = get_sqrt_ratio_at_tick(limit_tick)
            snapshot = reader.read_snapshot(
                args.pool,
                zero_for_one=zero_for_one,
                sqrt_price_limit_x96=sqrt_price_limit_x96,
                block_context=block_context,
                expected_factory=args.factory,
                max_bitmap_words=args.max_bitmap_words,
                max_initialized_ticks=args.max_initialized_ticks,
            )
            result = simulate_v3_swap(
                snapshot,
                amount_specified=args.amount_specified,
                zero_for_one=zero_for_one,
                sqrt_price_limit_x96=sqrt_price_limit_x96,
                max_steps=args.max_steps,
            )
            print(json.dumps({
                "mode": "read_only_v3_pinned_snapshot_simulation",
                "status": "complete" if result.complete else "incomplete",
                "classification": result.classification,
                "chain_id": result.chain_id,
                "pool_address": result.pool_address,
                "token0": snapshot.token0,
                "token1": snapshot.token1,
                "fee_pips": snapshot.fee_pips,
                "tick_spacing": snapshot.tick_spacing,
                "block_number": result.block_number,
                "block_hash": result.block_hash,
                "direction": args.direction,
                "current_tick": snapshot.tick,
                "limit_tick": limit_tick,
                "exact_input": result.exact_input,
                "amount_specified_raw": str(result.amount_specified),
                "amount_in_excluding_fee_raw": str(result.amount_in),
                "fee_amount_raw": str(result.fee_amount),
                "gross_amount_in_raw": str(result.amount_in + result.fee_amount),
                "amount_out_raw": str(result.amount_out),
                "amount_remaining_raw": str(result.amount_remaining),
                "final_sqrt_price_x96": str(result.sqrt_price_x96),
                "final_tick": result.tick,
                "final_liquidity": str(result.liquidity),
                "crossed_ticks": list(result.crossed_ticks),
                "steps": result.steps,
                "complete": result.complete,
                "stop_reason": result.stop_reason,
                "rpc_requests_used": reader.rpc_request_count,
                "rpc_requests_budget": args.max_rpc_requests,
                "live_execution": False,
                "transaction_submitted": False,
                "warning": (
                    "Read-only raw-unit simulation over a block-pinned snapshot. "
                    "Not a net-profit estimate: token decimals, transfer taxes, gas, router behavior, "
                    "MEV, inclusion and execution risk are not priced. No transaction is built or sent."
                ),
            }, indent=2))
        except (RpcReadError, ValueError, ArithmeticError, RuntimeError) as exc:
            parser.exit(2, f"v3-sim failed: {exc}\n")
    elif args.command == "flash-swap-sim":
        try:
            result = run_flash_swap_simulation(
                source_pair=args.source_pair,
                source_venue=args.source_venue,
                source_fee_bps=args.source_fee_bps,
                destination_pair=args.destination_pair,
                destination_venue=args.destination_venue,
                destination_fee_bps=args.destination_fee_bps,
                borrowed_token=args.borrow_token,
                amount_borrowed_raw=args.borrow_amount_raw,
                chain_id=args.chain_id,
                rpc_url=args.rpc_url,
                gas_used=args.gas_used,
                gas_price_wei=args.gas_price_wei,
                native_token=args.native_token,
                native_quote_pair=args.native_quote_pair,
                native_quote_venue=args.native_quote_venue,
                native_quote_fee_bps=args.native_quote_fee_bps,
                record_paper=args.record_paper,
                ledger_path=args.ledger,
            )
            print(json.dumps(result, indent=2))
        except (RpcReadError, ValueError, ArithmeticError, OSError, RuntimeError, sqlite3.Error) as exc:
            parser.exit(2, f"flash-swap-sim failed: {exc}\n")
    elif args.command == "solana-price":
        try:
            batch = JupiterPriceV3Client(timeout_seconds=args.timeout_seconds).fetch_prices(args.mint)
            print(json.dumps({
                "mode": "read_only_solana_indicative_price",
                "source": "Jupiter Price API V3",
                "network": "solana",
                "requested_mints": list(batch.requested_mints),
                "missing_mints": list(batch.missing_mints),
                "fetched_at": batch.fetched_at.isoformat(),
                "observations": [{
                    "mint": item.mint,
                    "usd_price": str(item.usd_price),
                    "decimals": item.decimals,
                    "source_block_id": item.block_id,
                    "price_change_24h_pct": str(item.price_change_24h) if item.price_change_24h is not None else None,
                    "reported_liquidity_usd": str(item.liquidity_usd) if item.liquidity_usd is not None else None,
                    "provider_created_at": item.created_at.isoformat() if item.created_at else None,
                    "fetched_at": item.fetched_at.isoformat(),
                    "data_classification": item.data_class,
                } for item in batch.observations],
                "data_classification": "indicative_heuristic_prices_not_executable_quotes",
                "live_execution": False,
                "warning": (
                    "Jupiter Price API V3 is a heuristic mark based on the last qualifying on-chain trade. "
                    "Missing mints have no reliable returned price. source_block_id identifies that trade's "
                    "Solana block, not the current slot; this command does not calculate block age. "
                    "fetched_at is local retrieval time only. This is not an executable price, order book, "
                    "or evidence of profit."
                ),
            }, indent=2))
        except (JupiterQuoteError, ValueError, ArithmeticError) as exc:
            parser.exit(2, f"solana-price failed: {exc}\n")
    elif args.command == "cex-universe-scan":
        try:
            counts = (
                len(args.venue), len(args.instrument),
                len(args.base_asset_id), len(args.quote_currency_id),
            )
            if len(set(counts)) != 1:
                raise CexUniverseScanError(
                    "repeat --venue, --instrument, --base-asset-id and --quote-currency-id equally"
                )
            fee_map = {}
            for entry in args.fee_bps:
                if "=" not in entry:
                    raise CexUniverseScanError("--fee-bps must use VENUE=BPS")
                venue, fee = entry.split("=", 1)
                if venue in fee_map:
                    raise CexUniverseScanError("duplicate fee profile")
                fee_map[venue] = fee
            if set(fee_map) - set(args.venue):
                raise CexUniverseScanError("fee profile supplied for an unselected venue")
            requests = [
                VenueRequest(
                    venue_id=venue,
                    instrument_id=instrument,
                    base_asset_id=base_id,
                    quote_currency_id=quote_id,
                    fee_bps=fee_map.get(venue),
                )
                for venue, instrument, base_id, quote_id in zip(
                    args.venue, args.instrument, args.base_asset_id, args.quote_currency_id
                )
            ]
            result = scan_cex_universe(
                venues=requests,
                base_quantity=args.base_quantity,
                latency_cost_bps=args.latency_cost_bps,
                max_quote_age_seconds=args.max_quote_age_seconds,
                max_quote_skew_seconds=args.max_quote_skew_seconds,
            )
            print(json.dumps(result, indent=2))
        except (CexUniverseScanError, ValueError, RuntimeError, OSError, ArithmeticError) as exc:
            parser.exit(2, f"cex-universe-scan failed: {exc}\\n")
    elif args.command == "cex-scan":
        try:
            print(json.dumps(run_cex_scan(args), indent=2))
        except (ValueError, RuntimeError, OSError, ArithmeticError) as exc:
            parser.exit(2, f"cex-scan failed: {exc}\n")
    elif args.command == "solana-quote":
        try:
            quote = JupiterQuoteOnlyClient(timeout_seconds=args.timeout_seconds).quote(
                input_mint=args.input_mint,
                output_mint=args.output_mint,
                amount_raw=args.amount_raw,
            )
            print(json.dumps({
                "mode": "read_only_solana_router_quote",
                "source": "Jupiter Swap API V2 /order",
                "network": "solana",
                "input_mint": quote.input_mint,
                "output_mint": quote.output_mint,
                "input_amount_raw": str(quote.input_amount_raw),
                "output_amount_raw": str(quote.output_amount_raw),
                "other_amount_threshold_raw": (
                    str(quote.other_amount_threshold_raw)
                    if quote.other_amount_threshold_raw is not None else None
                ),
                "fee_bps": quote.fee_bps,
                "fee_mint": quote.fee_mint,
                "price_impact_pct": str(quote.price_impact_pct) if quote.price_impact_pct is not None else None,
                "router": quote.router,
                "route_plan": list(quote.route_plan),
                "request_id": quote.request_id,
                "context_slot": quote.context_slot,
                "fetched_at": quote.fetched_at.isoformat(),
                "transaction_present": quote.transaction_present,
                "live_execution": False,
                "warning": (
                    "Quote-only request without a taker; the API response must explicitly contain transaction=null. "
                    "No transaction is built, signed, or submitted. A router quote is not a guarantee of execution, "
                    "inclusion, realized PnL, or profit after fees and network costs."
                ),
            }, indent=2))
        except (JupiterQuoteError, ValueError, ArithmeticError) as exc:
            parser.exit(2, f"solana-quote failed: {exc}\n")
    elif args.command == "paper-observe":
        try:
            result = observe_flash_swap_outcome(
                args.candidate_id,
                ledger_path=args.ledger,
                rpc_url=args.rpc_url,
            )
            print(json.dumps(result, indent=2))
        except (RpcReadError, ValueError, ArithmeticError, OSError, RuntimeError, sqlite3.Error) as exc:
            parser.exit(2, f"paper-observe failed: {exc}\n")
    elif args.command == "onchain-v2-scan":
        try:
            result = scan_onchain_v2_candidates(
                token_address=args.token_address,
                quote_token_address=args.quote_token_address,
                capital_quote=args.capital_quote,
                rpc_url=args.rpc_url,
                chain_id=args.chain_id,
                chain_slug=args.chain_slug,
                venue_ids=tuple(args.venue_ids) if args.venue_ids else None,
                max_candidate_pairs=args.max_candidate_pairs,
                max_rpc_requests=args.max_rpc_requests,
                min_request_interval=args.min_request_interval,
            )
            print(json.dumps(result, indent=2))
        except (MarketDataError, RpcReadError, ValueError, ArithmeticError, RuntimeError) as exc:
            parser.exit(2, f"onchain-v2-scan failed: {exc}\n")
    elif args.command == "token-check":
        try:
            reader = EthereumV2PoolReader(args.rpc_url, expected_chain_id=args.chain_id)
            result = reader.read_token_preflight(args.token_address)
            print(json.dumps({
                "mode": "read_only_token_contract_preflight",
                "chain_id": result.chain_id,
                "block_number": result.block_number,
                "block_hash": result.block_hash,
                "token_address": result.token_address,
                "contract_code_present": True,
                "runtime_bytecode_size": result.runtime_bytecode_size,
                "decimals": result.decimals,
                "symbol": result.symbol,
                "name": result.name,
                "metadata_status": result.metadata_status,
                "classification": "metadata_preflight_only_not_token_safety_verification",
                "transfer_tax_or_blacklist_checks": "not_performed",
                "live_execution": False,
                "warning": "ERC-20 metadata methods are optional. Code and metadata do not prove transfer semantics, proxy implementation safety, absence of taxes, blacklist behavior, or honeypot resistance.",
            }, indent=2))
        except (RpcReadError, ValueError, ArithmeticError) as exc:
            parser.exit(2, f"token-check failed: {exc}\n")
    elif args.command == "fork-call":
        try:
            result = AnvilForkSimulator(args.anvil_rpc).simulate_call(
                expected_chain_id=args.chain_id,
                fork_block=args.fork_block,
                to=args.to,
                data=args.data,
                from_address=args.from_address,
                value_wei=args.value_wei,
                gas_limit=args.gas_limit,
                include_trace=args.trace,
            )
            print(json.dumps({
                "mode": "read_only_local_anvil_fork_call_simulation",
                "chain_id": result.chain_id,
                "fork_block": result.fork_block,
                "block_hash": result.block_hash,
                "anvil_client_version": result.client_version,
                "call_output": result.call_output,
                "estimated_gas": result.estimated_gas,
                "trace": result.trace,
                "classification": result.classification,
                "transaction_submitted": False,
                "wallet_or_signer_used": False,
                "warning": "Exact calldata is simulated with eth_call and eth_estimateGas on a pinned local fork. This is a single-call simulation, not a multi-DEX atomic arbitrage proof or a guarantee of inclusion.",
            }, indent=2))
        except (ForkSimulationError, ValueError, ArithmeticError) as exc:
            parser.exit(2, f"fork-call failed: {exc}\n")
    elif args.command == "scan":
        try:
            result = scan(args.query, Decimal(args.min_liquidity), Decimal(args.min_volume), args.ledger)
        except (MarketDataError, ValueError) as exc:
            parser.exit(2, f"scan failed: {exc}\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
