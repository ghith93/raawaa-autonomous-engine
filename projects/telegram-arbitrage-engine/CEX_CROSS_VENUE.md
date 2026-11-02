CEX cross-venue spot depth scanner

Status: deterministic read-only comparison component, now exposed through an operator-invoked bounded CLI. It is not connected to a scheduler or live strategy loop.

Purpose

Compare both directions between two centralized-exchange spot order books. The scanner sweeps asks on the buy venue and bids on the sell venue for the same requested base quantity, rather than comparing only top-of-book prices. It passes complete fills through the conservative opportunity cost evaluator.

Explicit instrument identity

A caller must register each venue/instrument pair in an InstrumentRegistry with a canonical base-asset ID, canonical quote-currency ID, venue base quantity step, optional minimum base quantity and minimum quote notional, and cex_spot market family. InstrumentRegistry.from_metadata propagates these constraints from supported public instrument metadata. Matching symbols are not proof of matching assets. Unknown instruments are rejected, and comparisons are blocked when canonical base or quote IDs differ. No implicit ETH/WETH, BTC/WBTC, USDT/USDC or cross-chain equivalence is assumed.



Multi-venue comparison and partial source coverage

`compare_cex_spot_orderbook_universe` accepts two to eight available order-book snapshots from different CEX spot venues and compares every venue pair in both directions. It uses the same InstrumentRegistry and cost evaluator as the two-venue function; no venue-specific symbol conversion is inferred. Binance, Bybit, Kraken, Coinbase and OKX snapshots satisfy the shared read-only book contract (`source`, `symbol`, `bids`, `asks`, timezone-aware `fetched_at`, optional `category`).

The result includes:
- every valid pairwise direction as a `CrossVenueOpportunity`;
- `skipped_pairs` for pairs whose explicit canonical base/quote IDs differ;
- bounded `source_failures` codes supplied by the caller, so one unavailable exchange does not erase valid comparisons from other sources;
- `status=complete`, `partial`, or `insufficient_sources` and the number of compared venue pairs.

The caller owns bounded/concurrent fetching and must pass only sanitized failure codes (not raw exception text or credentials). A maximum of eight total venues is enforced to bound pairwise work. Each venue's rate limiter remains independent and shared by its adapter instances. This function performs no network calls and does not itself schedule or execute scans.

Depth and cost behavior

- Buy leg consumes asks until the requested base quantity is filled; exact Decimal VWAP and quote spent are calculated.
- Sell leg consumes bids for the same base quantity; exact Decimal VWAP and quote received are calculated.
- If either book cannot fill the full quantity, status is insufficient_depth and no gross or net profit is reported for that direction.
- If the requested base quantity is below either venue's minimum quantity, status is below_minimum_order_quantity and no net estimate is produced.
- If buy quote spend or sell quote proceeds is below the corresponding venue's minimum quote notional, status is below_minimum_order_notional and no net estimate is produced.
- Freshness uses each adapter's timezone-aware local fetched_at; maximum age defaults to five seconds.
- Venue fees and latency cost must be supplied for both legs. Depth-swept VWAP already incorporates book-walk price impact, so separate slippage bps should be zero only when the full-depth sweep is the evidence for slippage; otherwise supply an additional conservative estimate.
- Missing fees or latency assumptions leave net_profit_quote unset. The scanner never assumes zero fees.

This component does not fetch account-specific fee tiers, instrument metadata, deposits/withdrawals, transfer time, custody risk or execute orders. Canonical asset IDs and fee profiles must be independently verified. Even a positive result is an estimate with execution_verified=false.

Operator-invoked read-only CLI

The arb-engine cex-scan command fetches public spot instrument metadata and order books from two to four selected venues (Binance, Bybit, Coinbase Exchange, OKX) concurrently. Each venue must be passed as an exact venue_id=instrument_id pair. The caller must also supply exact uppercase base/quote asset codes, explicit canonical asset IDs, and a requested base quantity.

The CLI rejects metadata that does not exactly match the requested base/quote codes or whose status is not TRADING. It does not infer aliases such as Kraken XBT/BTC, USD/USDT, USDT/USDC, or BTC/WBTC. Kraken remains available as a lower-level adapter but is not included in this first orchestration path because its canonical pair/asset aliases need an explicit reviewed mapping layer.

Fetches are bounded to four concurrent venue workers and each adapter retains its own request pacing. A source failure is sanitized and retained as partial coverage; two successful compatible books are required to compare. The CLI intentionally supplies no fee profile, so the evaluator must leave net profit unknown and return incomplete_costs when depth and freshness pass. This is not a scheduler, execution engine, account-aware quote, or profitability signal.
