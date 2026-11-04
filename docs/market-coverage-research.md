# Multi-network and multi-market research baseline

**Research date:** 2026-11-02  
**Parent work:** Issue #175  
**Purpose:** Expand the arbitrage research engine beyond Ethereum while preserving the existing Python-first, read-only, zero-spend architecture.

## Executive finding

Ethereum V2 is a useful first adapter, not the system boundary. The next coverage layers should be independent adapters for (1) public CEX spot/perpetual market data, (2) Solana aggregator quotes, and (3) additional EVM chains and AMM families. Cross-chain routes and any order-flow/liquidation strategies remain research-only until their settlement and risk models are complete.

Do not install a large framework or add a paid data dependency as the first step. The repository currently has no runtime dependencies in the arbitrage subproject; preserve that baseline while proving one additional market family with fixtures.

## Verified source comparison

| Market family | Candidate source | Read-only capability verified | Limits / costs / caveats | Recommended treatment |
|---|---|---|---|---|
| CEX spot | Binance public market-data REST | Official docs designate `data-api.binance.vision` for public market-data-only requests; endpoint weights and IP usage are exposed in response headers. | Weight varies by endpoint; 429 requires backoff; repeated violations can lead to 418 IP bans. | First CEX adapter candidate; public endpoints only, bounded polling, no keys. |
| CEX spot + derivatives | Bybit V5 market API | Public ticker includes bid/ask, last, volume, mark/index, open interest, funding and basis fields where applicable; order book supports spot, linear, inverse and options. | IP and endpoint/UID limits are distinct; docs state a default 600 requests / 5 seconds per IP and endpoint-level rolling limits. | Strong second CEX source; retain category and instrument identity in normalized records. |
| Multi-exchange abstraction | CCXT | Unified public/private exchange classes across a broad and frequently updated list; supports REST clients and sandbox mode where supported. | Exchange support and endpoint semantics vary; per-exchange rate limits differ by endpoint and weights. Reuse one instance so the throttler/market cache is not reset. | Evaluate as an optional adapter, not a mandatory dependency. Direct public clients may be smaller and easier to audit for the first two exchanges. |
| Solana aggregation | Jupiter Swap API V2 Meta-Aggregator | Official `GET /swap/v2/order` returns a quote with `transaction: null` when `taker` is omitted; no wallet is needed. It aggregates Metis, JupiterZ RFQ, Dflow and OKX routing. | Keyless access is 0.5 requests/second; free API key is 1 request/second. RFQ makers have last-look rights, so the quote is not a guaranteed fill. Old V1 `/quote` is superseded by V2 `/order`. | High-priority independent market family. Use only `/order` without `taker`; reject any non-null transaction. Never call `/build`, `/execute`, `/submit`, or any signing/landing path. |
| EVM multi-chain | Direct JSON-RPC + per-chain trusted venue registries | Existing reader already uses chain ID, pinned block hashes, factory/pair verification and router quotes, but the current registry and candidate scan are Ethereum V2-specific. | Each chain/venue needs its own verified deployment addresses, fee model, RPC limits, finality/reorg rules and token identity. Public RPC access is not guaranteed or unlimited. | Extract a chain-aware adapter contract only after current V2 scanner lands; add networks one by one from official deployment sources. |
| AMM protocol math | degenbot (MIT) | Open-source reference for V2/V3/V4, Curve, Balancer, Solidly and Aave V3 state/math and simulation concepts. | Larger Rust/PyO3 footprint than this stdlib-first module; do not import its executor or signer path. | Use for parity research and test vectors; no dependency until a reproducible benefit is demonstrated. |
| Historical replay | arb-rewind (MIT) | Reference for historical fork replay and route research across multiple AMM families. | Project README documents limited strategies and non-production limitations. Historical simulation is not forward profitability evidence. | Adopt the replay/testing concept selectively after source adapters exist. |

## Source links

- Binance Spot REST API: https://developers.binance.com/en/docs/products/spot/rest-api
- Binance market-data-only endpoint: https://developers.binance.com/en/docs/products/spot/rest-api/market-data-endpoints
- Bybit V5 ticker: https://bybit-exchange.github.io/docs/v5/market/tickers
- Bybit V5 order book: https://bybit-exchange.github.io/docs/v5/market/orderbook
- Bybit rate limits: https://bybit-exchange.github.io/docs/v5/rate-limit
- CCXT manual and rate-limit guidance: https://docs.ccxt.com/docs/manual
- Jupiter Swap API V2 quote-only overview: https://developers.jup.ag/docs/swap
- Jupiter Developer Platform rate limits and keyless access: https://developers.jup.ag/changelog/developer-platform
- Jupiter API docs index: https://developers.jup.ag/docs/llms.txt
- degenbot: https://github.com/BowTiedDevil/degenbot
- arb-rewind: https://github.com/gardennn/arb-rewind
- Envio Uniswap V2 indexer: https://github.com/enviodev/uniswap-v2-indexer

## Jupiter quote-only integration detail

The current Jupiter Swap API V2 documentation explicitly distinguishes quote-only and transaction paths: `GET /swap/v2/order` without `taker` returns `transaction: null`; including `taker` returns a ready-to-sign transaction. Keyless calls are limited to 0.5 requests/second, so a zero-cost prototype should use at least a two-second per-client interval. The Meta-Aggregator combines Metis on-chain routing with JupiterZ RFQ, Dflow and OKX; JupiterZ market makers retain last-look rights. Treat the result as an indicative aggregator quote with source-specific fee/route evidence, not a guaranteed executable fill. Do not call V1 `/quote` (superseded), V2 `/build`, `/execute`, `/submit`, or any wallet/transaction path.

## Architecture decision

1. Keep the existing indicative DEX Screener scanner separate from on-chain quotes and exchange order books.
2. Add a small source adapter contract with explicit capabilities: market family, venue ID, chain/instrument ID, data type, timestamp/freshness, provenance, rate policy, request budget and exact numeric strings.
3. Use separate quote engines for EVM AMMs, Solana aggregator routes, CEX order books and derivatives. Never reuse V2 constant-product math for order books, concentrated liquidity or Solana programs.
4. Normalize only common fields (source, instrument identity, side, input/output amount, timestamp, raw evidence); retain protocol-specific payloads for independent verification.
5. Use address/mint/contract identity plus chain/network ID. Symbols are labels only and must never join assets across chains.
6. Enforce source-specific rate limits, timeouts, bounded retries, response-size ceilings and partial-source failure isolation. Cache metadata with explicit freshness; never treat retrieval time as quote-state time.
7. Compare full depth and costs: taker/maker fees, spread, slippage, gas, priority fees, funding, borrow/flash fees, bridge/settlement fees, latency and failure risk. Gross spread is not net profit.
8. Do not include private exchange endpoints, account balances, orders, withdrawals, wallet access, signing, transaction construction or submission in the read-only discovery adapters.

## Implementation sequence

- **Track A — CEX spot:** implement Binance public market-data adapter with a deterministic fixture and exact Decimal parsing; then add Bybit as an independent adapter and compare order-book depth.
- **Track B — Solana:** implement Jupiter quote-only adapter using mint addresses and exact raw amounts; enforce keyless 0.5 RPS default and prohibit transaction-building endpoints.
- **Track C — EVM:** generalize the existing chain ID and RPC configuration without weakening pinned-block, factory, router and venue verification. Add additional chains only after official deployment addresses and fee semantics are sourced and tested.
- **Track D — strategy layer:** add cross-venue and triangular route enumeration only after at least two market families produce validated normalized observations. Add derivatives/funding and cross-chain spreads as separate strategy modules.
- **Track E — evaluation:** capture snapshots, replay historical blocks/order-book states where possible, measure stale data, missed opportunities, depth, fees, latency, and false-positive rate. Keep simulated outcomes separate from realized PnL.

## Explicitly deferred

- Any paid provider plan, paid RPC/API, paid CCXT Pro, paid indexing service, or new infrastructure.
- Any account upgrade, billing enablement, or secret creation.
- Any signing, order placement, transaction submission, bridge transfer, withdrawal, lending, liquidation or MEV execution.
- Any claim that a gross spread is profitable without full cost and execution evidence.

## Status and evidence

This document records the research baseline and design decision. It was written
before implementation and is kept for its sourcing and the decisions it records.

Implementation has since gone further than this document claims, so the accurate
statement lives in **[market-coverage-status-2026-11-04.md](market-coverage-status-2026-11-04.md)**:
CEX spot adapters (Binance, Bybit, OKX, Kraken, Coinbase), a quote-only Solana
aggregator (Jupiter), perpetual funding data, and on-chain EVM V2/V3 all exist
with fixture tests. What does **not** exist is a cross-family comparator (the
comparator is CEX-spot only), triangular/cyclic route enumeration, a derivatives
strategy module, a cross-chain comparator, or an evaluation harness.

Every source remains `source_documented_not_live_verified`; no live endpoint has
been called. Track remaining work in issue #175; it must still be delivered
through isolated PRs with exact-head Engine CI and Telegram Arbitrage Engine CI.
