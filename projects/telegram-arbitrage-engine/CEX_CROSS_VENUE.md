CEX cross-venue spot depth scanner

Status: deterministic read-only comparison component with two manual CLI entry points; not connected to a scheduler or live strategy loop.

CLI entry point

`arb-engine cex-scan` composes public metadata and order-book clients for Bybit, Binance, Kraken, Coinbase Exchange and OKX. It fetches one instrument and one bounded Spot book from each selected venue, then calls `compare_cex_spot_orderbooks` for both directions.

The CLI requires:
- Exact venue IDs and instrument IDs (Kraken accepts a pair alias and resolves it through AssetPairs).
- Explicit canonical base-asset and quote-currency IDs for each venue. Different IDs block the comparison even when symbols match.
- One base quantity that conforms to both venues' lot steps and minimum order rules.
- Optional operator-supplied taker fee bps per venue and a combined latency-risk bps assumption. If fees or latency are omitted, the evaluator leaves net_profit_quote unset.
- Freshness/skew limits (defaults: 5-second maximum quote age and 2-second maximum inter-venue skew).

Depth-swept VWAP includes the observed book-walk price impact, so the CLI supplies explicit zero for the separate slippage fields only for that depth-swept calculation. It does not infer account fee tiers, custody/inventory availability, rebalancing, coordinated fills or settlement. A positive net estimate is not executable PnL.

Example:

```bash
arb-engine cex-scan \
  --venue-a bybit --instrument-a BTCUSDT \
  --a-base-asset-id asset:canonical:BTC --a-quote-currency-id currency:canonical:USDT \
  --fee-a-bps 10 \
  --venue-b binance --instrument-b BTCUSDT \
  --b-base-asset-id asset:canonical:BTC --b-quote-currency-id currency:canonical:USDT \
  --fee-b-bps 10 --latency-cost-bps 2 \
  --base-quantity 0.001
```


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


Multi-venue universe CLI

The separate `arb-engine cex-universe-scan` command composes the existing five public adapters with `compare_cex_spot_orderbook_universe`. It fetches two to five venue metadata/order-book pairs concurrently and compares every compatible venue pair in both directions. Repeat `--venue`, `--instrument`, `--base-asset-id`, and `--quote-currency-id` in matching order. Optional `--fee-bps VENUE=BPS` entries supply explicit per-venue taker-fee assumptions; `--latency-cost-bps` is a combined operator-supplied risk cost.

The operator must map each venue's base/quote asset codes to canonical IDs. This permits an explicit base alias such as Kraken XBT -> canonical BTC. Quote asset codes must match exactly across selected sources: USD, USDT and USDC are not converted or treated as interchangeable. If a source uses a different quote code, it is reported as `quote_asset_code_mismatch` and excluded rather than silently joined.

The command fetches at most five sources concurrently, applies each adapter's own process-local pacing, and preserves sanitized source failures. Two compatible books are required; otherwise the result is `insufficient_sources`. Partial coverage remains usable for the healthy pairwise comparisons. Missing fee or latency values leave net estimates unset. No account-specific fee tier, inventory, transfer/settlement, custody, or coordinated-fill capability is inferred. The command is manually invoked and read-only; it is not a scheduled strategy loop.
