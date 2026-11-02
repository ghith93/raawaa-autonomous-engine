# V3 multi-tick simulation over pinned caller-supplied state

This increment connects the V3 math primitives into a bounded multi-range simulator. It consumes an explicit `V3PoolSnapshot` containing:

- Chain, pool/token identities, fee tier, tick spacing and slot0-like current price/tick/liquidity.
- Block number and block hash.
- Bitmap words keyed by signed int16 word position.
- Initialized tick records keyed by raw tick index with signed int128 liquidityNet.

The simulator applies exact-input or exact-output steps, crosses initialized ticks in the correct direction, updates active liquidity, supports zero-liquidity gaps, and stops at an explicit price limit or amount completion. It fails closed if any bitmap word or initialized tick record needed for the simulated path is absent. A bounded step limit returns `complete=False`, never a silently complete quote.

## Trust boundary

The snapshot is caller-supplied. This module does not make RPC calls, verify pool bytecode/factory identity, prove that the data was fetched from the stated block hash, or guarantee that all supplied fields were read atomically at one canonical block. The returned classification is explicitly `caller_supplied_snapshot_math_only_not_rpc_verified`.

Do not treat the result as a live quote, executable route, net-profit estimate, or paper-trading observation until a read-only state adapter has fetched slot0, liquidity, tickSpacing, fee, bitmap words and initialized tick records at the same pinned block and verified the block hash and pool deployment. The next gate is official v3-core parity fixtures, then a source-backed RPC adapter with bounded calls and same-block provenance.

## References

- [Uniswap v3-core SwapMath.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/SwapMath.sol)
- [Uniswap v3-core TickBitmap.sol](https://github.com/Uniswap/v3-core/blob/main/contracts/libraries/TickBitmap.sol)
- [Uniswap V3 pool data guide](https://developers.uniswap.org/docs/sdks/v3/guides/pool-data)
- [Uniswap V3 whitepaper: initialized tick bitmap and crossing](https://app.uniswap.org/whitepaper-v3.pdf)
