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

## Live read-only deployment verification

- The `venue-check` command uses the public Ethereum JSON-RPC endpoint to verify the current chain ID, pins one block number/hash, checks factory/router/pair runtime bytecode, reads Router02 `factory()`, factory `getPair(WETH,USDC)`, pair `factory()`, and pair `token0()/token1()`, then rechecks the pinned block hash.
- Canonical Ethereum WETH address is sourced from Uniswap's official deployments documentation; Ethereum USDC address is listed by Circle and in Uniswap's official pool examples.
- The Telegram Arbitrage Engine CI runs this read-only smoke on pull requests and pushes to main. It uses no API key, wallet, signer, or transaction method; retry count and timeout are bounded.
- A passing check proves those deployment relationships for the canonical WETH/USDC pair at the checked block only. It does not prove fee semantics for every pool, token safety, transfer behavior, gas, atomic two-venue route execution, profitability, or future-block availability.
- If the public RPC is unavailable or rate-limited, the live smoke fails visibly; deterministic unit tests remain separate and use a fake RPC.

## Source-documented V2 venue registry

The packaged registry currently contains Ethereum mainnet only:

| Venue | Factory | Router02 | Swap fee assumption |
|---|---|---|---:|
| Uniswap V2 | `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f` | `0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D` | 30 bps |
| SushiSwap V2 | `0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac` | `0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F` | 30 bps |

- Uniswap deployment source: https://developers.uniswap.org/docs/protocols/v2/deployments
- Uniswap fee source: https://developers.uniswap.org/docs/get-started/concepts/fees (0.30% total V2 swap fee; protocol fee affects distribution, not the total trader fee).
- SushiSwap deployment source: https://github.com/sushiswap/v2-core/tree/master/deployments/ethereum
- SushiSwap fee source: https://github.com/sushiswap/v2-core/blob/master/contracts/UniswapV2Pair.sol (pair invariant deducts 3/1000 from input).
- Registry status is `source_documented_not_live_verified`. Selecting a venue auto-fills its fee, factory, and Router02, then the CLI verifies `pair.factory()`, `factory.getPair(token0,token1)`, and `router.factory()` via `eth_call` at the pinned block.
- These checks establish contract relationships at the selected block; they do not prove proxy implementation safety, actual token transfer behavior, gas cost, atomic route success, or profitability. Fee assumptions remain explicitly sourced assumptions until separately validated against the actual pair implementation.
- Only Ethereum mainnet (chain ID 1) is currently registered. Other networks must continue to use explicit manual addresses and fee assumptions until separately researched and verified.

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


## Open-source tracing evaluation

- Evaluated `shottah/tracer` (Rust; MIT OR Apache-2.0) as a candidate for richer call-tree, token-balance-delta, fund-flow, and historical transaction replay reports: https://github.com/shottah/tracer
- It can use an Anvil-fork backend to replay an already-mined transaction and compare the replay with the original receipt. This is useful for later audit/replay tooling, but it does not directly replace the current exact-calldata `fork-call` interface for a hypothetical, not-yet-submitted route.
- Not added as a dependency in this release: it introduces a Rust toolchain and a separate multi-crate executable, while the Python adapter is intentionally small, stdlib-only, loopback-restricted, and read-only. Reconsider only when trace decoding, ERC-20 balance deltas, or historical receipt replay become a concrete requirement.


## ERC-20 token contract preflight

- The read-only `token-check` command verifies chain ID, pins the latest block number/hash, checks that the address has runtime bytecode at that block, and reads `decimals()`, `symbol()`, and `name()` where available.
- ERC-20 `name`, `symbol`, and `decimals` are optional metadata methods under EIP-20. The tool never invents defaults: missing or malformed metadata is reported as unavailable/incomplete.
- Common ABI dynamic-string and legacy bytes32 metadata encodings are decoded with strict length bounds.
- A successful check only establishes that bytecode exists and that some metadata calls returned parseable values. It does not prove ERC-20 transfer compliance, truthful metadata, safe proxy implementation, no transfer tax, no blacklist/pause logic, no rebasing, or absence of honeypot behavior.
- EIP-20 reference: https://eips.ethereum.org/EIPS/eip-20
- Example: `arb-engine token-check --token-address 0x... --chain-id 1`
- No approvals, transfer calls, signing, or transactions are performed.


## DEX Screener pair-address discovery and on-chain V2 verification

- Official endpoint: GET /token-pairs/v1/{chainId}/{tokenAddress}; published rate limit is 300 requests/minute. Reference: https://docs.dexscreener.com/api/reference
- The scanner uses this endpoint only to enumerate candidate pair addresses and token-address metadata for an explicitly supplied base token. It does not use the API's price, USD liquidity, volume, or pair labels as an execution signal.
- Candidate filtering requires the exact explicitly supplied base/quote contract addresses, Ethereum chain slug, and a DEX ID mapped to the packaged trusted venue registry. Candidate count is bounded (default four); the public API response is capped at 200 entries before parsing.
- Every accepted candidate must then pass on-chain pair.factory() and factory.getPair(base,quote) checks against the trusted factory at one pinned block. Both venue pools are read at that same block; both Router02 factory() bindings are checked; and both round-trip directions use router getAmountsOut via eth_call at the same block.
- The reader applies an explicit per-scan RPC request budget (default 32, maximum 64), and the scanner revalidates the pinned block hash after quote collection. Any RPC/ABI failure is surfaced as a failure; a non-V2 or unregistered candidate is classified as a candidate mismatch, not silently accepted.
- This is a read-only gross router-quote comparison. It is not a flash-swap repayment simulation, gas estimate, net-profit estimate, historical backtest, paper trade, or live execution. No calldata is built, no wallet/signer is used, and no transaction is submitted.
- The scanner first uses DEX Screener candidates, then queries any missing registered venue's trusted factory `getPair(tokenA,tokenB)` directly at the same pinned block. A factory-discovered pair is still required to pass `pair.factory()` and `factory.getPair()` verification before it is quoted. This avoids a ranked discovery API silently excluding a lower-volume venue; if a trusted factory has no pair, the scanner returns an explicit insufficient-venues status.
- DEX Screener documents the endpoint and its rate limit; Uniswap V2 factory source confirms getPair is the factory's canonical pair registry and pair creation emits PairCreated: https://github.com/Uniswap/v2-core/blob/master/contracts/UniswapV2Factory.sol

## On-chain V2 cross-venue candidate scan

- DEX Screener API reference: https://docs.dexscreener.com/api/reference
- Discovery uses `GET /token-pairs/v1/{chainId}/{tokenAddress}` for the explicit base-token contract. The response contributes pair addresses, chain/DEX identifiers, token addresses, and optional display metadata only. DEX Screener price, USD liquidity, and volume fields are not read or used by the scanner.
- The DEX Screener endpoint family is documented at up to 300 requests/minute; the client still enforces a one-second minimum interval between sequential requests. The scanner caps the response at 200 candidate rows, filters by the exact explicit quote-token contract, and rejects more than four relevant pair candidates rather than silently truncating them.
- The operator must provide both exact base-token and quote-token contract addresses plus a quote-token capital amount. Symbols are display metadata only; stablecoins are never inferred from symbols.
- DEX IDs map through the versioned trusted venue registry. A DEX label is only a discovery hint: each candidate must pass on-chain pair `factory()` and factory `getPair(tokenA,tokenB)` checks at the pinned block. Non-V2 candidates are skipped; transport/provider errors remain fatal.
- Both verified pools are read at the exact same `EvmBlockContext`; the reader checks chain ID, block hash, block freshness, and canonicality. Both Router02 `factory()` relationships are verified at that block, then `getAmountsOut(uint256,address[])` is called for both buy/sell directions using the same block tag.
- The Ethereum reader enforces a per-invocation RPC request budget (default 32; configurable up to 64) and a conservative 0.55-second minimum request interval by default. Nodely documents a 2 requests/second limit for heavy methods such as `eth_call`; the default pacing stays below that ceiling. With four candidates, the scanner's fixed path is bounded below the default budget.
- Output contains raw token units, on-chain token decimals, human-readable amounts, gross quote-token surplus, and gross return basis points. It is a same-block router round-trip quote only—not net profit, a flash-swap repayment proof, a paper order, or an execution guarantee.
- Gas, flash-swap repayment mechanics, MEV/builder payments, inclusion/revert risk, transfer taxes, state changes after the pinned block, and execution overhead are excluded. No calldata is built, no wallet or signer is used, and no transaction is submitted.
- Uniswap V2 Router02 reference: https://developers.uniswap.org/docs/protocols/v2/reference/smart-contracts/router-02
- Uniswap V2 pricing reference: https://developers.uniswap.org/docs/protocols/v2/concepts/pricing
- Uniswap V2 flash-swap repayment guide: https://developers.uniswap.org/docs/protocols/v2/guides/flash-swaps

## Open-source implementation references

- [BowTiedDevil/degenbot](https://github.com/BowTiedDevil/degenbot) (MIT) is a useful reference for deterministic AMM pool math and protocol adapters across Uniswap V2/V3/V4, Curve, Balancer, Solidly, and Aave V3. Prefer selective, version-pinned math/test reuse over adopting a complete bot.
- [gardennn/arb-rewind](https://github.com/gardennn/arb-rewind) (MIT) is a reference for historical fork replay and route research. Its public maintenance/activity level is limited; treat it as a design reference, not production evidence.
- [flashbots/mev-share](https://github.com/flashbots/mev-share) documents a later-stage private order-flow/MEV research path. It is not a dependency for the read-only scanner and should only be evaluated after route economics and execution simulation are proven.
- No external bot, RPC provider, wallet, or paid aggregator is required by this read-only discovery path.



## Open-source project review for the next research milestones

### degenbot (BowTiedDevil/degenbot)
- Repository: https://github.com/BowTiedDevil/degenbot ; MIT license stated by the project.
- Strong candidate for a later protocol-math and state-engine reference: its Rust core/Python driver covers Uniswap V2/V3/V4, Curve, Solidly, Balancer V2, and Aave V3; it includes pool state, event decoding, swap math, cyclic arbitrage solving, and an in-process revm simulation engine.
- Do not import its live runner or signing/submission path into this project. The current engine intentionally remains Python/stdlib-first and read-only. First compare its exact V2 integer math and fork-test fixtures against our existing implementation; only consider a pinned optional dependency after a reproducible parity suite and supply-chain review.
- Repository activity and API surface are substantial, but this is a complex Rust/PyO3 stack with a higher operational/build burden than the present lightweight scanner.
### arb-rewind (gardennn/arb-rewind)
- Repository: https://github.com/gardennn/arb-rewind ; MIT license stated by the project.
- Useful reference for historical fork replay across Uniswap V2/V3, Curve, and Balancer V2, log-space triangular-cycle detection, and end-to-end simulated Balancer flash-loan settlement inside Foundry tests.
- Its README explicitly lists important limitations: triangular cycles only, greedy per-hop venue choice, fixed probe size, no mempool awareness, and not a production searcher. Treat historical positive results as block-specific backtest evidence, not forward returns.
- Recommended next use: port the concept of replaying captured candidate snapshots at pinned historical blocks and testing multiple input sizes; do not adopt its executor as live trading code.
### Envio HyperIndex Uniswap V2 indexer
- Repository: https://github.com/enviodev/uniswap-v2-indexer ; a multichain migration of the Uniswap V2 subgraph with PairCreated, Sync, Swap, Mint/Burn, and LP Transfer indexing.
- It advertises support for Ethereum, Optimism, Arbitrum, Base, Polygon, BSC, Avalanche, and additional networks. Its README requires Node.js 22+, pnpm, Docker, an Envio API token, and RPC URLs for its historical E2E replay; this is not a zero-configuration/free-tier assumption.
- Defer integration until the scanner needs continuous multi-chain pair discovery. A self-operated indexer introduces infrastructure and maintenance cost; compare with direct bounded RPC logs and The Graph before choosing.
### The Graph / Uniswap subgraphs
- Official Uniswap guidance says public endpoints are examples that may not be actively maintained, hosted subgraphs have been deprecated, and production users should verify deployment/schema/chain versions or operate their own subgraph. Gateway access uses an API key associated with billing.
- This makes a public subgraph a poor hard dependency for the current zero-cost baseline. It remains an optional discovery/backfill source only after endpoint health, freshness, schema, cost, and rate limits are independently checked.
### MEV-Share
- Specification repository: https://github.com/flashbots/mev-share . It describes an open-source order-flow auction protocol with privacy/validity conditions and searcher/builder interactions; it is not a simple free quote API or a profitability guarantee.
- Consider only after historical backtesting, complete atomic-route simulation, risk controls, and a separately reviewed execution architecture. Private orderflow, builder competition, and inclusion economics are advanced operational concerns, not prerequisites for proving the scanner's correctness.


### Independent public Ethereum RPC backstop

- Nodely documents a no-account/no-key Ethereum mainnet HTTPS JSON-RPC endpoint at https://eth.nodely.dev, served by two European nodes with automatic failover. Its nodes are pruned and retain state for roughly 100,800 blocks (about 14 days); historical calls older than that window are not supported. Nodely documents a 2 requests/second per-IP limit for heavy methods such as `eth_call` (burst 5), so the scanner defaults to a 0.55-second interval. Reference: https://nodely.io/docs/ethereum
- This endpoint is used by the live cross-venue scanner smoke as an independent read-only provider, while the existing venue-deployment smoke continues to use PublicNode. This reduces dependence on a single RPC operator; it is not an SLA or a guarantee against shared public-endpoint throttling.
- The scanner's --rpc-url option permits an operator-selected HTTPS endpoint. Do not assume a public endpoint supports archive history, trace/debug namespaces, WebSockets, or high request volume unless its operator documents those capabilities.
