# Telegram Arbitrage Engine: ecosystem and development review

**Research date:** 2026-11-02  
**Repository:** `ghith93/raawaa-autonomous-engine`  
**Scope:** simulation-first EVM DEX arbitrage, V2 flash swaps, future multi-protocol search, and safe path to evidence-based paper trading.

## Executive decision

Do not replace the current engine or import a complete live bot. Keep the existing Python module as the control, accounting, data-provenance, and policy layer; keep Foundry/Anvil as the executable contract oracle; evaluate a Rust EVM/protocol core only behind an isolated adapter after the current route model is validated.

The highest-value next investment is **data quality + route simulation + paper-trading evidence**, not live transaction submission or adding more agents. A bot that discovers stale or non-executable price differences will not become profitable by running faster.

No source code from the projects below is copied into this repository by this research note. Reuse requires reviewing the exact upstream commit, license, dependencies, supported chains, token behavior, and security posture.

## Current local baseline

The repository already has:
- Read-only market discovery and Telegram read-only controls.
- SQLite observations and update-id receipts/checkpointing.
- Read-only EVM JSON-RPC pool reads pinned to a canonical block.
- V2 reserve math and a source-documented Ethereum V2 venue registry.
- A local Anvil call adapter restricted to loopback endpoints.
- A Foundry test-only callback harness and a pinned Ethereum fork smoke.
- Explicit policy: no wallet, signer, approvals, swaps, orders, transaction submission, or live trading.

The pinned-fork smoke validates real Uniswap V2 and SushiSwap V2 pair relationships and tests callback success-or-revert on an ephemeral local fork. It does not establish net profitability, realistic inclusion probability, or repeatable opportunities.

## Open-source project evaluation

| Project | What it offers | Fit for this engine | Decision |
|---|---|---|---|
| [BowTiedDevil/degenbot](https://github.com/BowTiedDevil/degenbot) | Rust MEV core with Python bindings; V2/V3/V4, Curve, Solidly, Balancer, Aave; event decoding, solvers, pool state, swap encoding, and an in-process REVM simulator. MIT license stated upstream. | Best broad reference for a mature multi-protocol architecture and a future performance sidecar. Upstream currently requires Python 3.12+ and a recent Rust toolchain; this project currently targets Python 3.11+. It also includes signing/submission paths that must not be enabled or inherited. | **Study and benchmark in an isolated optional CI job.** Do not add as a runtime dependency yet; first verify release maturity, exact license file, supported chain/venue versions, build reproducibility, and API stability. |
| [bluealloy/revm](https://github.com/bluealloy/revm) | Rust EVM implementation capable of executing transactions against configured block/state context and supporting inspectors/traces. MIT license stated upstream. | Strong future simulation engine once we can construct deterministic state and exact transaction input. It does not discover pools, supply canonical state, or solve arbitrage by itself. Integration adds Rust/API/version complexity. | **Defer.** First prove the route against Anvil and produce stable transaction inputs/state fixtures. Then compare REVM outputs against Anvil as an optional differential test. |
| [foundry-rs/foundry](https://github.com/foundry-rs/foundry) | Forge testing/building and Anvil local fork node. | Already used by the project and is the right contract-level oracle for callback, state rollback, and pinned-fork tests. | **Continue using.** Capture isolated route-call gas, but do not mislabel it as total transaction gas. Add gas snapshots only after a stable deterministic fixture exists. |
| [Flashbots searcher documentation](https://docs.flashbots.net/flashbots-auction/quick-start) and [RPC endpoints](https://docs.flashbots.net/flashbots-auction/advanced/rpc-endpoint) | Searcher bundles and `eth_callBundle` simulation against a target block; private submission path and builder connectivity. | Relevant only after a strategy has passed backtesting and paper/shadow execution. The searcher path requires signed transactions and an authentication identity. | **Research now; integrate only after a separately approved execution milestone.** First implement offline bundle construction fixtures and simulation schema without keys or network submission. |
| [Flashbots MEV-Share](https://docs.flashbots.net/flashbots-mev-share/introduction) | Selectively shared user transaction hints and backrun search; docs currently state nodes accept backruns only. | Potential future backrun-specific strategy, not a general replacement for DEX price scanning. | **Defer** until a dedicated backrun strategy and user-protective policy exist. |
| [Flashbots MEV-Boost](https://github.com/flashbots/mev-boost) | Validator-side middleware for a competitive block-builder marketplace. | Useful to understand the block-building supply chain, but it is not a searcher SDK or an arbitrage bot. | **Do not integrate as a searcher dependency.** |

### Important protocol facts

- Uniswap V2 flash swaps transfer output before checking repayment and invoke `uniswapV2Call` when callback data is non-empty. Callback implementations must authenticate the pair through the factory and repay enough to satisfy the pair invariant. The canonical docs explain the different-token repayment case and the `getAmountIn` calculation: [Uniswap flash-swap implementation guide](https://developers.uniswap.org/docs/protocols/v2/guides/flash-swaps).
- Flash swaps avoid upfront inventory for the borrowed asset; they do not remove gas, LP fees, slippage, competition, failed-inclusion risk, or the need for a profitable executable route.
- MEV-Boost serves validators/proposers through builders and relays. Searchers use a separate bundle interface. Conflating these roles would lead to an incorrect architecture.

## Proposed target architecture

### 1. Canonical chain-data plane
- Make block-pinned on-chain state authoritative for pool reserves, token decimals, fee configuration, and pair/factory relationships.
- Use DEX Screener only for candidate discovery and broad market context, never as executable pool state.
- Add a read-only log indexer for pair creation and Sync/Swap events with block number, block hash, transaction index, and log index.
- Persist raw observations immutably; handle chain reorganizations by marking orphaned block-derived records and replaying from the last finalized checkpoint.
- Use bounded HTTP JSON-RPC polling initially; add WebSocket heads/logs only after reconnect, backfill, and reorg handling are tested.
- Support multiple public RPC endpoints with per-endpoint health, timeout, rate-limit backoff, and source provenance. Do not silently fall back to a different block height.

### 2. Protocol adapter boundary
Define one typed interface for:
- pool identity and token metadata;
- block-pinned state reads;
- exact-input/exact-output quotes;
- fee model and fee provenance;
- liquidity/price-impact bounds;
- state update/reorg semantics.

Keep V2 as the first fully validated adapter. Add V3 only with tick/liquidity traversal and exact rounding; V4 only after hooks and dynamic fee behavior are modelled. Curve/Balancer/Solidly/Aerodrome/Aave are independent adapters, not aliases for a V2 formula.

### 3. Route search and amount optimization
- Build a directed token/pool graph from verified pools.
- Generate bounded candidate paths and reject repeated pools, incompatible token paths, unsupported tokens, stale states, and insufficient liquidity.
- Optimize trade size using integer math and bounded search; compare against exhaustive/reference cases on fixtures.
- Record why each candidate was rejected (fee, impact, reserve, stale block, token restrictions, gas, route constraints).
- Start with same-chain atomic paths only. Cross-chain spreads must remain informational until bridge/custody/settlement risk is separately modelled.

### 4. Simulation and economics
- Keep pure integer quote math independent from I/O.
- Compare Python results to Foundry/Anvil on deterministic fixtures and real pair bytecode on pinned forks.
- Measure route-call gas separately from transaction intrinsic gas, calldata, deployment, and setup. Build a transaction-level gas estimate only from a complete representative transaction.
- Use explicit EIP-1559 scenarios (base fee, priority fee, and chosen gas limit/overhead). Do not assume a single gas price.
- Convert native gas cost into repayment-token raw units using same-block reserve ratios and conservative rounding; record decimal metadata and price-pool provenance. Do not multiply raw reserve ratios by an extra decimal factor: decimal scales cancel when converting raw units to raw units.
- Add conservative allowances for builder payments, revert probability, inclusion failure, transfer taxes, token-specific behavior, and operational overhead before any net-profit label.
- A positive reserve surplus or positive scenario net result is not realized profit.

### 5. Backtesting and paper/shadow trading
Create append-only event records for:
1. candidate detected;
2. exact block/state snapshot captured;
3. route generated;
4. simulation result and gas scenario;
5. rejection/acceptance reason;
6. hypothetical submission time and target block;
7. later on-chain outcome, if any;
8. counterfactual slippage, gas, and inclusion outcome.

Avoid look-ahead bias: a strategy may only use data available at the candidate timestamp. Measure candidate-to-simulation latency and snapshot age. Paper results must include failed/reverted opportunities and missed inclusions, not only successful candidates.

### 6. Risk, policy, and capital allocation
- Keep live trading disabled by default and enforce this in both configuration and code paths.
- Add hard per-route, per-token, per-day, and global exposure limits before any future execution adapter.
- Add a circuit breaker for stale data, RPC disagreement, reorgs, abnormal gas, repeated simulation failures, and drawdown.
- Require an explicit operator-approved release gate for keys, signing, relay submission, contract deployment, or capital exposure.
- Keep keys outside GitHub files/logs and never send them to research agents or model prompts.
- Allocate capital only after sufficient out-of-sample paper evidence; do not extrapolate historical returns linearly.

### 7. Observability and evaluation
Track per strategy and venue:
- candidate count and rejection reasons;
- quote/state age and RPC disagreement;
- gross surplus, gas scenario, estimated net, and break-even gas price;
- route-call gas vs complete transaction gas estimate;
- simulated success/revert rate;
- expected vs realized execution and inclusion;
- net PnL, drawdown, return on deployed capital, and confidence intervals;
- CPU/RAM, RPC requests, retry rate, and infrastructure cost.

## Ordered implementation plan

| Priority | Work item | Acceptance gate |
|---|---|---|
| P0 | Complete and merge the gas-economics PR only after exact-head CI passes | 18/6 WETH-USDC tests, native repayment, ceiling rounding, block/hash checks, and fork gas event all pass |
| P0 | Differential tests for Python V2 quote/repayment math vs Solidity pair behavior | Property-based and boundary tests across both token directions, fees, reserves, and rounding |
| P1 | Token metadata and fee provenance registry | Metadata pinned to chain/block; unknown/fee-on-transfer tokens are rejected from paper-profit calculations |
| P1 | Append-only opportunity lifecycle and paper-trading ledger | Restart/replay-safe records; every candidate has a complete provenance chain |
| P1 | Candidate-to-simulation latency and snapshot freshness metrics | Stale or reorged snapshots cannot be promoted to valid candidates |
| P2 | Read-only block/log indexer and reorg recovery | Backfill and restart tests; deterministic replay from saved logs |
| P2 | Bounded route graph and amount optimizer | Cross-check against exhaustive fixtures; bounded CPU/RPC budget |
| P2 | Historical backtest harness | No look-ahead; include failed, missed, and reverted opportunities |
| P3 | Isolated Degenbot/REVM comparison | Optional CI benchmark; no signing or submission code invoked; parity against Anvil fixtures |
| P3 | Flashbots bundle simulation design | Offline signed-transaction fixtures only; no key or relay submission |
| P4 | Limited paper/shadow operation | Pre-registered success thresholds over a meaningful observation window; no capital exposed |
| P5 | Real execution review | Separate security, legal, operational, key-management, and explicit operator approval gate |

## Immediate recommendations

1. Finish PR #159; do not merge until Python tests, lint/compile, Solidity mock tests, and pinned-fork workflow pass on its exact head.
2. Add differential/property-based V2 tests before broadening protocols.
3. Implement the append-only paper-trading lifecycle and snapshot freshness/reorg tracking next.
4. Treat Degenbot as the leading architecture/benchmark reference, not a drop-in dependency.
5. Keep REVM and Flashbots submission integration behind later gates. Anvil is already the right low-cost validation layer today.
6. Keep the current zero-spend and no-live-trading policy. Public RPC availability is best-effort and does not justify a paid endpoint or upgrade.

## Sources

- [Degenbot repository and architecture](https://github.com/BowTiedDevil/degenbot) — Rust/Python split, protocol coverage, simulator and license statement.
- [REVM repository](https://github.com/bluealloy/revm) — EVM execution and inspection APIs, MIT license statement.
- [Foundry repository](https://github.com/foundry-rs/foundry) — Forge and Anvil local fork/test tooling.
- [Uniswap V2 flash-swap implementation](https://developers.uniswap.org/docs/protocols/v2/guides/flash-swaps) — callback and repayment requirements.
- [Flashbots searcher quick start](https://docs.flashbots.net/flashbots-auction/quick-start) — bundle searcher model and authentication/signing requirements.
- [Flashbots RPC endpoints](https://docs.flashbots.net/flashbots-auction/advanced/rpc-endpoint) — `eth_callBundle` simulation.
- [MEV-Share introduction](https://docs.flashbots.net/flashbots-mev-share/introduction) — selective hints and backrun-only behavior.
- [MEV-Boost repository](https://github.com/flashbots/mev-boost) — validator-side middleware and builder marketplace.

*This is an engineering fit assessment, not investment advice or a profitability forecast. Upstream repositories and APIs evolve; recheck exact versions and licenses before adopting code.*
