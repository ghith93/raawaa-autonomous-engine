# Multi-family comparison

`market_sources/cross_venue.py` compares CEX spot order books with each other.
Until this module existed that was the only comparison the codebase could
express, even though `opportunity_costs.py` could already price four market
families.

`market_sources/multi_family.py` closes that gap without pretending the families
are interchangeable.

## What it accepts

One `LegQuote` per side. A leg is a value, not a client: it carries what a source
actually reported, and nothing fetches.

| Field | Why it exists |
|---|---|
| `market_family` | one of `cex_spot`, `cex_perp`, `evm_amm`, `solana_aggregator` — the same set the cost model prices |
| `venue_id`, `instrument_id` | the physical source, for evidence |
| `base_asset_id`, `quote_currency_id` | canonical IDs, never symbols; identity is compared on these |
| `price_quote_per_base` | `Decimal`/string only — a float is refused, because a float cannot hold a price |
| `observed_at` | timezone-aware; freshness and skew are judged from it |
| `data_type` | `order_book`, `executable_quote`, `on_chain_state` or `indicative_quote` |
| `provenance` | where the number came from, carried into the result |
| `max_base_quantity` | how much base the price actually covers; `None` means the source made **no depth claim** |

## What it refuses

- **Same venue and instrument on both sides** — that is not a spread
  (`MultiFamilyError`).
- **Identity by symbol.** Different canonical base or quote → status
  `asset_or_quote_identity_mismatch`, no numbers.
- **Unknown costs as zero.** The evaluator demands network + priority fees for a
  `solana_aggregator` leg, gas + priority for `evm_amm`, funding for `cex_perp`;
  anything missing yields `incomplete_costs` and no net figure.
- **A profit label from indicative data.** If either leg is `indicative_quote`,
  a positive result is labelled `indicative_spread_only` — a price nobody
  committed to is not a profit.
- **Depth it never quoted.** A leg that covers less than the requested size
  reports `insufficient_depth` with the computed fill fraction, rather than
  pricing the whole size at the quoted price.

## What it returns

`MultiFamilyComparison` carries the underlying `OpportunityEvaluation`, the two
legs, the requested base quantity, both fill fractions and the evidence tuple
(family, data type and provenance per side, plus whether depth was declared).
`label == "net_positive_candidate"` only when the evaluator returned
`net_positive_estimate` **and** neither side was indicative — `is_net_profit_claim`
is the single boolean anything downstream should branch on.

## What it still does not do

- No live calls. Every leg comes from a caller who already fetched it; the
  adapters in this project are unchanged.
- No triangular/cyclic enumeration, no cross-chain comparator (the `cross_chain`
  flag only *requires* bridge and settlement costs), no evaluation harness.
- No execution. Nothing here signs, submits, or holds a position.
- No claim about production D1, live venues, or real fills. All tests are
  deterministic fixtures.
