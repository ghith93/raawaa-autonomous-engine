Public instrument metadata and canonical asset mapping

Status: implemented for single-symbol Bybit Spot and Binance Spot metadata; read-only.

Bybit

- Endpoint: GET /v5/market/instruments-info?category=spot&symbol=BTCUSDT.
- The adapter reads baseCoin, quoteCoin, status, lotSizeFilter.basePrecision, priceFilter.tickSize and minOrderAmt.
- Bybit documents basePrecision as the base-coin precision for Spot. The adapter retains it as the base quantity step used by the scanner.

Binance

- Endpoint: GET /api/v3/exchangeInfo?symbol=BTCUSDT on the official data-api.binance.vision market-data-only host.
- The adapter reads baseAsset, quoteAsset, status, LOT_SIZE.stepSize, LOT_SIZE.minQty, PRICE_FILTER.tickSize and MIN_NOTIONAL/NOTIONAL.minNotional where supplied.
- Binance's LOT_SIZE stepSize defines the allowed quantity increment; PRICE_FILTER tickSize defines the allowed price increment.

Canonical mapping

InstrumentRegistry.from_metadata(metadata, canonical_asset_ids) builds registry entries only for instruments whose status is Trading/TRADING. The caller must explicitly map each (venue_id, asset_code) to a canonical ID, for example ('bybit','BTC') -> 'asset:canonical:BTC' and ('binance','BTC') -> the same canonical ID. Quote assets such as USDT and USDC must be mapped separately; they are never treated as equivalent automatically.

The registry key remains (venue_id, instrument_id). It does not join instruments merely because their symbols look alike. Missing base or quote aliases fail closed. Metadata supplies precision and minimum order constraints. The common CEX comparator enforces the minimum base quantity and quote notional before evaluating a spread. Metadata does not establish account fee tiers, custody equivalence, transfer costs, deposit/withdrawal availability, or execution guarantees.

CI uses deterministic fixtures only. The methods make public GET requests only; no API keys, account endpoints, orders, balances, transfers or withdrawals are used.
