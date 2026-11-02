# V3 initialized-tick bitmap and liquidity arithmetic

This increment implements the pure local arithmetic needed to find the next initialized tick within one 256-bit bitmap word and apply a tick's signed `liquidityNet` when crossing it.

## Tick bitmap behavior

- Tick indices are compressed by pool tick spacing.
- Negative non-aligned ticks use floor semantics, not truncation toward zero.
- Leftward lookup searches the current bit and lower bits, choosing the most significant set bit.
- Rightward lookup starts at the next compressed tick and searches higher bits, choosing the least significant set bit.
- Empty words return the nearest word boundary with `initialized=False`.
- The result can be outside global TickMath bounds; a pool swap loop must clamp before converting it to a sqrt ratio.

## Liquidity crossing

- Crossing right applies `liquidityNet`.
- Crossing left applies its negation.
- Resulting active liquidity must remain within uint128 and cannot become negative.

## Still not a complete pool quote

The helpers consume one caller-supplied bitmap word and one caller-supplied liquidityNet. They do not fetch bitmap words or tick records, prove that data comes from the same canonical block as slot0/liquidity, iterate over multiple words, or execute a multi-range swap. The next layer is a bounded state provider and a multi-range simulator with deterministic fixtures and parity tests against official v3-core behavior.

## References

- [Uniswap v3-core TickBitmap.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TickBitmap.sol)
- [Uniswap v3-core LiquidityMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/LiquidityMath.sol)
- [Uniswap V3 pool data guide](https://developers.uniswap.org/docs/sdks/v3/guides/pool-data)
