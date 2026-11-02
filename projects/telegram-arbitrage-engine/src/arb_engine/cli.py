from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal

from .dexscreener import DexScreenerClient, MarketDataError
from .ledger import OpportunityLedger
from .models import MarketQuote, Opportunity
from .risk import validate_opportunity
from .scanner import find_spreads


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
    elif args.command == "scan":
        try:
            result = scan(args.query, Decimal(args.min_liquidity), Decimal(args.min_volume), args.ledger)
        except (MarketDataError, ValueError) as exc:
            parser.exit(2, f"scan failed: {exc}\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
