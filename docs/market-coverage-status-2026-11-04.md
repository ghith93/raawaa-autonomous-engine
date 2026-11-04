# #175 status evaluation — 2026-11-04

An honest accounting of the multi-market research epic against its own acceptance
criteria, written because the issue text and the code had drifted in opposite
directions: the issue reads as though no adapters exist, and
`docs/market-coverage-research.md` still says it "does not claim that Binance,
Bybit, Jupiter or additional EVM networks are integrated". The code contains
adapters for all of them. Neither statement describes the repository.

Method: read the code and tests, run the project's suite, and cross-check the
venue registry's own claims. **No live endpoint was called** for this
evaluation. That is deliberate — every number below comes from code, fixtures,
and the registry's own `source_status` field.

Head evaluated: `e84c282` (comparator increment: `ed24d24`). Project suite:
**508 tests, all passing**
(`cd projects/telegram-arbitrage-engine && PYTHONPATH=src python3 -m pytest`).

## Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Ethereum-specific assumptions isolated to Ethereum V2/V3 adapters, not global models | **Verified** | `rpc.py` takes `expected_chain_id` (line 73) and refuses a chain whose `eth_chainId` disagrees (line 212); V2 and V3 live in separate modules (`v2.py`, `onchain_v2_scan.py` vs `v3_math.py`, `sqrt_price_math.py`, `tick_bitmap.py`, `v3_rpc.py`); the registry carries 14 venues across 7 chains / chain IDs (1, 10, 56, 137, 8453, 42161, 43114) |
| At least two independently implemented market families queryable read-only through a common interface, deterministic fixtures in CI | **Partially verified** | Families exist and are independent: CEX spot (`market_sources/binance.py`, `bybit.py`, `okx.py`, `kraken.py`, `coinbase.py`, all tagged `market_family="cex_spot"`), Solana aggregator (`market_sources/jupiter.py`, quote-only, keyless 0.5 RPS enforced), perpetual funding (`market_sources/funding.py`, `market_family="perpetual"`), EVM on-chain V2/V3. The **shared contract is partial**: `opportunity_costs.py` normalises four families (`cex_spot`, `cex_perp`, `evm_amm`, `solana_aggregator`), but the comparator `market_sources/cross_venue.py` refuses any family other than `cex_spot` (line 41) |
| Tests cover chain/venue/token identity collisions, stale/malformed data, precision, rate limits, partial source failure, cost decomposition | **Verified** | `test_cross_venue.py`: `test_mismatched_canonical_asset_or_quote_currency_blocks_symbol_match`, `test_missing_explicit_instrument_mapping_is_not_inferred_from_symbol`, `test_multi_venue_comparison_never_equates_coinbase_usd_with_usdt`, `test_stale_observation_blocks_net_profit_estimate`, `test_multi_venue_comparison_preserves_partial_sources_and_skips_identity_collisions`. `test_opportunity_costs.py`: `test_unknown_fees_or_slippage_are_never_treated_as_zero`, `test_float_costs_naive_timestamps_and_invalid_flags_are_rejected`, `test_exact_depth_swept_gross_profit_override_preserves_quote_precision`. `test_jupiter_quote_only.py`: `test_quote_enforces_keyless_rate_limit_of_at_most_half_request_per_second`. `test_instrument_metadata.py`: lot/price-filter precision and duplicate-mapping rejection |
| No strategy labelled net-profitable unless every required cost and execution constraint is evidenced | **Verified** | `opportunity_costs.py` requires gas + priority fee for `evm_amm`, network + priority fees for `solana_aggregator`, funding for `cex_perp`, bridge + settlement when `cross_chain`; `test_cross_venue.py::test_insufficient_depth_on_either_venue_blocks_profit_label`, `test_opportunity_costs.py::test_partial_depth_blocks_net_profit_even_when_costs_are_known` |
| Exact-head Engine CI and Telegram Arbitrage Engine CI pass | **Verified at the last touched head** | Engine CI green on `e84c282`; the arbitrage workflow last ran green on `main` at `ba7abec6` (2026-11-03) and the project has not changed since. The suite was re-run locally for this evaluation |
| Zero-spend, dry-run and no-external-write gates unchanged | **Verified** | No provider inference, no signing, no order placement in any adapter; Jupiter's transaction path is refused by test (`test_quote_only_request_uses_exact_mints_and_never_requests_transaction`); the registry marks all 14 venues `source_documented_not_live_verified` |

## Implementation sequence

| Step | Status |
|---|---|
| A — inventory scanner, registry, RPC, market clients, schemas | Done (this document is the inventory) |
| B — verified coverage matrix with docs, licences, limits, terms | Done for the sources in use: `docs/market-coverage-research.md`, `venue_registry.json` with per-venue `deployment_source` / `fee_source` |
| C — select one lowest-cost/highest-coverage adapter | Done, and overshot: five CEX adapters, Jupiter, and on-chain V2/V3 rather than one |
| D — adapter behind flags with deterministic tests | Done for the sources above |
| E — cross-adapter normalisation, identity and freshness tests, route comparators | **Partial**: normalisation and cost decomposition exist; the comparator is CEX-only |
| F — multi-network strategy layer after source correctness | **Not started** (see gaps) |
| G — docs and exact-head CI evidence | This document |

## Gaps, stated plainly

1. ~~**No cross-family comparator.**~~ **Closed 2026-11-04** by
   `market_sources/multi_family.py` (`compare_legs`) with 12 fixture tests: a leg
   from any of the four priced families can now sit on either side of a
   comparison, with identity, freshness, skew, depth, data-type and full-cost
   gates preserved. `cross_venue.py` still governs CEX-only order-book sweeps and
   is unchanged. What remains open here: the comparison takes caller-supplied
   legs, so nothing yet wires a DEX or Solana adapter's output into it
   automatically — that is the next increment, and it needs no new source.
2. **No triangular/cyclic enumeration.** Nothing in `src/` implements it; the term appears only in docs. The cost model could price the legs, but the route enumeration does not exist.
3. **No derivatives strategy module.** Funding data is ingested and normalised (`funding.py`); nothing computes basis, carry or spot-perp convergence from it.
4. **No cross-chain comparator.** The cost model has a `cross_chain` flag with bridge/settlement requirements; no code builds such a comparison.
5. **No evaluation harness (Track E).** No snapshot capture, replay, stale-data measurement or false-positive rate.
6. **Every source is unverified live.** All 14 registry venues are `source_documented_not_live_verified`, and no live call has been made — by design under the zero-spend policy, but it means "read-only queryable" is proven against fixtures, not against the networks.

## Recommended next increment

Not another adapter — the adapter layer is already wider than its verification.
The cheapest gap that changes what the system *is*:

1. **Cross-family comparator** (gap 1): generalise `cross_venue.py` to accept any pair of legs that `opportunity_costs.py` can price, keeping every identity, freshness, depth and cost gate. This turns a set of adapters into a research system, and it is where most of the existing tests already point.
2. Then **triangular enumeration inside one venue** (gap 2), which reuses the same cost model and needs no new data source.
3. Then **evaluation harness** (gap 5), because without it no claim about strategy quality can be falsified.

## Non-claims

- No strategy is net-profitable. Gross spread is reported as gross spread.
- No endpoint was called for this document; nothing here is evidence of live data quality, rate-limit behaviour in production, or regional availability.
- Nothing here authorises deployment, provider activation, spend, or execution. The engine's dry-run and zero-spend gates are unchanged.


## Cross-family comparator depth correction — 2026-11-04

The first multi-family comparator increment exposed a false-positive path: an undeclared `max_base_quantity` was converted to a 100% fill fraction. The evaluator now treats a missing depth fraction as unknown and returns `depth_unverified`; it never emits `net_profit_quote` for that comparison. The comparison evidence records buy- and sell-side depth separately, and the aggregate depth basis distinguishes fully quoted, partially declared, and undeclared depth. A net-positive candidate requires explicit depth evidence for both legs. Regression fixtures cover unknown buy depth, unknown sell depth, both unknown, and both declared, using a positive mathematical spread and complete costs. This remains a research estimate, not proof of execution or profitability.
