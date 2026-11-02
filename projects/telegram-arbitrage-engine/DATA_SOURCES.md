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

## Public EVM JSON-RPC candidate

- Default PublicNode endpoint: https://ethereum-rpc.publicnode.com (Ethereum mainnet, chain ID 1). The CLI accepts `--chain-id` and a custom HTTPS `--rpc-url` for other EVM networks.
- The current `v2-sim` CLI uses this endpoint for read-only `eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`, and `eth_call` requests. It reads V2 `token0`, `token1`, `getReserves`, and token `decimals`, pins both pools to one block, checks the block timestamp, and re-reads the block hash to detect a reorg during collection. When explicit router addresses are supplied, it also calls V2 `getAmountsOut(uint256,address[])` for each leg at that same block, validates the ABI response, and re-checks the canonical block hash after the router calls.
- Public/free availability is not an SLA, and rate limits or service terms may change.
- No key, wallet, signer, transaction submission, or paid access is required by the current code.

## V2 reserve-math simulator

- The simulator consumes explicit pool reserves, fee basis points, token addresses, and matching block number/hash.
- It uses integer raw token units and the constant-product exact-input calculation.
- Fee basis points are explicit per pool; the code does not guess a venue fee.
- The result excludes gas, token decimals conversion, transfer taxes, rebasing/blacklist behavior, dynamic fees, pool-specific hooks, reverts, RPC freshness, and transaction execution.
- Router comparison is optional and uses `eth_call` only; it is not a full transaction simulation and does not prove balance, allowance, gas sufficiency, token behavior, or transaction inclusion. Results are still research quotes, not paper trades or expected PnL.

## Release gates before paper-trade mode

1. Use the optional factory verification for each pair, then independently verify the protocol's fee model; factory membership alone does not prove the configured fee basis points.
2. Reject fee-on-transfer/rebasing/blacklisted assets where their behavior cannot be safely simulated.
3. Estimate gas and convert it to the quote asset using a fresh, independent price source.
4. Compare reserve math against router `eth_call` exact-input quotes at the same block, after verifying pair and router factory bindings.
5. Include route-level slippage, minimum-output constraints, and revert simulation.
6. Record block hash, retrieval time, data source, and every assumption in each simulation.
7. Replay captured snapshots and test quote stability across blocks before enabling paper-trade accounting.

No item in this list authorizes live trading.


## Telegram Bot API

- Official reference: https://core.telegram.org/bots/api
- The optional controller uses HTTPS `getUpdates` long polling and `sendMessage` replies only.
- Incoming commands are accepted only from explicitly allowlisted private chat IDs where sender ID matches chat ID.
- The bot does not set webhooks, manage chats, publish to channels, place trades, or accept arbitrary command execution.
- The polling offset is stored in the local SQLite state table to avoid replaying already handled updates after a restart.
- One bot polling process should run at a time for a given token and ledger. Telegram long polling and webhooks are mutually exclusive; the controller deliberately does not delete or replace an existing webhook.
- A bot token is a secret. Supply it only as `TELEGRAM_BOT_TOKEN` in the runtime secret environment; set `TELEGRAM_ALLOWED_CHAT_IDS` to the operator's numeric private chat ID(s).


## Router quote reference and integration choice

- Uniswap's official V2 pricing guide documents exact-input `getAmountsOut` and slippage-aware minimum-output handling: https://developers.uniswap.org/docs/protocols/v2/concepts/pricing
- The engine now calls the V2 router's `getAmountsOut(uint256,address[])` via read-only `eth_call`, pinned to the same block used for pool reserves. This is an independent quote cross-check, not a transaction simulation.
- 0x Swap API provides indicative `/price` and firm `/quote` routes, but its current documentation requires an API key for requests. It is therefore not a dependency in the zero-cost baseline: https://docs.0x.org/docs/introduction/quickstart/swap-tokens-with-0x-swap-api
- Uniswap's Aggregator Quote API describes route simulation and gas estimates, but it is an external API dependency and must be evaluated for access, key requirements, terms, and cost before integration: https://developers.uniswap.org/docs/api-reference/aggregator_quote
- A router quote alone does not verify the factory, pair fee, token transfer semantics, allowance, balance, gas sufficiency, or actual transaction execution. Those remain release gates.


## Observation ledger identity

- Cross-DEX comparisons and persisted summaries are keyed by chain plus exact base/quote token contract addresses, not token symbols alone.
- The SQLite migration preserves existing observations and upgrades the legacy symbol-only uniqueness constraint. Older records without contract addresses are retained with empty address fields and are not silently relabelled.


## Price impact and gas-cost assumptions

- The V2 simulator reports per-leg constant-product price impact separately from the configured pool fee. Price impact compares actual integer output against the post-fee marginal spot output; it excludes the fee itself and is not a guarantee against state changes or MEV.
- Optional `--gas-units-assumption` multiplies an operator-supplied gas-unit assumption by the current node `eth_gasPrice` result. `--gas-safety-bps` adds a configurable unit buffer (default 20%).
- The resulting estimate is denominated in the chain's native asset wei. It is deliberately not converted to the quote token and is not deducted from gross profit because there is no independently validated native-to-quote valuation route yet.
- This is not `eth_estimateGas`, a signed transaction simulation, or a guarantee of actual gas used. A future transaction simulator must construct the exact atomic route call and simulate it against the same state before any gas estimate can be considered route-specific.
- Example: `arb-engine v2-sim ... --gas-units-assumption 350000 --gas-safety-bps 2000`.
- Ethereum JSON-RPC defines `eth_gasPrice` as a node-provided current gas-price estimate; it does not estimate gas units. The operator-supplied unit assumption and the node fee quote are therefore kept separately visible. Reference: https://ethereum.org/developers/docs/apis/json-rpc/


## Slippage-bound reporting

- Optional router validation derives a minimum buy output from the buy quote using `--slippage-bps` (default 50 bps).
- The second router quote is then requested using that conservative minimum buy output, not the expected buy output. A second minimum quote output is derived using the same per-leg tolerance.
- These values are read-only scenario bounds. No calldata is signed or submitted, and they do not guarantee execution price or transaction success.


## Pair reserve-update timestamp

- Each V2 `getReserves()` response includes `blockTimestampLast` (a uint32 timestamp for the last pair reserve update). The reader now records this value and its modulo-2^32 elapsed age relative to the pinned block timestamp.
- This is descriptive pool activity metadata, not a freshness failure by itself: an inactive pool can have unchanged but still-current reserves. The separately checked pinned block timestamp/hash remains the actual snapshot freshness and reorg control.


## Local Anvil fork-call simulator

- Foundry Anvil is an open-source in-memory EVM node that can fork an EVM network at a fixed block and expose execution traces. Official documentation: https://www.getfoundry.sh/anvil/index.html
- The new `fork-call` command connects only to HTTP loopback addresses (`127.0.0.1`, `localhost`, or `::1`); it refuses remote RPC endpoints and verifies the Anvil client identity, chain ID, exact fork head, and pinned block hash.
- It accepts exact caller-supplied calldata and performs only `eth_call`, `eth_estimateGas`, and optionally `debug_traceCall`. It rechecks the block hash and head after simulation.
- The adapter's RPC allowlist excludes `eth_sendTransaction`, `eth_sendRawTransaction`, signing, impersonation, account unlock, and state mutation methods. It does not create calldata, approvals, wallets, or transactions.
- Example setup (run Anvil separately and keep it bound to loopback):
  `anvil --fork-url https://ethereum-rpc.publicnode.com --fork-block-number 20000000 --chain-id 1 --host 127.0.0.1 --port 8545`
- Example call:
  `arb-engine fork-call --chain-id 1 --fork-block 20000000 --to 0x... --data 0x... --from-address 0x... --trace`
- The caller must supply correct calldata and a caller address whose fork-state balances/allowances are representative. A successful single call is not proof that a multi-DEX round trip is atomic, profitable, or executable in a future block. It does not submit a transaction or alter the fork.
- Anvil's default development accounts are publicly known; this simulator never requests or uses them. Do not expose the Anvil RPC port to a network or use account impersonation for this workflow.
- Anvil fork simulation depends on the upstream RPC serving state at the selected block. Keep the fork pinned and treat unavailable/pruned state as a hard failure.
