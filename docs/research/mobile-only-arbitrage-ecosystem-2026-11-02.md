# Mobile-only arbitrage research

**Research date:** 2026-11-02.  
**Devices:** iPhone + Lenovo TB370FU only; no PC/laptop assumed.  
**Policy:** zero new spend, read-only research/simulation/paper mode; no wallet, signing, approvals, orders, swaps, transaction submission or live trading.

## Decision

Use a **cloud-executed, mobile-operated** design. Phones/tablet are control terminals, not servers. Keep the existing Python engine as policy/accounting/provenance layer, Cloudflare Worker + D1 as remote control/task state, GitHub Actions as bounded batch executor, and Foundry/Anvil as EVM simulation oracle. AndroidHarness stays optional diagnostics only.

The current free architecture is periodic and bounded, not guaranteed 24/7. GitHub scheduled workflows can be delayed or dropped; Cloudflare Workers Free has hard CPU/request/subrequest limits. This supports research and periodic paper observations, not block-level competitive arbitrage. A continuous low-latency process may require paid hosting; no spend is authorized without explicit approval.

## Verified repository baseline

Already implemented: EVM V2 math/venue registry/same-block read-only RPC; multi-chain factory/router deployment smoke; Foundry/Anvil fork tests; flash-swap simulation and gas scenarios; public CEX clients for Binance, Bybit, Coinbase, Kraken and OKX; order-book depth sweep and explicit canonical instrument mapping; funding data; Jupiter Price V3 and Swap V2 quote-only clients; append-only paper ledger and forward-only outcome observer; Python CLI; read-only Telegram controller; Cloudflare Worker/D1 task state and bounded GitHub Actions runner.

PR #226 merged as `1594d19d19621fb61546871979d51a9ecb907dba`. Exact-head SHA `8913e05c9848f01030c53083bc1ee69f09bf15f4` passed Telegram Arbitrage Engine CI, Engine CI and multi-chain deployment smoke for BNB Chain, Arbitrum, Optimism, Base, Polygon and Avalanche. This validates Jupiter CLI exposure and CI, not profit or execution.

## Open-source comparison

| Project | Strongest capability | Decision |
|---|---|---|
| [Degenbot](https://github.com/BowTiedDevil/degenbot) | Rust/Python MEV core, V2/V3/V4, Curve, Balancer, Solidly, Aave, solvers | Benchmark isolated later; no runtime dependency now |
| [REVM](https://github.com/bluealloy/revm) | Rust EVM execution and inspectors | Defer until exact route transaction/state fixtures; compare against Anvil |
| [Foundry/Anvil](https://github.com/foundry-rs/foundry) | Solidity tests, local EVM and pinned forks | Keep and expand; already integrated |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | CEX/DEX connectors, market making, cross-exchange/funding strategies, API/Dashboard/MCP | Study connector contracts and inventory math; do not deploy duplicate stack |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Rust event-driven backtest/live core, replay and multi-venue adapters | Architecture benchmark; defer adoption until measured bottleneck and stable release |
| [Freqtrade](https://github.com/freqtrade/freqtrade) | Backtesting, dry-run, optimization, lookahead analysis | Reuse validation methods, not runtime; candle strategies are not atomic DEX arbitrage |
| [CCXT](https://github.com/ccxt/ccxt) | Unified API for 100+ exchanges | Do not add while five native tested CEX clients exist |
| [Jesse](https://github.com/jesse-ai/jesse) | Research, optimization, charts and paper trading | Reporting/validation reference; no integration |
| [Flashbots](https://docs.flashbots.net/) | Bundle simulation and private orderflow/backrun infrastructure | Research only; signing/submission outside policy |
| [Jupiter](https://developers.jup.ag/) | Solana price marks and route quotes | Keep existing read-only adapter; improve provenance/cost capture |

Reuse ideas, not complete applications. Review exact upstream commit, license, dependencies, maintenance, venue support and execution surface before copying code.

## Strategy ranking

| Strategy | Benefit | Main barriers | Priority |
|---|---|---|---|
| CEX spot cross-venue | Depth and VWAP are measurable | Prefunded inventory, fees, withdrawals, exchange/custody and regional risk | P1 research |
| Perpetual funding / spot basis | Longer-lived than block-level MEV | Margin, liquidation, funding reversals, fees and counterparty risk | P1 research |
| Same-chain DEX cyclic/cross-venue | Atomic route can avoid inventory transfer | Gas, LP fees, state movement, MEV, reverts and inclusion risk | P1 core |
| Solana aggregator routes | Read-only quote access | Quote is not fill; priority fees, slots, RFQ and account costs | P2 |
| CEX-DEX | Detect dislocations | Prefunded inventory/hedging, settlement clocks, gas and custody | P2 |
| Cross-chain/bridges | Apparent spreads | Finality, bridge risk, inventory lockup and settlement | P4 informational |
| Liquidation/backrun/MEV | Specialist markets | Competition, infrastructure, terms/privacy and signing gates | P5 research |

Profit means reconciled realized PnL after fees, spread, slippage, funding, borrow, gas, builder costs, failed attempts, latency, capital lockup and infrastructure. Gross spread, repeated observations or simulated reserve surplus is not profit.

## iPhone + Lenovo operating model

- iPhone: browser, GitHub, status and private Telegram control/alerts.
- Lenovo: browser and occasional AndroidHarness diagnostics.
- Neither device is required to remain awake or online.
- Python execution, tests, scans and replay run in cloud CI or an authorized cloud worker.
- Current Telegram controller is Python long-polling with a local SQLite ledger; it is not a durable cloud phone-control service by itself.
- Next mobile-control feature: Cloudflare Worker Telegram webhook with D1-backed allowlist, update-id receipts, rate limits, bounded commands and audit events. Reuse D1/task patterns; no second queue or arbitrary shell endpoint.
- Next status page: heartbeat, last scan, source freshness, queue depth, candidate/rejection counts, CI health, quota/cost and kill-switch state.
- Continuous low-latency feeds may need paid hosting. No spend without explicit approval and measured break-even.

Cloudflare Workers Free currently documents 100,000 requests/day, 10 ms CPU/request, 128 MB memory and 50 subrequests/request. This is not a general long-running Python scanner. GitHub scheduled workflows are best-effort, not a latency SLA.

## Implementation priorities

**P0 — evidence**
1. Complete atomic two-venue route simulation; compare Python integer math with Solidity/Anvil.
2. Add candidate-time/no-lookahead and out-of-sample walk-forward tests.
3. Persist every candidate, rejection, simulation, failure and later outcome.
4. Record source block/slot/time, receive, detection, quote and simulation timestamps.
5. Include CEX VWAP/depth, fee schedules, inventory and hedge costs before net labels.

**P1 — coverage**
1. Add CEX WebSocket books only with sequence-gap detection, reconnect, snapshot resync and bounded limits.
2. Maintain chain/address or venue/instrument canonical identity; never join by ticker alone.
3. Add V3 only as its own tick/liquidity/rounding model; V4 only after hook/dynamic-fee modelling.
4. Add Jupiter slot/quote provenance and priority-fee cost estimates; keep Solana isolated until comparability is proven.

**P2 — optimization/operations**
1. Bounded route graph and trade-size optimizer with CPU/RPC budgets.
2. Historical replay with data gaps and provenance.
3. Funding/basis simulator with liquidation and adverse-funding scenarios.
4. Benchmark narrow Degenbot/REVM/Nautilus prototypes on identical fixtures.
5. Implement mobile webhook/status dashboard and alerts for stale data, reorg, RPC disagreement, task expiry, provider failure and budget thresholds.

**Later approval gates:** no signing, keys, orders, swaps, bundles, deployment or capital exposure until meaningful out-of-sample paper evidence, complete costs, security/terms review, tested risk limits and explicit operator approval.

## Rejected shortcuts

- Do not install a full trading bot on the phone/tablet or turn AndroidHarness into a scheduler.
- Do not add Hummingbot as a competing stack or CCXT to replace existing native clients.
- Do not adopt Degenbot/Nautilus/REVM before apples-to-apples benchmarks.
- Do not infer returns from marketing, volume, gross spread or backtests alone.
- Do not pay for VPS, RPC, data, API keys, inference or hosting without approval.

## Sources checked 2026-11-02

- https://github.com/BowTiedDevil/degenbot
- https://github.com/bluealloy/revm
- https://github.com/foundry-rs/foundry
- https://github.com/hummingbot/hummingbot
- https://hummingbot.org/docs/
- https://github.com/nautechsystems/nautilus_trader
- https://nautilustrader.io/open-source/
- https://github.com/freqtrade/freqtrade
- https://github.com/freqtrade/freqtrade/blob/develop/docs/lookahead-analysis.md
- https://github.com/ccxt/ccxt
- https://github.com/jesse-ai/jesse
- https://docs.flashbots.net/
- https://developers.jup.ag/
- https://developers.cloudflare.com/workers/platform/limits/
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## Acceptance

- Control/monitor from iPhone or Android browser without local build tools.
- Cloud execution and recovery do not depend on either device.
- Telegram remote commands authenticated, allowlisted, idempotent and D1-backed.
- Data surfaces distinguish indicative marks, quotes, simulations and realized fills.
- Every strategy has cost accounting and no-lookahead tests.
- Exact-head Engine and Telegram CI pass.
- No paid services or live financial actions without explicit approval.
