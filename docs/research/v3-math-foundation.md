# V3 math foundation: exact tick conversion

## Scope

This increment adds only two pure integer primitives:

- `get_sqrt_ratio_at_tick`: maps a signed tick to a Q64.96 square-root price with protocol-compatible upward rounding.
- `get_tick_at_sqrt_ratio`: returns the greatest tick whose encoded square-root price does not exceed a supplied ratio.

The implementation is math-only. It does **not** read pool state, bitmap words, initialized ticks, liquidityNet, swap fees, or quote a multi-tick swap. It must not be used as a V3 opportunity scanner or profit estimator yet.

## Why this is separate from V2

V3 liquidity is concentrated in price intervals. As swaps cross initialized ticks, active liquidity changes; a V2 constant-product reserve formula cannot model that. A correct quote path still needs protocol-specific SqrtPriceMath, SwapMath, TickBitmap traversal, signed liquidity changes, fee accounting, exact rounding, and same-block state provenance.

## Validation

The deterministic tests pin the protocol boundary values and representative positive/negative ticks, check the inverse mapping, and reject invalid types/ranges. Before any quote adapter is added, the next gate is exact SqrtPriceMath/SwapMath parity against the official core contracts and Foundry fixtures.

## References

- [Uniswap V3 architecture and math library responsibilities](https://developers.uniswap.org/docs/protocols/v3/concepts/architecture)
- [Uniswap V3 concentrated-liquidity and tick behavior](https://developers.uniswap.org/docs/get-started/concepts/liquidity-providers/concentrated-liquidity)
- [Uniswap V3 TickMath reference implementation](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TickMath.sol)
- [Uniswap V3 whitepaper](https://app.uniswap.org/whitepaper-v3.pdf)

The reference implementation is distributed under GPL-2.0-or-later. This repository adds a separately authored Python implementation of the public fixed-point equations and protocol constants; no Solidity source file is copied into this project. Re-check licensing and attribution requirements before any future direct source reuse.
