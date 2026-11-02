# Cross-venue V2 flash-swap reserve model

## Scope

This module adds a deterministic, integer-only model of one possible atomic route:

1. Request one token from a source V2 pair using its flash-swap callback mechanism.
2. Sell the borrowed token through a distinct destination V2 pair.
3. Repay the source pair in the other asset, including the source pair's configured fee.
4. Report the raw-unit surplus or deficit.

The model reuses the existing `V2PoolState` validation and `quote_exact_input` implementation. It requires both pool snapshots to refer to the same chain and canonical block, and rejects the same pair address or mismatched assets.

## Repayment math

For a source pair with borrowed-side reserve \(R_b\), repayment-side reserve \(R_r\), borrowed amount \(A\), and fee \(f\) basis points, the minimum repayment input is:

`repayment = floor(R_r * A * 10000 / ((R_b - A) * (10000 - f))) + 1`

The final `+1` preserves the integer rounding convention used by Uniswap V2's `getAmountIn`. The destination sale is calculated using the existing exact-input V2 constant-product formula and its own explicit fee.

## Deliberate exclusions

This is **not** an executable arbitrage strategy or a paper-trading result. It does not implement Solidity callbacks, router calldata, pair state mutation, flash-swap execution, Anvil state-diff/revert simulation, gas valuation, token transfer-tax/rebase/blacklist behavior, MEV, builder inclusion, or profitability guarantees. It must not be used to authorize a transaction. Live execution and signing remain absent.

## Next validation gate

Compare this pure model against a local Anvil fork using a dedicated test harness that executes the exact atomic callback route with pinned contracts, funded test balances, and complete state-diff/revert inspection. Keep all fork interactions local/read-only unless a separately reviewed test contract and local state mutation are explicitly part of the test harness. Never broadcast a transaction.
