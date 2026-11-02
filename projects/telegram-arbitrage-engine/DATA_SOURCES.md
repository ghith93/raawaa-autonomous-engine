# Data sources and market-data trust boundaries

## DEX Screener

- API reference: https://docs.dexscreener.com/api/reference
- Search endpoint: `GET /latest/dex/search?q=...`
- Published endpoint rate limit: 300 requests/minute.
- This client deliberately waits at least one second between sequential requests (at most 60/minute per client instance).
- `priceUsd`, `liquidity.usd`, and `volume.h24` are indicative market metadata.
- The search response includes pair creation time, but does not provide a canonical block number, block hash, or quote-state timestamp for the displayed price.
- The client's `fetched_at` is the local response retrieval time only. It must never be interpreted as proof that the on-chain pool state was fresh at that time.
- API marks are useful for discovery and ranking only. They are not executable swap quotes, guaranteed depth, or evidence of profit.

## Public Ethereum JSON-RPC candidate

- PublicNode endpoint: https://ethereum-rpc.publicnode.com
- This is a candidate for a future read-only Ethereum pool-state adapter. The current release does not yet call this endpoint.
- Public/free availability is not an SLA, and rate limits or service terms may change.
- No key, wallet, signer, transaction submission, or paid access is required by the current code.

## V2 reserve-math simulator

- The simulator consumes explicit pool reserves, fee basis points, token addresses, and matching block number/hash.
- It uses integer raw token units and the constant-product exact-input calculation.
- Fee basis points are explicit per pool; the code does not guess a venue fee.
- The result excludes gas, token decimals conversion, transfer taxes, rebasing/blacklist behavior, dynamic fees, pool-specific hooks, reverts, RPC freshness, and transaction execution.
- Until an RPC reader and those risk checks exist, results are math fixtures—not live paper trades or expected PnL.

## Release gates before paper-trade mode

1. Add read-only RPC state retrieval pinned to one block for both pools.
2. Verify pool ABI/type and fee model; reject unknown pool families.
3. Resolve token decimals and reject fee-on-transfer/rebasing/blacklisted assets where they cannot be safely simulated.
4. Estimate gas and convert it to the quote asset using a fresh, independent price source.
5. Include route-level slippage and minimum-output constraints.
6. Record block hash, retrieval time, data source, and every assumption in each simulation.
7. Replay captured snapshots and compare calculated outputs against router `eth_call` quotes before enabling paper-trade accounting.

No item in this list authorizes live trading.
