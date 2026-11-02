# Empirical evidence: DEX arbitrage, MEV competition, and execution costs

**Research date:** 2026-11-02  
**Purpose:** Translate peer-reviewed and preprint empirical findings into measurable requirements for the Telegram Arbitrage Engine.  
**Important:** Historical extracted value, transaction volume, and study-specific profitability are not forecasts for this engine.

## Executive conclusions

1. DEX arbitrage is a real, measurable market, but competition and execution access concentrate returns among a small number of sophisticated searchers.
2. Gross price gaps are not enough. Gas bidding, builder relationships, inventory risk, price impact, adversarial reordering, and inclusion probability determine realized outcomes.
3. Flash swaps remove the need to pre-fund the borrowed asset for one atomic transaction; they do not remove execution costs or competitive bidding.
4. Cross-chain arbitrage is not a low-capital extension by default. Empirical evidence shows that pre-positioned inventory dominates bridge-based execution because bridge latency is material.
5. The engine should prove net outcomes through block-pinned backtesting and paper/shadow execution before it considers capital deployment.

## Selected empirical studies

### 1. CEX-DEX extracted value and searcher concentration (2025)

**Paper:** Fei Wu, Danning Sui, Thomas Thiery, Mallesh Pai, *Measuring CEX-DEX Extracted Value and Searcher Profitability: The Darkest of the MEV Dark Forest* (arXiv:2507.13023).

- Dataset covers 19 months, August 2023 to March 2025.
- Identifies 7,203,560 CEX-DEX arbitrages and estimates USD 233.8 million of extracted value across 19 major searchers.
- Three searchers capture roughly three-quarters of identified volume and extracted value.
- Profitability is strongly connected to searcher-builder integration and the share of revenue paid to builders.
- The paper reports that some integrated searchers retain only 10–15% of arbitrage revenue at searcher level while their integrated builder captures part of the economics.
- The paper's PnL is estimated from on-chain evidence and execution-horizon assumptions; it is not a complete view of private rebates, off-chain hedges, or every searcher's costs.

**Implication:** Do not assume that a positive on-chain gross surplus is the searcher's net profit. The engine needs a builder/inclusion-cost model and must record realized versus estimated outcomes. Private orderflow integration may be a structural advantage that a small operator cannot replicate.

Source: https://arxiv.org/abs/2507.13023

### 2. Gas-fee competition and inventory risk (2026)

**Paper:** Xue Dong He, Chen Yang, Yutian Zhou, *Arbitrage on Decentralized Exchanges* (arXiv:2507.08302; updated September 2026).

- Develops an equilibrium model of gas-fee competition between arbitrageurs.
- Studies no-revert, auto-revert, and selectable-revert transaction settings.
- Uses Binance and Uniswap V2 data and finds evidence of positive inventory risk aversion.
- Priority gas fees rise with price discrepancies and liquidity; trade size also responds to price discrepancies and gas fees.
- The preferred transaction/reversion behavior depends on inventory-risk assumptions and whether the objective is searcher profit or market efficiency.

**Implication:** Gas price cannot be a fixed constant in the model. Paper trading must log the gas scenario, opportunity size, liquidity, and outcome. A failed/reverted attempt is an economic outcome, not an ignorable technical exception.

Source: https://arxiv.org/abs/2507.08302

### 3. Cyclic DEX arbitrage on Uniswap V2 (2021)

**Paper:** Ye Wang et al., *Cyclic Arbitrage in Decentralized Exchanges* (arXiv:2105.02784).

- Analyzes Uniswap V2 transaction data over eleven months.
- Identifies 292,606 executed cyclic arbitrages and reports more than USD 138 million in revenue during the historical sample.
- Develops a theoretical framework for profitability conditions and optimal trade sizing.
- Discusses atomic smart-contract execution and price impact.

**Implication:** Multi-hop/cyclic search is a legitimate future strategy family, but this is a historical 2021 sample and must not be used as a current yield benchmark. Add graph-based route search only after V2 cross-venue correctness and data quality are established.

Source: https://arxiv.org/abs/2105.02784

### 4. Cross-chain arbitrage and inventory versus bridges (2025)

**Paper:** Burak Öz et al., *Cross-Chain Arbitrage: The Next Frontier of MEV in Decentralized Finance* (arXiv:2501.17335).

- Measures one year of activity (September 2023 to August 2024) across nine chains.
- Identifies 242,535 executed arbitrages with USD 868.64 million in total arbitrage volume, not profit.
- 66.96% of trades use pre-positioned inventory rather than transferring assets through bridges.
- The paper reports approximately 9 seconds for inventory-based execution versus 242 seconds for bridge-based arbitrage.
- The five largest addresses account for more than half of trades, with one address near 40% of daily volume post-Dencun.

**Implication:** Do not present cross-chain arbitrage as a simple extension of the current flash-swap engine. It requires pre-positioned inventory, multi-chain state and finality tracking, bridge/settlement risk, and much more complex capital management. Keep it out of the first live strategy.

Source: https://arxiv.org/abs/2501.17335

### 5. Slippage, gas, and adversarial reordering (2023)

**Paper:** Austin Adams et al., *Don't Let MEV Slip: The Costs of Swapping on the Uniswap Protocol* (arXiv:2309.13648).

- Measures quoted versus realized execution for Uniswap V3 pools.
- Separates benign, liquidity, collision, and adversarial/reordering slippage.
- Finds gas costs dominate for small swaps, while price impact and slippage become more important for larger trades.
- Finds adversarial slippage varies materially by token and liquidity; large swaps are more exposed.
- A transaction placed near the top of a block can still experience non-zero slippage because state may have changed since the quote.

**Implication:** The engine must record quote age, trade size, liquidity, expected price impact, and realized output. A single flat slippage buffer is not enough. Backtests should model quote-to-inclusion delay and adverse state changes.

Source: https://arxiv.org/abs/2309.13648

## What these findings mean for our roadmap

| Priority | Required capability | Evidence-driven reason |
|---|---|---|
| P0 | Separate gross surplus, gas, builder/priority cost, and net result | Searcher-builder economics and gas auctions materially affect retained profit |
| P0 | Record failed/reverted/missed opportunities | Execution competition and reversion behavior affect expected returns |
| P0 | Block-pinned state and quote-age tracking | A quote can be stale by one or more blocks before execution |
| P1 | Trade-size optimizer with liquidity/impact constraints | Large orders change pool prices and can lose more to price impact/slippage |
| P1 | Paper/shadow observer with inclusion and outcome records | Estimated profit must be compared with actual later state and inclusion |
| P1 | Searcher competition metrics | Candidate count alone does not show the probability of winning an opportunity |
| P2 | Cyclic/multi-hop route graph | Historical work supports this strategy family, but it increases path and state complexity |
| P3 | Cross-chain strategy | Requires pre-positioned inventory or bridge execution and separate finality/settlement risk |
| P4 | Builder/relay integration | Relevant only after paper evidence and a separate operator-approved execution gate |

## Concrete measurement changes

Every candidate should retain:
- chain ID, block number, block hash, transaction/log index when available;
- token contract addresses, decimals, pair addresses, venue, fee source, and pool reserves;
- exact candidate detection time, snapshot age, simulation start/end, and route-call gas;
- gross surplus in raw units, gas used, gas-price scenario, estimated native gas cost, quote conversion pool and block, and net scenario result;
- rejection reason, stale-state reason, and whether the candidate was ever executable in the later canonical state;
- paper/shadow inclusion outcome, realized output, gas used, revert status, and difference from simulation.

The ledger must retain failed and rejected candidates. Reporting only profitable-looking candidates would create selection bias.

## Strategy choices

### Continue now
- Same-chain Ethereum V2 cross-venue flash-swap research.
- Integer reserve math and bounded borrow-size search.
- Pinned-fork callback execution/revert tests.
- Gas-cost scenarios and explicit break-even calculations.
- Append-only paper-trading event records.

### Defer
- Cross-chain arbitrage and bridges.
- CEX-DEX inventory hedging.
- Multi-chain capital allocation.
- Builder-specific exclusive orderflow arrangements.
- Any live bundle submission or transaction signing.

### Explicit non-goals
- Do not extrapolate the studies' historic dollar figures to our engine.
- Do not treat total arbitrage volume as profit.
- Do not interpret flash loans or flash swaps as free capital or guaranteed execution.
- Do not claim profitability without out-of-sample, fee-inclusive, execution-aware evidence.

## Methodology and limitations

These studies use different markets, periods, chains, and definitions: CEX-DEX, DEX-DEX cyclic, and cross-chain arbitrage are not interchangeable. The 2021 Uniswap V2 study is historical; the 2023 Uniswap V3 slippage study concerns swap costs, not arbitrage bot returns; and CEX-DEX estimates include assumptions about off-chain hedging. Their purpose here is to identify risk factors and measurement requirements, not to predict our system's return.
