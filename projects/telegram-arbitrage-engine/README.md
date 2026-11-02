# Telegram Arbitrage Engine

Simulation-first spot-arbitrage research module, isolated inside the autonomous-engine repository. This import does not alter the engine's existing runtime, task schema, provider routing, or storefront projects.

## Safety
- Read-only public DEX Screener market data.
- Deterministic risk calculations use Decimal; floats are rejected.
- SQLite stores observations only. A repeated-observation report measures persistence, not historical PnL or executable performance.
- No wallet connection, signing, approvals, swaps, exchange orders, or live trading code.
- Optional Telegram bot uses long polling, private-chat allowlisting, a per-chat rate limit, and a persisted update offset checkpointed after each processed update. This narrows the replay window but does not provide exactly-once command or message delivery; a crash between processing and checkpointing can still replay the current update. Configure the token and comma-separated allowed private chat IDs through environment variables; never commit secrets.
- DEX Screener USD marks are indicative only, not executable quotes or proof of available depth. Its search response does not provide an on-chain quote timestamp; retrieval age must not be mistaken for pool-state freshness.
- No AI decision can authorize a trade. Any future AI use is restricted to research and explanation.
- Telegram polling is **at-least-once**, not exactly-once: the bot durably checkpoints the next update offset after each completed update. For `/scan`, observation rows and a replayable response receipt are committed atomically under Telegram's `update_id`; a replay after that commit reuses the stored response without another DEX discovery request. A crash before the transaction commits can still repeat the read, and Telegram `sendMessage` delivery remains at-least-once because remote delivery cannot be committed atomically with SQLite.
- An optional local Anvil fork-call adapter accepts exact caller-supplied calldata and performs read-only eth_call/eth_estimateGas with optional callTracer output; it refuses non-loopback endpoints and never submits transactions. It simulates one call only, not an atomic two-venue arbitrage route.
- A read-only EVM JSON-RPC adapter retrieves two V2 pool states and token decimals at one pinned block. The V2 constant-product calculator uses integer raw units and requires two distinct pools at the same block; it is reserve math only and excludes gas, transfer taxes, dynamic fees, reverts, MEV, and execution. Pool fee basis points can be supplied manually or selected from the source-documented Ethereum V2 registry. Registry entries remain unverified until the CLI confirms pair.factory(), factory.getPair(), and router.factory() at the pinned block; the fee model is still a documented assumption.

## Read-only Solana price observations

A dependency-free Jupiter Price API V3 client is available at `arb_engine.market_sources.jupiter.JupiterPriceV3Client`. It batches up to 50 exact 32-byte base58 mint addresses per request, preserves USD price, token decimals, block ID, 24-hour change, liquidity, creation time and local receive time, and returns omitted/unreliable prices separately in `missing_mints`. Monetary fields are parsed as `Decimal`. Price observations are explicitly labelled `indicative_price`, not executable quotes or net-profit evidence.

The existing Jupiter quote-only client and Price V3 client share a process-wide 0.5 requests/second keyless limiter. HTTP 429 responses defer the limiter using bounded Retry-After handling. No API key, symbol search, wallet, transaction, or swap execution is used.

Example:

```python
from arb_engine.market_sources.jupiter import JupiterPriceV3Client

client = JupiterPriceV3Client()
batch = client.fetch_prices([
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
])
for observation in batch.observations:
    print(observation.mint, observation.usd_price, observation.block_id)
print("Missing/unpriced:", batch.missing_mints)
```

## Read-only Solana quote data

A fixture-tested Jupiter Swap API V2 quote-only client is available at `arb_engine.market_sources.jupiter.JupiterQuoteOnlyClient`. It calls only `GET /swap/v2/order` without `taker` or an API key. Jupiter documents that this returns a quote with `transaction: null`; the adapter rejects any response that contains a transaction. It enforces the keyless 0.5 requests/second limit (2-second minimum interval), validates raw uint64 amounts and mint identity, and preserves fee/route/price-impact metadata.

Example:

```python
from arb_engine.market_sources.jupiter import JupiterQuoteOnlyClient

client = JupiterQuoteOnlyClient()  # keyless, quote only; no wallet or transaction
quote = client.quote(
    input_mint="So11111111111111111111111111111111111111112",
    output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    amount_raw="100000000",
)
print(quote.output_amount_raw, quote.fee_bps, quote.router, quote.price_impact_pct)
```

This is an indicative aggregator quote, not a guaranteed fill or a net-profit calculation. Jupiter's Meta-Aggregator may combine on-chain routes and off-chain RFQ sources; fees, quote freshness, last-look behavior and execution risk must be accounted for separately. No transaction is built, signed, or submitted. See [data sources and trust boundaries](DATA_SOURCES.md).

## Same-block on-chain V2 cross-venue scan

### BNB Smart Chain example

```bash
python -m arb_engine.cli onchain-v2-scan \
  --chain-id 56 \
  --rpc-url https://YOUR-TRUSTED-BSC-RPC \
  --token-address 0x<BASE_TOKEN_ADDRESS> \
  --quote-token-address 0x<QUOTE_TOKEN_ADDRESS> \
  --capital-quote 1000
```

For Ethereum and BNB Smart Chain, `--venue-ids` is optional because each currently has exactly two registered venues. Other chains with incomplete or ambiguous venue pairs require explicit `--venue-ids`; the scanner does not guess. The RPC endpoint must report the requested chain ID and support the read-only calls used by the scanner.



The `onchain-v2-scan` command uses DEX Screener only as a candidate discovery hint. It resolves the default venue pair from `--chain-id`: Ethereum selects Uniswap V2 / SushiSwap V2; BNB Smart Chain selects PancakeSwap V2 / ApeSwap V2. It verifies discovered pairs against the selected chain's registered V2 factories and, if a ranked discovery response omits a registered venue, queries that venue's trusted factory `getPair()` directly at the same pinned block. Every selected pair still passes `pair.factory()` and `factory.getPair()` verification before both pools are read and both Router02 factories and buy/sell `getAmountsOut` quotes are checked at that same block.

Example (replace the token addresses with verified contracts; the quote token is always explicit):

```bash
python -m arb_engine.cli onchain-v2-scan \
  --token-address 0x<BASE_TOKEN_ADDRESS> \
  --quote-token-address 0x<QUOTE_TOKEN_ADDRESS> \
  --capital-quote 1000 \
  --max-candidate-pairs 4 \
  --max-rpc-requests 32 \
  --min-request-interval 0.55
```

The result reports raw and human-readable router outputs and gross quote-token surplus for each direction. It is **not net profit** and does not model flash-swap repayment, gas, MEV, inclusion/revert risk, transfer taxes, or execution overhead. Candidate discovery is bounded to four pairs; the Ethereum reader enforces a 32-request default budget and 0.55-second request pacing to respect public RPC `eth_call` limits. No transaction, calldata, wallet, signer, approval, or order is created or submitted.

Use `flash-swap-sim` separately for the V2 pair-invariant flash-swap repayment simulation; neither command enables live execution.

## Read-only centralized-exchange market data

A fixture-tested Bybit public market-data adapter is available at `arb_engine.market_sources.bybit.BybitPublicMarketData`. It retrieves spot/perpetual/option tickers and order-book snapshots using public GET endpoints only, preserves numeric precision with `Decimal`, records venue timestamps/update IDs, rejects malformed or crossed books, blocks redirects, and applies a conservative one-request-per-second per-instance budget. It does not require API credentials and does not expose account/order/withdrawal methods.

Example:

```python
from arb_engine.market_sources.bybit import BybitPublicMarketData

market = BybitPublicMarketData()  # public Bybit REST API; no API key
book = market.fetch_orderbook(category="spot", symbol="BTCUSDT", limit=50)
for price, size in book.bids[:5]:
    print(price, size)
perpetual = market.fetch_tickers(category="linear", symbol="BTCUSDT")[0]
print(perpetual.mark_price, perpetual.index_price, perpetual.funding_rate)
```

This is a source adapter, not yet a cross-venue strategy or profitability engine. Depth is only a timestamped snapshot; fees, fill probability, latency and account eligibility must be modelled before ranking any opportunity. See [data sources and trust boundaries](DATA_SOURCES.md).

## Read-only Binance Spot market data

A fixture-tested Binance public market-data adapter is available at `arb_engine.market_sources.binance.BinancePublicMarketData`. It uses the official market-data-only HTTPS host, requires no API key, and exposes only depth snapshots and best bid/ask. Decimal precision and the REST `lastUpdateId` are preserved; local retrieval time is recorded separately because the REST depth response has no exchange timestamp.

Example:

```python
from arb_engine.market_sources.binance import BinancePublicMarketData

market = BinancePublicMarketData()  # public market-data-only host; no API key
book = market.fetch_orderbook(symbol="BTCUSDT", limit=100)
ticker = market.fetch_book_ticker(symbol="BTCUSDT")
print(book.last_update_id, book.bids[0], ticker.ask_price, book.fetched_at)
```

The adapter enforces one request per second and does not synchronize REST snapshots with WebSocket depth updates. It is a timestamped indicative source, not a guaranteed executable book or net-profit engine. See [data sources and trust boundaries](DATA_SOURCES.md).

## Read-only Kraken Spot market data

A fixture-tested Kraken public REST adapter is available at `arb_engine.market_sources.kraken.KrakenPublicMarketData`. It uses only the public `AssetPairs` and `Depth` GET endpoints, requires no API key, resolves pair aliases to Kraken's internal REST pair ID, and preserves exact Decimal order-book levels and per-level timestamps.

Example:

```python
from arb_engine.market_sources.kraken import KrakenPublicMarketData

market = KrakenPublicMarketData()  # public endpoints only; no API key
instrument = market.fetch_instrument(pair="XBTUSDT")
book = market.fetch_orderbook(pair=instrument.instrument_id, count=100)
print(instrument.base_asset_code, instrument.quote_asset_code)
print(instrument.fee_tiers)  # taker/maker bps by 30-day volume threshold
print(book.bids[:5], book.asks[:5], book.fetched_at)
```

Kraken's internal asset codes (such as `XXBT` and `ZUSD`) must be explicitly mapped to canonical IDs; the adapter never infers equivalence from symbols. Public REST pacing is limited to one request per second per IP by Kraken; the adapter uses a shared process-wide limiter. Taker/maker schedules are preserved but no current account tier is assumed. Missing fee, depth or settlement inputs keep net estimates incomplete. See [data sources and trust boundaries](DATA_SOURCES.md).

## Read-only Coinbase Exchange Spot market data

A fixture-tested public adapter is available at `arb_engine.market_sources.coinbase.CoinbaseExchangePublicMarketData`. It calls only the unauthenticated public product and aggregated order-book GET endpoints. It preserves product increments, minimum market funds, status restrictions, Decimal book levels, order counts, sequence number and exchange/local timestamps.

Example:

```python
from arb_engine.market_sources.coinbase import CoinbaseExchangePublicMarketData

market = CoinbaseExchangePublicMarketData()  # public market data only; no API key
instrument = market.fetch_instrument(product_id="BTC-USD")
book = market.fetch_orderbook(product_id=instrument.instrument_id, level=2)
print(instrument.base_asset_code, instrument.quote_asset_code)
print(instrument.base_step_size, instrument.price_tick_size)
print(book.bids[:5], book.asks[:5], book.sequence)
```

The adapter uses conservative one-request-per-second pacing despite Coinbase's higher published public limit. Product status flags and auction mode are enforced; account-specific fee tiers are not inferred. Coinbase market-data terms apply and regional account eligibility remains unverified. See [data sources and trust boundaries](DATA_SOURCES.md).

## Multi-chain EVM V2 scanning

The V2 candidate scanner now accepts an explicit EVM chain ID, DEX Screener slug and two registered venue IDs. The source-documented registry includes Ethereum (1), BNB Chain (56), Arbitrum One (42161), Optimism (10), Base (8453), Avalanche C-Chain (43114) and Polygon PoS (137). It selects a registered two-venue pair per chain by default; an operator may override the exact pair.

Non-Ethereum runs require an explicit read-only RPC URL and verify the RPC chain ID, factory/pair identity and router binding at one pinned block before returning a route. Registry addresses and fee assumptions are not live attestations. The automated live venue/scanner smoke remains Ethereum-only; the other chains remain source-documented but not live-verified. No cross-chain bridge or transfer route is inferred, and no profitability or execution claim is made.

See [DATA_SOURCES.md](DATA_SOURCES.md) for the full venue matrix and deployment sources.

## Conservative opportunity cost evaluator

The pure `arb_engine.opportunity_costs.evaluate_two_leg_spread` component separates gross spread from estimated net profit. It refuses to compare different canonical asset IDs or quote currencies, rejects stale/partial-depth observations, and requires venue fees, slippage, latency, gas/priority fees, Solana network fees, funding, bridge/settlement and borrow costs when applicable. Missing required costs produce `net_profit_quote=None`; they are never treated as zero.

See [OPPORTUNITY_COST_MODEL.md](OPPORTUNITY_COST_MODEL.md). This evaluator is a deterministic guardrail component and is not yet connected to automated opportunity ranking or order execution. Even a positive result remains an estimate with `execution_verified=False`.

## Public instrument metadata and explicit asset mapping

The Bybit and Binance public adapters now expose single-instrument metadata reads. Bybit uses spot `instruments-info` precision fields; Binance uses `exchangeInfo` LOT_SIZE and PRICE_FILTER rules. `InstrumentRegistry.from_metadata` builds venue instrument specs only when the caller supplies explicit canonical IDs for each venue asset code. It never assumes that matching symbols prove matching assets.

See [INSTRUMENT_METADATA.md](INSTRUMENT_METADATA.md). This provides verified lot steps and price ticks for the depth scanner, but it does not fetch account-specific fees or automatically equate wrapped/native/stablecoin assets.

## CEX cross-venue spot depth scanner

The read-only compare_cex_spot_orderbooks component compares both directions between registered CEX spot books using full depth sweeps at one base quantity. An explicit InstrumentRegistry must map each venue instrument to the same canonical base and quote IDs; symbol equality alone is never sufficient. Instrument minimum base quantity and minimum quote notional are propagated from public metadata and enforced before an estimate can be produced. The result is passed through the Decimal cost evaluator, so unknown fees/latency leave net profit unset, partial depth blocks profit labels, and below-minimum orders are explicitly rejected.

See CEX_CROSS_VENUE.md. This is a read-only comparison component, not an order executor or automated strategy loop.

## Read-only flash-swap simulation

The `flash-swap-sim` command reads two V2 pools at one canonical block, models the opposite-token repayment invariant, and optionally applies a caller-supplied gas-used/gas-price scenario. It can optionally record local research events with `--record-paper`.

Example (replace every placeholder with verified pair/token addresses):

```bash
python -m arb_engine.cli flash-swap-sim \
  --source-pair 0x<SOURCE_PAIR> --source-fee-bps 30 \
  --destination-pair 0x<DESTINATION_PAIR> --destination-fee-bps 30 \
  --borrow-token 0x<BORROW_TOKEN> --borrow-amount-raw 1000000000000000000
```

To add a gas scenario in which the repayment token is USDC and gas is paid in WETH, also supply `--gas-used`, `--gas-price-wei`, `--native-token`, and a same-block `--native-quote-pair` with its fee or venue. Gas units must be a conservative caller estimate including the overhead the caller intends to model; the command does not claim to measure full transaction gas.

Add `--record-paper` to append candidate/snapshot/route/gas events to the selected research ledger (SQLite by default). Without this flag, no ledger write occurs. The `--ledger` argument is the local SQLite path and is ignored in D1 mode. To explicitly select the staged remote backend, set `RAAWAA_PAPER_LEDGER_BACKEND=d1` and provide `RAAWAA_ENGINE_API_URL` plus `RAAWAA_ENGINE_API_TOKEN` through protected environment secrets. D1 errors fail closed and never fall back to SQLite. The Worker must be separately deployed and migration 0004 applied to a non-production D1 database before this mode can work; neither action is performed by this repository change. Candidate/event IDs are deterministic for identical pinned simulation inputs, supporting idempotent retry. The command never creates a paper order, wallet, signer, calldata, approval, or on-chain transaction. See [PAPER_TRADING_LEDGER.md](PAPER_TRADING_LEDGER.md).

## Paper-trading research ledger

An isolated SQLite event ledger is available for provenance-linked research records. It is append-only, hash-chained, idempotent by caller-supplied event ID, and stores raw-unit economics as decimal strings. It is a storage primitive only: no order is created or submitted, and its records are not realized PnL. See [PAPER_TRADING_LEDGER.md](PAPER_TRADING_LEDGER.md).

## Run in isolation
Requires Python 3.11+.

```bash
cd projects/telegram-arbitrage-engine
python -m pip install -e '.[dev]'
pytest
arb-engine demo
arb-engine report --limit 500
arb-engine token-check --token-address 0x... --chain-id 1
arb-engine venues --chain-id 1  # source-documented Ethereum V2 deployments
arb-engine venue-check --chain-id 1  # read-only on-chain factory/router/pair verification
arb-engine scan WETH/USDC --min-liquidity 10000 --min-volume 1000
arb-telegram  # optional read-only polling bot; requires TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_CHAT_IDS
arb-engine v2-sim --buy-pair 0x... --buy-venue uniswap_v2_ethereum --sell-pair 0x... --sell-venue sushiswap_v2_ethereum --quote-token 0x... --base-token 0x... --capital-quote 50 --chain-id 1 --slippage-bps 50 --gas-units-assumption 350000 --gas-safety-bps 2000
# optional exact-calldata simulation against a separately started local Anvil fork
anvil --fork-url https://ethereum-rpc.publicnode.com --fork-block-number 20000000 --chain-id 1 --host 127.0.0.1 --port 8545
arb-engine fork-call --chain-id 1 --fork-block 20000000 --to 0x... --data 0x... --from-address 0x... --trace
```

Scan snapshots are saved to `data/opportunities.sqlite3`. Avoid tight polling and respect upstream rate limits.

See [data sources and trust boundaries](DATA_SOURCES.md) for source rate limits, freshness limitations, and the release gates for future pool-state simulation.

## Before any paper-profit claim
The source-documented venue registry reduces manual address/fee entry for Ethereum Uniswap V2 and SushiSwap V2. Selecting a registry venue causes the CLI to verify pair.factory(), factory.getPair(), and router.factory() at the pinned block, but registry deployment addresses are not pre-verified on-chain and fee provenance remains a documented assumption. Still required: live fork validation, pool-state/block freshness, token transfer-tax/blacklist behavior, independently verified fee semantics, gas valuation, and a composed atomic multi-venue route simulation with full state-diff/revert analysis. The current Anvil adapter only proves a single supplied call on a pinned local fork and does not satisfy this gate. Cross-chain spreads remain informational only and require bridge cost, settlement delay, and custody/bridge risk modelling.

## Current milestone
0.17: adds a bounded DEX Screener token-pair discovery adapter and a same-block Ethereum V2 cross-venue scan. Candidate pair addresses are filtered by explicit token contract addresses and a trusted venue registry, verified through pair.factory()/factory.getPair(), read at one pinned block, and quoted through both Router02 contracts in both directions. The scan enforces candidate and RPC-request budgets, revalidates the block hash, and reports gross router surplus only. It does not include gas, flash-swap repayment, MEV, execution, or net-profit claims; no wallet, signer, calldata, or transaction is used. 0.16: adds a bounded read-only live verification command and CI smoke job for the registered Ethereum V2 factory/router/WETH-USDC pair relationships at one pinned block, using the public Ethereum RPC with bounded retries and no API key. This is deployment relationship verification, not token safety or fee proof. 0.15: adds a source-documented Ethereum V2 venue registry for Uniswap and SushiSwap; registry selections auto-fill fee/factory/router values and force same-block on-chain pair/factory/router verification. Registry source status is explicitly not live-verified; manual fee assumptions remain supported and visibly labelled. 0.14: adds a pinned-block read-only token contract preflight (runtime bytecode presence, optional ERC-20 decimals/symbol/name metadata) and per-update Telegram offset checkpoints. Metadata is not token-safety verification. Also adds a local-Anvil-only exact-calldata call simulator with strict loopback RPC validation, pinned fork block and chain checks, read-only eth_call/eth_estimateGas, optional callTracer diagnostics, and explicit no-submission guarantees. Retains the read-only market scanner, address-aware SQLite ledger, Telegram read-only controller, canonical-block V2 reserve/router quote checks, per-leg slippage bounds, and explicit native gas assumptions. Atomic multi-DEX route construction, token-behavior guarantees, independently verified fee provenance, gas-to-quote valuation, and paper-PnL remain unimplemented. Live market connectivity must be verified in the deployment environment; local CI uses fixtures and does not prove live API availability.
