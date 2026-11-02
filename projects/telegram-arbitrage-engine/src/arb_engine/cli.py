from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal

from .dexscreener import DexScreenerClient, MarketDataError
from .ledger import OpportunityLedger
from .models import MarketQuote, Opportunity
from .risk import validate_opportunity
from .rpc import DEFAULT_ETHEREUM_RPC, EthereumV2PoolReader, RpcReadError
from .scanner import find_spreads
from .v2 import simulate_round_trip


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


def scan(query: str, min_liquidity: Decimal, min_volume: Decimal, ledger_path: str) -> dict:
    pairs = DexScreenerClient().search_pairs(query)
    spreads = find_spreads(pairs, min_liquidity_usd=min_liquidity,
                           min_volume_24h_usd=min_volume)
    recorded = OpportunityLedger(ledger_path).record(spreads)
    return {
        "mode": "read_only_indicative_scan", "source": "DEX Screener public API",
        "query": query, "pairs_received": len(pairs),
        "cross_dex_comparisons": len(spreads), "observations_recorded": recorded,
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
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulation-only spot arbitrage research engine")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("demo", help="run deterministic synthetic risk demo")
    report_parser = sub.add_parser("report", help="summarize repeated indicative observations from the local ledger")
    report_parser.add_argument("--ledger", default="data/opportunities.sqlite3")
    report_parser.add_argument("--limit", type=int, default=500)
    v2_parser = sub.add_parser("v2-sim", help="read-only same-block Ethereum V2 reserve simulation")
    v2_parser.add_argument("--buy-pair", required=True)
    v2_parser.add_argument("--buy-fee-bps", required=True, type=int)
    v2_parser.add_argument("--sell-pair", required=True)
    v2_parser.add_argument("--sell-fee-bps", required=True, type=int)
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
            parser.exit(2, f"report failed: {exc}\\n")
    elif args.command == "v2-sim":
        try:
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
                "buy_pool_fee_bps_assumed": buy_snapshot.state.fee_bps,
                "sell_pool_fee_bps_assumed": sell_snapshot.state.fee_bps,
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
            parser.exit(2, f"v2-sim failed: {exc}\\n")
    elif args.command == "scan":
        try:
            result = scan(args.query, Decimal(args.min_liquidity), Decimal(args.min_volume), args.ledger)
        except (MarketDataError, ValueError) as exc:
            parser.exit(2, f"scan failed: {exc}\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
