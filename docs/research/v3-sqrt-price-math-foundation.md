# V3 SqrtPriceMath foundation

This increment implements a bounded, pure-Python subset of the public Uniswap V3 `SqrtPriceMath` equations:

- Token0 and token1 deltas between Q64.96 square-root prices, including selectable protocol rounding.
- Next square-root price for exact-input and exact-output token amounts in either swap direction.
- Explicit uint128/uint160/uint256 input-domain validation and denominator/liquidity failure handling.

The implementation uses Python arbitrary-precision integers for intermediate products, then validates the protocol output width. The token0 round-up path deliberately preserves the two-stage rounding used by v3-core; collapsing it into a single division can differ by one raw unit.

## Not implemented

This is still not a complete swap quote. It does not calculate fee growth, compute a swap step, find initialized ticks, traverse TickBitmap, apply liquidityNet at tick crossings, read canonical pool state, or estimate gas/profit. Never present these functions as a complete V3 quote.

## Required next gate

Implement and test `SwapMath.computeSwapStep` against official core test vectors. After that, add initialized-tick traversal and exact `liquidityNet` updates. Only then build a read-only V3 pool adapter with same-block state provenance.

## References

- [Uniswap v3-core SqrtPriceMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SqrtPriceMath.sol)
- [Uniswap v3-core SwapMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SwapMath.sol)
- [Uniswap v3-core TickMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TickMath.sol)

These are independent Python equations and protocol-compatible test cases, not copied Solidity source. Review the upstream license and attribution before incorporating any source code verbatim.
