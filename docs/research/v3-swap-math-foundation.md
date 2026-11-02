# V3 single-range SwapMath foundation

This module adds a single price-range `compute_swap_step` operation using the preceding exact Q64.96 TickMath and SqrtPriceMath primitives.

It supports:
- Exact-input and exact-output amount modes.
- Both token0-to-token1 and token1-to-token0 directions.
- A hard target price for the current range.
- Fee-pips calculation with the protocol's 1,000,000 denominator.
- Protocol-compatible amount rounding and output capping.

## Hard boundary

This function is **not a complete pool swap quote**. It computes one step between a current price and a caller-supplied target. It does not discover the next initialized tick, read TickBitmap, update active liquidity using signed liquidityNet, or continue across ranges. Calling it once and treating the result as a complete V3 quote would be incorrect whenever the swap crosses an initialized tick.

## Required next implementation

1. Add bitmap word/bit traversal and next initialized tick lookup, respecting tick spacing and negative-tick compression semantics.
2. Read initialized tick liquidityNet at the same pinned block as slot0/liquidity/bitmap.
3. Apply signed liquidity changes on crossing in the correct direction and reject underflow/overflow.
4. Build a bounded multi-range quote simulator and compare it to official Uniswap V3 core harness outputs at fixed blocks.

## References

- [Uniswap v3-core SwapMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SwapMath.sol)
- [Uniswap v3-core SqrtPriceMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SqrtPriceMath.sol)
- [Uniswap v3-core TickBitmap.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TickBitmap.sol)
