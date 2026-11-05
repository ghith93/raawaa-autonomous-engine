# Practical viability assessment — Telegram Arbitrage Engine

**Date:** 2026-11-05 · **Scope:** `projects/telegram-arbitrage-engine` at repo main
`03c7b5f` · **Method:** full source/doc review (10,506 LOC Python + Solidity
harness), local test execution, CI/run evidence inspection. Tests were run in a
throwaway venv (`pip install -e '.[dev]'`): **509 passed in 24.16s** — matching
the 509-test CI claim. This assessment is research; it authorizes nothing.

## TL;DR verdict

| Use | Verdict |
|---|---|
| Read-only research, spread discovery, paper/simulation evidence | **Applicable now** — this is what the code is, and it is unusually disciplined about not overclaiming. |
| Live, money-making arbitrage execution | **Not applicable yet** — the entire execution layer (accounts, wallets, order/tx submission, streaming data, capital movement) is deliberately absent, and several evidence gates that must precede it are still open. Going live is additionally an owner-only financial decision (capital at risk, exchange KYC/accounts, fees), outside the USD 0 autonomous envelope. |

## What exists today (verified)

- **Market data (all read-only, keyless/free):** DEX Screener discovery;
  same-block EVM V2 pool reads with factory/router verification at a pinned
  block; Uniswap V3 snapshot + multi-tick simulation with exact integer math;
  Jupiter Solana indicative prices and no-taker quote-only calls; public CEX
  order books/tickers/funding for Binance, Bybit, OKX, Kraken, Coinbase.
- **Evaluation:** `opportunity_costs.evaluate_two_leg_spread` fails closed on
  identity mismatch, stale quotes (>5s default), insufficient depth, or any
  unknown required cost (`incomplete_costs` → no net figure). Cross-venue and
  multi-family comparators add synchronization guards (≤5s quote age, ≤2s
  inter-venue skew) and refuse to compare unsynchronized books.
- **Math integrity:** money is `Decimal` (floats rejected) in the CEX/research
  layer and integer raw units in the EVM layer; profit model charges both legs'
  fees, slippage, gas and a safety margin, with a 35 bps default net threshold.
- **Persistence:** SQLite observation ledger (indicative spreads), an
  append-only hash-chained paper ledger, and a D1 paper-ledger mode — all
  explicitly *not* realized PnL.
- **Interfaces/ops:** 15+ CLI commands, a read-only Telegram controller with
  allowlist/rate-limit/checkpointing, daily CEX public-market smoke (green
  2026-11-03…05), multi-chain venue-deployment smoke, and the engine CI
  (509 tests + live venue relationship checks).
- **Safety posture:** no wallet, signer, approval, order, or transaction code
  exists anywhere. `execution_verified` is always false. This is by design.

## Why it is not live-applicable yet — the five missing layers

1. **Execution layer (entirely absent).** No CEX private APIs, accounts,
   balances, order placement/cancel, no DEX wallet/signer/calldata builder,
   no gas management. A quote is not a fill; nothing can currently act on a
   signal, even manually, through this code.
2. **Streaming market data.** CEX inputs are REST snapshots. Real cross-CEX
   spreads at decision-relevant horizons live and die in well under the
   5s/2s sync guards; snapshot "spreads" at that age are dominated by stale
   artifacts. Live capture needs WebSocket book feeds with local book
   maintenance, plus clock discipline — none of which exists here yet.
3. **Capital topology.** Cross-venue arbitrage requires pre-positioned
   balances on every venue, withdrawal/rebalancing flows (fees, delays,
   chain finality), and inventory risk while one leg is filled and the other
   is not (cross-venue trades are **not atomic**). None is modeled.
   DEX routes can be atomic (flash swaps are simulated, incl. repayment
   invariants), but the composed atomic multi-venue route simulation is
   explicitly unimplemented (README release gates).
4. **Verified account economics.** Venue fees are caller-supplied
   (`--fee-bps`); no fee schedule is fetched or verified, no VIP tier, no
   withdrawal-fee table. At typical public taker fees (~10 bps/leg → ~20 bps
   round trip) plus slippage/latency allowances, only spreads clearing
   roughly ≥25–35 bps net can matter — the engine's 35 bps threshold is
   honest, but whether such spreads *persist after full costs* is unknown
   (next point).
5. **Persistence evidence (the decisive unknown).** The system can *detect*
   and *record* indicative spreads, and the paper observer measures
   persistence — but there is no multi-week dataset showing how often
   `net_positive_estimate` survives full costs, at what notionals, with what
   fill-realistic depth. Without that distribution, any capital decision is
   speculation, however good the math is.

Plus non-code gates: exchange account eligibility/KYC and ToS (incl.
jurisdictional restrictions), tax/record-keeping, MEV/private-relay
engineering for DEX execution, and — controlling here — **owner
authorization for any real capital**, which the USD 0 policy forbids
autonomously.

## Strengths worth keeping

Fail-closed unknowns (never zero-costed), pinned-block and factory-verified
on-chain reads, hash-chained paper provenance, honest labelling everywhere
("indicative", "not net profit"), deterministic fixture tests with no live
calls in CI, and documentation that states its own release gates
(`DATA_SOURCES.md`, `OPPORTUNITY_COST_MODEL.md`).

## Findings (minor)

- **Version drift:** `__init__.py` says 0.14.0, `pyproject.toml` 0.16.0,
  README milestone 0.19. Aligned to 0.19.0 alongside this assessment.
- **Live-venue smoke flakiness:** the engine workflow's push run on
  2026-11-04 failed only in the live WETH-USDC scanner step (exit 2 —
  public-RPC/venue reachability) while all 509 unit tests passed; treat
  live-smoke as connectivity evidence, never as a code gate, and consider
  bounded retries.
- Non-Ethereum venue registry entries remain
  `source_documented_not_live_verified` (fees are sourced assumptions).

## Recommended roadmap (evidence-gated, zero-cost until the last gate)

- **Phase A — decisive paper evidence (do now, $0).** Run the existing
  comparators + paper observer on a bounded schedule (e.g. daily GitHub
  Actions, public data only) for 2–4 weeks; persist every evaluation with
  full cost inputs and classify outcomes. **Gate metric:** frequency,
  notional depth, and half-life of `net_positive_estimate` after *all*
  required costs. If ~zero, the honest conclusion is "no edge at this
  data tier" — cheap to learn now.
- **Phase B — close data gaps ($0).** Source-documented public fee-schedule
  registry (taker bps + withdrawal fees, with citations) replacing manual
  `--fee-bps`; replay harness over saved snapshots; WebSocket book-feed
  prototype for two venues to measure true spread half-life vs REST.
- **Phase C — DEX atomicity proof ($0, fork-only).** Complete the composed
  atomic multi-venue route simulation on local Anvil forks with full
  state-diff/revert analysis (existing single-call adapter + flash-swap
  harness), per the repo's own release gates.
- **Phase D — owner decision pack (STOP; owner-only).** Only if Phases A–C
  show a durable cost-surviving edge: a written proposal covering venue
  accounts/KYC, micro-capital sizing, max-loss kill-switches, and legal/tax
  review. **No autonomous step may cross into Phase D** under the USD 0
  mandate.

## Bottom line

As engineered, this is a credible **research instrument** that can already
answer "do exploitable spreads survive real costs?" — the only question
worth answering before money. It is not, and does not pretend to be, a
trading system. The fastest practical path is Phase A's persistence
evidence; it costs nothing, risks nothing, and either justifies or
retires every later phase.
