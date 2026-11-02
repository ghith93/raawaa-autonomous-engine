# Multi-Market Coverage Research

**Research snapshot:** 2026-11-02  
**Scope:** read-only discovery and research adapters only. This document is a coverage decision record, not permission to place orders or enable paid services.

## Executive decision

Keep the current Ethereum V2 implementation as one protocol-specific pilot. Do not make it the platform boundary.

The best low-cost next coverage expansion is:

1. **Solana indicative prices:** Jupiter Price API V3, queried by exact mint addresses. Keyless access is documented at 0.5 requests/second; the free API-key tier is 1 request/second. This is an indicative price source, not an executable quote or proof of arbitrage.
2. **CEX spot order books:** an optional CCXT adapter, initially configured for Kraken public spot market data. CCXT is MIT-licensed and supports 100+ exchange markets; public order-book endpoints do not require account credentials. Kraken says its public REST market-data endpoints should be called at one request/second or less. Keep CCXT optional so the core project has no mandatory dependency or spend.
3. **EVM multi-chain:** add networks only after the specific pair factory, router, fee model, public RPC, and second venue have been verified. The official Uniswap V2 deployment table gives a useful first deployment inventory, but Uniswap addresses alone do not provide cross-venue arbitrage.

Do not combine these data types into one “profit” number. Preserve source type and uncertainty in every observation.

## Current implementation inventory

| Component | Current verified scope | Boundary |
|---|---|---|
| DEX Screener discovery | Candidate pair addresses and liquidity metadata | Indicative discovery only; never used as execution price |
| Ethereum V2 RPC reader | Ethereum chain ID 1, pair state, decimals, pinned block provenance | EVM V2 only |
| Venue registry | Ethereum Uniswap V2 and SushiSwap V2 | No automatic chain/venue inference |
| V2 route simulation | Same-block router `getAmountsOut` quotes and flash-swap research | Gross quote surplus excludes gas and execution risk |
| Paper ledger | Local SQLite and staged authenticated D1 adapter | No production Worker deployment or D1 migration |
| Task/agent engine | Existing engine and workflow infrastructure | No evidence yet of continuous profitable trading |

## Verified coverage matrix

| Market family | Candidate source | Data available | Cost/access | Rate/freshness constraints | Decision |
|---|---|---|---|---|---|
| EVM DEX V2 | Uniswap official deployment registry; existing Sushi/Uniswap registry | Factory/router addresses, on-chain reserves, decimals, same-block router quotes | Public JSON-RPC can be free but endpoint quotas vary | Pin block, verify pair.factory/factory.getPair/router.factory; per-provider request budgets | Keep Ethereum pilot; assess Arbitrum, Base, Optimism, Polygon, BNB Chain and Avalanche next |
| Solana | Jupiter Price API V3 | USD price, decimals, block ID, 24h change, liquidity and creation metadata for up to 50 mints per call | Keyless 0.5 RPS; free key tier 1 RPS; no paid plan required for a small pilot | Sliding-window rate limit; missing/unreliable prices are omitted and must be treated as missing | First Solana adapter; classify as indicative price only |
| Solana executable routing | Jupiter Swap API V2 `/order` | Aggregated route quote plus assembled transaction; routing can include Metis, JupiterZ RFQ, Dflow and OKX | Keyless access 0.5 RPS; free key 1 RPS | `/order` requires a taker and returns transaction data; `/execute` is a separate transaction-landing action | Research only for now; do not call or submit transactions in the first adapter |
| CEX spot | CCXT public methods; Kraken public REST | Order books, trades, tickers, OHLCV, market metadata | CCXT MIT software; public market data usually needs no API key | CCXT built-in rate limiter is enabled by default; typical exchanges allow 1–2 req/s, endpoint weights vary. Kraken public REST recommends <=1 req/s | Select optional CCXT adapter; Kraken first pilot |
| CEX derivatives | CCXT where exchange exposes funding, mark/index, contract metadata and order book | Funding rates, basis, mark/index and depth where supported | Public endpoints may be available without account keys; exchange-specific | Must model contract multiplier, funding interval, settlement asset, margin and regional restrictions | Phase 2 research; do not compare funding rates without instrument normalization |
| Cross-chain | Bridge/aggregator quote APIs plus independent chain readers | Bridge route, estimated fees, output, route and estimated time | Often provider-specific; free-tier and terms vary | Bridge finality, relayer, refund, failure, custody and liquidity risks | Informational only until settlement and failure recovery can be modelled |
| Liquidations / MEV / order flow | Protocol-specific public data and Flashbots documentation | Liquidation thresholds, order flow, auctions and private routing | Highly protocol-specific; some paths require privileged infrastructure | Legal, privacy, competition, latency and execution review required | Separate future research track, not a first-stage adapter |

### EVM deployment findings

The official Uniswap V2 deployment page currently lists factory and Router02 deployments for Mainnet, Arbitrum, Avalanche, BNB Chain, Base, Optimism, Polygon, Zora, WorldChain, Unichain and other networks. It also publishes a unified deployments feed.

These addresses are not enough to enable a network. For every chain we still need:
- A supported second venue with independently verified factory/router addresses.
- A free/read-only RPC endpoint with known request limits and archive/fork capabilities where required.
- A chain-specific native token, gas model, finality/reorg policy and wrapped-native address.
- Same-block pair and router verification tests.
- Token contract address identity and decimals; never match by symbol.

Initial EVM assessment order: **Arbitrum, Base, Optimism, Polygon, BNB Chain, Avalanche**. This is a research priority order, not an enabled-network list. Do not add any until a second venue and public RPC are independently verified.

### Solana findings

Jupiter's current developer documentation identifies:
- Swap API V2 base URL: `https://api.jup.ag/swap/v2`.
- Keyless access at 0.5 RPS without signup.
- Free API-key tier at 1 RPS.
- Price API V3: `GET /price/v3?ids=<mint addresses>`, up to 50 mint addresses per call.
- Price API fields include USD price, decimals, block ID, 24-hour change, liquidity and creation time.
- Tokens with no reliable price or no trade in seven days are omitted rather than returned with a null price.

The first Solana adapter should use **Price API V3 only**. It must:
- Accept exact mint addresses, not ticker/symbol search as identity.
- Retain decimals, token program and response block/provenance where available.
- Mark the result `indicative_price`, not `executable_quote`.
- Treat omitted mint IDs as missing data, never as zero.
- Use a shared 0.5 RPS keyless limiter and honour 429/rate-limit headers.
- Avoid /order, /build, /execute, /submit, wallet, signer or RPC write paths.

Solana mint identity is chain-specific. The mint account stores decimals and token-program ownership. Token-2022 extensions can include transfer fees and transfer hooks, so a price alone cannot establish an executable net output.

### CEX findings

CCXT's current public documentation describes 100+ supported exchange markets and is MIT-licensed. Its unified public methods cover market metadata, tickers, order books, trades and OHLCV. Built-in rate limiting is enabled by default. The manual warns that most exchanges allow roughly 1–2 requests per second, endpoint costs vary, and one exchange instance has one limiter; reuse the same instance instead of creating parallel clients for the same venue/IP.

Kraken's official public REST market-data documentation says:
- Public endpoints are available without an account.
- Most public endpoints are rate-limited by IP.
- A frequency of one request per second or less remains within public market-data limits.
- Trades/OHLC are additionally limited by IP and currency pair.

Initial CEX selection: **Kraken Spot public order book through optional CCXT**. This gives a clean no-credential market-data pilot. Use a single long-lived exchange instance, `enableRateLimit=True`, bounded depth, explicit timeouts and per-source error isolation. Do not enable private methods, balances, orders, withdrawals or account credentials.

Do not automatically enable Binance, OKX or Bybit. Evaluate each venue's current public endpoints, terms, IP/regional restrictions, endpoint weights, symbol/instrument semantics and funding/contract metadata first. Market-data API availability does not imply account or trading eligibility.

### Derivatives and funding/basis

A derivatives adapter must preserve:
- Instrument type (linear/inverse), contract size/multiplier and settlement asset.
- Mark price, index price, last trade and order-book depth as separate fields.
- Funding rate, next funding timestamp and funding interval.
- Margin mode, maintenance margin/liquidation rules and leverage constraints.
- Trading fee tier and borrow/financing costs as unknown until account-specific evidence exists.

A positive funding rate is not automatically an arbitrage profit. Basis convergence, funding timing, position entry/exit, collateral, liquidation, fees and market impact must all be modelled. Keep derivatives read-only and disabled until metadata and funding history are validated.

## Open-source project assessment

| Project | License / evidence | Reusable value | Integration decision |
|---|---|---|---|
| [CCXT](https://github.com/ccxt/ccxt) | MIT; actively maintained; 100+ exchange markets in current docs | Unified public market-data API, market metadata, built-in rate limiting, optional per-exchange support | Preferred optional CEX adapter; no private credentials or order methods |
| [degenbot](https://github.com/BowTiedDevil/degenbot) | MIT | Protocol-specific AMM math and pool representations across V2/V3/V4 and other AMMs | Use as a reference and consider selective math adapters; do not import a full bot or add a heavy dependency without parity tests |
| [arb-rewind](https://github.com/gardennn/arb-rewind) | MIT | Historical fork replay and multi-hop arbitrage research patterns | Reference for a future historical replay/backtest harness; not production proof |
| [Jupiter Swap API / Price API](https://developers.jup.ag/docs/swap/index.md) | Official API docs; free/keyless low-rate access | Solana route and price data without building an RPC stack | Price API V3 first; Swap API V2 quote path remains a separate gated research step |
| [Uniswap V2 deployments](https://developers.uniswap.org/docs/protocols/v2/deployments) | Official protocol deployment registry | Verified starting point for multi-chain V2 factory/router discovery | Import addresses only with chain-specific tests and a second verified venue |
| [Flashbots MEV-Share](https://github.com/flashbots/mev-share) | Open protocol and documentation | Future order-flow/auction research | Defer until the scanner has evidence of durable positive net edge and a separate policy/security review |

## Proposed normalized market observation

Use a shared envelope but preserve each source's native evidence. Do not force every market into a constant-product pool model.

Required fields:
- `source_id`, `market_family`, `venue_id`, `instrument_id`.
- `chain_namespace` and `chain_id` where applicable.
- `base_asset_id`, `quote_asset_id`; canonical mapping must be explicit.
- `data_type`: `onchain_state`, `router_quote`, `order_book`, `indicative_price`, `funding_rate`, or `index_price`.
- `observed_at`, `source_timestamp`, `block_number/hash` where available, and `freshness_ms`.
- Exact decimal/raw-string values; never convert raw token amounts to binary floats.
- Source rate-limit budget, response headers, provenance and any omitted/missing fields.
- `limitations` and `unknown_costs` arrays.

Asset identity rules:
- EVM: `eip155:<chain_id>/erc20:<lowercase_contract_address>`.
- Solana: `solana:<mint_address>`, with token-program identity and decimals.
- CEX: map exchange instrument base/quote to a canonical asset registry entry; ticker symbols alone are not sufficient.
- Never equate two assets merely because they share a symbol (e.g. multiple USDC deployments or bridged assets).

## Opportunity and cost accounting

Every opportunity must separate:
1. **Gross spread / gross quote surplus** — source observations only.
2. **Executable depth** — quote size supported by actual order-book depth or same-block router quote.
3. **Known costs** — trading fees, AMM fees, gas, priority fees, funding, borrow/flash fees, bridge costs, withdrawal/deposit costs and slippage.
4. **Execution risks** — stale quote, latency, MEV, inclusion, partial fills, reorgs, bridge finality, token transfer fees/hooks, liquidation and recovery.
5. **Unknowns** — any unavailable cost or execution constraint remains unknown, not zero.

Do not label an opportunity `net_profitable` unless all required cost components and execution assumptions are sourced and validated. If any material cost is unknown, use `net_profit_status=unknown`.

## Rate limits and reliability policy

- One shared rate limiter per source/venue, not one limiter per task.
- Reuse CCXT exchange instances; do not create one per symbol or worker.
- Jupiter keyless Price API budget: 0.5 RPS, with a shared limiter and a 429 backoff.
- Kraken public REST budget: <=1 request/second.
- EVM RPC: per-provider request budget and timeout; bounded concurrency; pinned block; no assumption that a public RPC is archive-capable.
- Cache token/market metadata separately from fast price observations.
- Circuit-break an unhealthy source without suppressing healthy sources; return partial results with per-source status.
- If cost, rate limit, endpoint terms, regional access or data provenance is unknown, leave the source disabled.

## Implementation sequence

1. Merge and validate the current Ethereum V2 on-chain discovery work.
2. Add a source-neutral observation envelope and fixtures without changing the V2 adapter's math.
3. Implement Jupiter Price API V3 as a keyless, read-only `indicative_price` adapter.
4. Implement optional CCXT Kraken Spot public order-book adapter, with `ccxt` as an optional extra and a fake exchange for CI.
5. Add explicit canonical asset mapping and freshness/precision tests.
6. Only then evaluate EVM multi-chain V2 adapters and derivatives.
7. Build cross-market comparisons only after depth and cost accounting are source-backed.

## Acceptance gates

- At least two independent market families (EVM V2 plus Solana indicative prices and/or CEX order books) are queryable in read-only mode.
- No API key is required for the first-stage public data adapters.
- Tests cover chain/venue/token identity collisions, missing/omitted token prices, stale/malformed data, exact decimal precision, rate limiting, partial source failure and cost decomposition.
- Existing no-wallet/no-signer/no-submit/no-production-deploy gates remain unchanged.
- Exact-head Engine CI and Telegram Arbitrage Engine CI pass.
