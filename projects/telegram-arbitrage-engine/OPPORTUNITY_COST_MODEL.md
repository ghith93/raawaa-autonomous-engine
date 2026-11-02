# Conservative opportunity cost model

**Status:** Implemented as a pure, deterministic evaluator; not yet wired into a live or paper strategy ranking pipeline.

## Why it exists

A visible spread is not profit. A two-leg comparison can be labelled net-positive only after the system has verified the same underlying asset, the same quote currency, fresh observations, full depth on both legs, and all required costs. Unknown costs are never silently treated as zero.

## Inputs and identity gates

Call `arb_engine.opportunity_costs.evaluate_two_leg_spread` with:

- Exact buy/sell prices and quote notional as `Decimal` or decimal strings (never floats).
- An explicit canonical `base_asset_id` for each venue and explicit quote-currency IDs. They must match exactly; symbols such as BTC, ETH, USDT or USDC are not identity keys.
- Market family for each leg: `cex_spot`, `cex_perp`, `evm_amm`, or `solana_aggregator`.
- Timezone-aware local fetch timestamps for both quotes, a maximum quote age (default 5 seconds), and fill fractions.
- An `OpportunityCosts` object with source-backed fee, slippage, network and financing estimates.

## Required cost inputs

| Condition | Required costs |
|---|---|
| Every two-leg comparison | Buy/sell venue fees, buy/sell slippage, latency-risk basis points |
| Any EVM AMM leg | Gas cost and priority fee in quote currency |
| Any Solana aggregator leg | Network fee and priority fee in quote currency |
| Any perpetual leg | Funding cost for the relevant holding horizon |
| Cross-chain route | Bridge cost and settlement/finality cost |
| Borrowed or leveraged capital | Borrow cost |

Known optional costs are included in the total as well. Explicit zero is accepted only when the caller has evidence that the cost is zero. Missing required fields produce `incomplete_costs` and `net_profit_quote=None`.

## Result statuses

- `asset_or_quote_identity_mismatch`: comparison blocked; no gross or net comparison is produced.
- `stale_data`: one or both observations exceed the age bound or are materially future-dated.
- `insufficient_depth`: either leg cannot fill the complete notional.
- `incomplete_costs`: one or more required costs are unknown.
- `net_positive_estimate`: all gates passed and estimated net profit is positive.
- `net_nonpositive`: all gates passed and estimated net profit is zero or negative.

`execution_verified` is always false. Even a `net_positive_estimate` is a research estimate, not a guaranteed fill or realized PnL. This evaluator does not fetch fee schedules, resolve exchange instrument metadata, map assets across networks, calculate route depth, or submit orders. Those source-specific facts must be supplied and independently validated by upstream adapters before it can be integrated into a strategy.

## Deterministic validation

`tests/test_opportunity_costs.py` covers positive/negative spreads, unknown costs, EVM and Solana network costs, funding, bridge/settlement and borrowing costs, asset/quote identity mismatches, stale quotes, partial depth, float rejection and timezone requirements. No network calls are made.
