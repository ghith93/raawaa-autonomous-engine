# Telegram Arbitrage Engine

Simulation-first spot-arbitrage research module, isolated inside the autonomous-engine repository. This import does not alter the engine's existing runtime, task schema, provider routing, or storefront projects.

## Safety
- Read-only public DEX Screener market data.
- Deterministic risk calculations use Decimal; floats are rejected.
- SQLite stores observations only. A repeated-observation report measures persistence, not historical PnL or executable performance.
- No wallet connection, signing, approvals, swaps, exchange orders, or live trading code.
- Optional Telegram bot uses long polling, private-chat allowlisting, a per-chat rate limit, and a persisted update offset. It exposes only status, indicative scans, and read-only observation reports. Configure the token and comma-separated allowed private chat IDs through environment variables; never commit secrets.
- DEX Screener USD marks are indicative only, not executable quotes or proof of available depth. Its search response does not provide an on-chain quote timestamp; retrieval age must not be mistaken for pool-state freshness.
- No AI decision can authorize a trade. Any future AI use is restricted to research and explanation.
- A read-only EVM JSON-RPC adapter retrieves two V2 pool states and token decimals at one pinned block. The V2 constant-product calculator uses integer raw units and requires two distinct pools at the same block; it is reserve math only and excludes gas, transfer taxes, dynamic fees, reverts, MEV, and execution. Pool fee basis points must be supplied explicitly and independently verified.

## Run in isolation
Requires Python 3.11+.

```bash
cd projects/telegram-arbitrage-engine
python -m pip install -e '.[dev]'
pytest
arb-engine demo
arb-engine report --limit 500
arb-engine scan WETH/USDC --min-liquidity 10000 --min-volume 1000
arb-telegram  # optional read-only polling bot; requires TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS
arb-engine v2-sim --buy-pair 0x... --buy-fee-bps 30 --sell-pair 0x... --sell-fee-bps 30 --quote-token 0x... --base-token 0x... --capital-quote 50 --chain-id 1 --slippage-bps 50 --gas-units-assumption 350000 --gas-safety-bps 2000 --buy-router 0x... --sell-router 0x... --buy-factory 0x... --sell-factory 0x...
```

Scan snapshots are saved to `data/opportunities.sqlite3`. Avoid tight polling and respect upstream rate limits.

See [data sources and trust boundaries](DATA_SOURCES.md) for source rate limits, freshness limitations, and the release gates for future pool-state simulation.

## Before any paper-profit claim
The next mandatory layer is verified factory/protocol/fee provenance, pool-state/block freshness, route price impact, gas valuation, token transfer-tax/blacklist checks, full transaction revert simulation, and chain atomicity analysis. Cross-chain spreads remain informational only and require bridge cost, settlement delay, and custody/bridge risk modelling.

## Current milestone
0.11: hardened fail-closed input parsing, explicit retrieval-vs-quote freshness provenance, public read-only DEX scanner, token-address-aware SQLite observation ledger with legacy migration, private-chat Telegram control bot, read-only EVM V2 pool reader pinned to a canonical block with stale-head and reorg checks, integer reserve simulation with fee-separated per-leg price impact, verified pair/router factory bindings, sequential per-leg minimum-output slippage bounds, and optional explicitly assumed native gas budgeting. Gas-to-quote valuation, independently verified fee provenance, token-behavior checks, and full transaction simulation remain unimplemented. Live market connectivity must be verified in the deployment environment; local CI uses fixtures and does not prove live API availability.
