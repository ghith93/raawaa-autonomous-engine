# V3 same-block RPC snapshot reader

## Scope

`EvmV3PoolReader` is a read-only HTTPS JSON-RPC adapter for Uniswap V3-style pools. It reuses the existing bounded EVM transport, request budget, pinned block context and canonical-block recheck.

For a requested direction and sqrt-price limit, it:

1. Captures one fresh chain/block context.
2. Requires pool and factory runtime bytecode at that block.
3. Reads pool.factory(), token0(), token1(), fee(), tickSpacing(), slot0() and liquidity().
4. Verifies pool.factory() against the expected factory.
5. Verifies factory.getPool(token0, token1, fee) returns the exact pool.
6. Verifies factory.feeAmountTickSpacing(fee) matches the pool's tickSpacing.
7. Fetches only the bitmap words on the requested price-limit path, within a caller-configured word bound.
8. Reads liquidityGross/liquidityNet/initialized for every set bit, within a caller-configured initialized-tick bound.
9. Rechecks the canonical block hash before returning the snapshot.

The default factory is the official Uniswap V3 Ethereum mainnet factory. Other chains require an explicit factory address; do not reuse Ethereum addresses on other networks.

## Important limits

- RPC calls are individual bounded `eth_call` requests, not a Multicall batch. The request budget and per-request delay are explicit.
- The reader verifies factory registration and the factory fee-tier spacing but does not verify pool bytecode against a pinned code hash or prove the expected factory address is trusted when supplied for another chain.
- A snapshot is bounded by max bitmap words and max initialized ticks. If the path exceeds either bound, the reader fails closed.
- The snapshot is pinned to one block number and its hash is checked before and after reads. It does not use EIP-1898 block-hash call parameters; reorgs detected by the final hash check abort the read.
- This is still a read-only snapshot plus local simulator, not a net-profit calculation. Token decimals, transfer taxes, gas, router calldata, liquidity changes between blocks, MEV and transaction inclusion are outside this scope.
- No transaction, signing, wallet, or live trading method exists in this adapter.

## Usage outline

Construct `EvmV3PoolReader` with an HTTPS RPC endpoint and a bounded `max_rpc_requests`. Call `read_snapshot` with the pool address, direction, sqrt-price limit, and (for non-Ethereum networks) the explicitly trusted factory. Pass the returned snapshot to `simulate_v3_swap`. Treat the result as a read-only, block-pinned simulation, not an executable trade.

## Sources

- [Official Uniswap V3 Ethereum deployment addresses](https://developers.uniswap.org/docs/protocols/v3/deployments/v3-ethereum-deployments) — Ethereum factory: `0x1F98431c8aD98523631AE4a59f267346ea31F984`.
- [Official Uniswap V3 pool data guide](https://developers.uniswap.org/docs/sdks/v3/guides/pool-data) — slot0, liquidity, bitmap words and initialized tick data retrieval.
- [Uniswap V3 factory interface](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/IUniswapV3Factory.sol) — getPool and feeAmountTickSpacing.
- [Uniswap V3 pool immutables](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolImmutables.sol).
- [Uniswap V3 pool state interface](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolState.sol).
