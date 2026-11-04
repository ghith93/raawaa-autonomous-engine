from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_engine.market_sources.multi_family import (
    LegQuote,
    MultiFamilyError,
    compare_legs,
)
from arb_engine.opportunity_costs import OpportunityCosts

NOW = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)
BTC = "asset:btc"
USDT = "asset:usdt"  # canonical ids, never symbols


def leg(
    *,
    family="cex_spot",
    venue="binance",
    instrument="BTCUSDT",
    price="60000",
    data_type="order_book",
    observed_at=None,
    max_base_quantity=None,
    base=BTC,
    quote=USDT,
):
    return LegQuote(
        market_family=family,
        venue_id=venue,
        instrument_id=instrument,
        base_asset_id=base,
        quote_currency_id=quote,
        price_quote_per_base=Decimal(price),
        observed_at=observed_at or NOW,
        data_type=data_type,
        provenance=f"{venue}:fixture",
        max_base_quantity=None if max_base_quantity is None else Decimal(max_base_quantity),
    )


FULL_COSTS = OpportunityCosts(
    buy_fee_bps="10", sell_fee_bps="10",
    buy_slippage_bps="2", sell_slippage_bps="2",
    latency_cost_bps="1",
)


def test_cex_against_solana_aggregator_with_full_costs_reaches_a_net_label():
    # The comparison the codebase could not previously express: an order book on
    # one side, an aggregator quote on the other, priced by the same evaluator.
    costs = OpportunityCosts(
        buy_fee_bps="10", sell_fee_bps="10",
        buy_slippage_bps="2", sell_slippage_bps="2", latency_cost_bps="1",
        network_fee_quote="0.01", priority_fee_quote="0.005",
    )
    result = compare_legs(
        buy=leg(price="60000", venue="binance", max_base_quantity="1"),
        sell=leg(family="solana_aggregator", venue="jupiter", instrument="btc-mint/usdc-mint",
                 price="60900", data_type="executable_quote", max_base_quantity="1"),
        notional_quote="600",
        costs=costs,
        now=NOW,
    )
    assert result.status == "net_positive_estimate"
    assert result.label == "net_positive_candidate"
    assert result.is_net_profit_claim
    assert result.evaluation.net_profit_quote > 0
    kinds = dict(result.evidence)
    assert kinds["buy_family"] == "cex_spot"
    assert kinds["sell_family"] == "solana_aggregator"


def test_a_solana_leg_without_network_fees_is_not_profitable():
    # The evaluator asks for network + priority fees whenever a
    # solana_aggregator leg is present; silence is not zero.
    result = compare_legs(
        buy=leg(price="60000"),
        sell=leg(family="solana_aggregator", venue="jupiter", instrument="mint-a/mint-b", price="60900"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert result.status == "incomplete_costs"
    assert "network_fee_quote" in result.evaluation.missing_required_costs
    assert not result.is_net_profit_claim


def test_an_evm_leg_requires_gas_and_priority_fees():
    result = compare_legs(
        buy=leg(family="evm_amm", venue="uniswap_v2_ethereum", instrument="pool-1",
                price="60000", data_type="on_chain_state"),
        sell=leg(price="60900"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert result.status == "incomplete_costs"
    assert set(result.evaluation.missing_required_costs) == {"gas_cost_quote", "priority_fee_quote"}


def test_an_indicative_leg_caps_the_label_even_when_the_math_is_positive():
    # An aggregator route nobody committed to can be a spread, but not a profit.
    costs = OpportunityCosts(
        buy_fee_bps="10", sell_fee_bps="10",
        buy_slippage_bps="2", sell_slippage_bps="2", latency_cost_bps="1",
        network_fee_quote="0.01", priority_fee_quote="0.005",
    )
    result = compare_legs(
        buy=leg(price="60000", max_base_quantity="1"),
        sell=leg(family="solana_aggregator", venue="jupiter", instrument="mint-a/mint-b",
                 price="60900", data_type="indicative_quote", max_base_quantity="1"),
        notional_quote="600", costs=costs, now=NOW,
    )
    assert result.evaluation.status == "net_positive_estimate"
    assert result.label == "indicative_spread_only"
    assert not result.is_net_profit_claim


def test_canonical_identity_mismatch_blocks_the_comparison():
    result = compare_legs(
        buy=leg(base="asset:btc"),
        sell=leg(base="asset:wbtc", venue="bybit"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert result.status == "asset_or_quote_identity_mismatch"
    assert not result.is_net_profit_claim


def test_quote_currency_mismatch_blocks_the_comparison():
    result = compare_legs(
        buy=leg(quote="asset:usdt"),
        sell=leg(quote="asset:usd", venue="coinbase"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert result.status == "asset_or_quote_identity_mismatch"


def test_a_leg_that_cannot_fill_the_size_is_insufficient_depth():
    # The quote covers 0.002 BTC; the request is 0.01 BTC.
    result = compare_legs(
        buy=leg(price="60000", max_base_quantity="0.002"),
        sell=leg(price="60900", venue="bybit", max_base_quantity="1"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert result.status == "insufficient_depth"
    assert result.buy_fill_fraction == Decimal("0.2")
    assert not result.is_net_profit_claim


def test_unknown_depth_never_becomes_a_full_fill_or_profit_candidate():
    # Each unknown side is tested against a mathematically positive spread and
    # fully known costs. Only two explicit depth declarations may earn a label.
    buy_unknown = compare_legs(
        buy=leg(price="60000"),
        sell=leg(price="60900", venue="bybit", max_base_quantity="1"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert buy_unknown.status == "depth_unverified"
    assert buy_unknown.buy_fill_fraction is None
    assert buy_unknown.sell_fill_fraction == Decimal("1")
    assert buy_unknown.evaluation.net_profit_quote is None
    assert not buy_unknown.is_net_profit_claim
    assert dict(buy_unknown.evidence)["buy_depth_basis"] == "undeclared"
    assert dict(buy_unknown.evidence)["sell_depth_basis"] == "quoted"
    assert dict(buy_unknown.evidence)["depth_basis"] == "partially_declared"

    sell_unknown = compare_legs(
        buy=leg(price="60000", max_base_quantity="1"),
        sell=leg(price="60900", venue="bybit"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert sell_unknown.status == "depth_unverified"
    assert sell_unknown.buy_fill_fraction == Decimal("1")
    assert sell_unknown.sell_fill_fraction is None
    assert sell_unknown.evaluation.net_profit_quote is None
    assert not sell_unknown.is_net_profit_claim
    assert dict(sell_unknown.evidence)["depth_basis"] == "partially_declared"

    both_unknown = compare_legs(
        buy=leg(price="60000"),
        sell=leg(price="60900", venue="bybit"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert both_unknown.status == "depth_unverified"
    assert both_unknown.buy_fill_fraction is None
    assert both_unknown.sell_fill_fraction is None
    assert both_unknown.evaluation.net_profit_quote is None
    assert not both_unknown.is_net_profit_claim
    assert dict(both_unknown.evidence)["depth_basis"] == "undeclared"

    declared = compare_legs(
        buy=leg(price="60000", max_base_quantity="1"),
        sell=leg(price="60900", venue="bybit", max_base_quantity="1"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert declared.status == "net_positive_estimate"
    assert declared.label == "net_positive_candidate"
    assert declared.evaluation.net_profit_quote > 0
    assert declared.is_net_profit_claim
    assert dict(declared.evidence)["depth_basis"] == "quoted"


def test_stale_and_skewed_quotes_keep_the_evaluator_verdict():
    stale = compare_legs(
        buy=leg(price="60000", observed_at=NOW - timedelta(seconds=60)),
        sell=leg(price="60900", venue="bybit"),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert stale.status == "stale_data"
    skewed = compare_legs(
        buy=leg(price="60000"),
        sell=leg(price="60900", venue="bybit", observed_at=NOW - timedelta(seconds=3)),
        notional_quote="600", costs=FULL_COSTS, now=NOW,
    )
    assert skewed.status == "unsynchronized_data"


def test_the_same_instrument_on_both_sides_is_refused():
    with pytest.raises(MultiFamilyError, match="same venue and instrument"):
        compare_legs(buy=leg(), sell=leg(price="60900"), notional_quote="600",
                     costs=FULL_COSTS, now=NOW)


def test_unknown_family_and_data_type_are_refused_at_construction():
    with pytest.raises(MultiFamilyError, match="unsupported market family"):
        leg(family="dex_whatever")
    with pytest.raises(MultiFamilyError, match="unsupported data type"):
        leg(data_type="vibes")
    with pytest.raises(MultiFamilyError, match="timezone-aware"):
        LegQuote(
            market_family="cex_spot", venue_id="v", instrument_id="i",
            base_asset_id=BTC, quote_currency_id=USDT, price_quote_per_base="1",
            observed_at=datetime(2026, 11, 4, 16, 0), data_type="order_book", provenance="p",
        )


def test_floats_are_rejected_because_they_cannot_hold_exact_prices():
    with pytest.raises(MultiFamilyError, match="never a float"):
        LegQuote(
            market_family="cex_spot", venue_id="v", instrument_id="i",
            base_asset_id=BTC, quote_currency_id=USDT, price_quote_per_base=60000.0,
            observed_at=NOW, data_type="order_book", provenance="p",
        )
