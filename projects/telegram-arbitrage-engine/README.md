# Telegram Arbitrage Engine

Simulation-first spot-arbitrage research module, isolated inside the autonomous-engine repository. This import does not alter the engine's existing runtime, task schema, provider routing, or storefront projects.

## Safety
- Read-only public DEX Screener market data.
- Deterministic risk calculations use Decimal; floats are rejected.
- SQLite stores observations only. A repeated-observation report measures persistence, not historical PnL or executable performance.
- No wallet connection, signing, approvals, swaps, exchange orders, or live trading code.
- DEX Screener USD marks are indicative only, not executable quotes or proof of available depth. Its search response does not provide an on-chain quote timestamp; retrieval age must not be mistaken for pool-state freshness.
- No AI decision can authorize a trade. Any future AI use is restricted to research and explanation.
- A separate V2 constant-product calculator uses integer raw units and requires two distinct pools at the same block; it is reserve math only and excludes gas, transfer taxes, dynamic fees, reverts, and execution.

## Run in isolation
Requires Python 3.11+.

```bash
cd projects/telegram-arbitrage-engine
python -m pip install -e '.[dev]'
pytest
arb-engine demo
arb-engine report --limit 500
arb-engine scan WETH/USDC --min-liquidity 10000 --min-volume 1000
```

Scan snapshots are saved to `data/opportunities.sqlite3`. Avoid tight polling and respect upstream rate limits.

## Before any paper-profit claim
The next mandatory layer is exact-size router quotes, pool-state/block freshness, route price impact, fee and gas estimation, token transfer-tax/blacklist checks, revert/failure simulation, and chain atomicity analysis. Cross-chain spreads remain informational only and require bridge cost, settlement delay, and custody/bridge risk modelling.

## Current milestone
0.3: hardened fail-closed input parsing, explicit retrieval-vs-quote freshness provenance, public read-only DEX scanner, local SQLite observation ledger, repeated-observation summary, and same-block V2 reserve-math simulation. A live RPC pool-state reader and router-level execution simulation are not implemented yet. Live market connectivity must be verified in the deployment environment; local CI uses fixtures and does not prove live API availability.
