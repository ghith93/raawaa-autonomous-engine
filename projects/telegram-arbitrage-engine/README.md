# Telegram Arbitrage Engine

Simulation-first spot-arbitrage research module, isolated inside the autonomous-engine repository. This import does not alter the engine's existing runtime, task schema, provider routing, or storefront projects.

## Safety
- Read-only public DEX Screener market data.
- Deterministic risk calculations use Decimal; floats are rejected.
- SQLite stores observations only.
- No wallet connection, signing, approvals, swaps, exchange orders, or live trading code.
- DEX Screener USD marks are indicative only, not executable quotes or proof of available depth.
- No AI decision can authorize a trade. Any future AI use is restricted to research and explanation.

## Run in isolation
Requires Python 3.11+.

```bash
cd projects/telegram-arbitrage-engine
python -m pip install -e '.[dev]'
pytest
arb-engine demo
arb-engine scan WETH/USDC --min-liquidity 10000 --min-volume 1000
```

Scan snapshots are saved to `data/opportunities.sqlite3`. Avoid tight polling and respect upstream rate limits.

## Before any paper-profit claim
The next mandatory layer is exact-size router quotes, pool-state/block freshness, route price impact, fee and gas estimation, token transfer-tax/blacklist checks, revert/failure simulation, and chain atomicity analysis. Cross-chain spreads remain informational only and require bridge cost, settlement delay, and custody/bridge risk modelling.

## Current milestone
0.2: deterministic risk core, public read-only DEX scanner, and local SQLite observation ledger. Live market connectivity must be verified in the deployment environment; local CI uses fixtures and does not prove live API availability.
