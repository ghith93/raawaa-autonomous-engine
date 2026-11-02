from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_engine.opportunity_costs import (
    OpportunityCosts,
    OpportunityEvaluationError,
    evaluate_two_leg_spread,
)


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
BASE = "asset:canonical:ETH"
QUOTE = "currency:canonical:USD"


def cex_costs(**overrides):
    values = {
        "buy_fee_bps": "10",
        "sell_fee_bps": "10",
        "buy_slippage_bps": "5",
        "sell_slippage_bps": "5",
        "latency_cost_bps": "2",
    }
    values.update(overrides)
    return OpportunityCosts(**values)


def evaluate(**overrides):
    args = {
        "buy_price_quote_per_base": "100",
        "sell_price_quote_per_base": "102",
        "notional_quote": "1000",
        "base_asset_id_buy": BASE,
        "base_asset_id_sell": BASE,
        "quote_currency_id_buy": QUOTE,
        "quote_currency_id_sell": QUOTE,
        "buy_market_family": "cex_spot",
        "sell_market_family": "cex_spot",
        "buy_fetched_at": NOW - timedelta(seconds=1),
        "sell_fetched_at": NOW - timedelta(seconds=1),
        "costs": cex_costs(),
        "now": NOW,
    }
    args.update(overrides)
    return evaluate_two_leg_spread(**args)


def test_cex_spot_calculates_net_profit_only_after_all_required_costs():
    result = evaluate()
    assert result.status == "net_positive_estimate"
    assert result.gross_spread_bps == Decimal("200")
    assert result.gross_profit_quote == Decimal("20")
    assert result.known_cost_quote == Decimal("3.2")
    assert result.net_profit_quote == Decimal("16.8")
    assert result.missing_required_costs == ()
    assert result.execution_verified is False


def test_unknown_fees_or_slippage_are_never_treated_as_zero():
    result = evaluate(costs=OpportunityCosts(
        buy_fee_bps="0",
        sell_fee_bps=None,
        buy_slippage_bps="0",
        sell_slippage_bps="0",
        latency_cost_bps="0",
    ))
    assert result.status == "incomplete_costs"
    assert result.gross_profit_quote == Decimal("20")
    assert result.net_profit_quote is None
    assert result.missing_required_costs == ("sell_fee_bps",)


def test_evm_dex_leg_requires_gas_and_priority_fee():
    result = evaluate(
        buy_market_family="evm_amm",
        costs=cex_costs(),
    )
    assert result.status == "incomplete_costs"
    assert set(result.missing_required_costs) == {"gas_cost_quote", "priority_fee_quote"}
    assert result.net_profit_quote is None

    result = evaluate(
        buy_market_family="evm_amm",
        costs=cex_costs(gas_cost_quote="0.5", priority_fee_quote="0.1"),
    )
    assert result.status == "net_positive_estimate"
    assert result.known_cost_quote == Decimal("3.8")
    assert result.net_profit_quote == Decimal("16.2")


def test_solana_aggregator_leg_requires_network_and_priority_fees():
    result = evaluate(
        buy_market_family="solana_aggregator",
        costs=cex_costs(),
    )
    assert set(result.missing_required_costs) == {"network_fee_quote", "priority_fee_quote"}
    assert result.net_profit_quote is None

    result = evaluate(
        buy_market_family="solana_aggregator",
        costs=cex_costs(network_fee_quote="0.01", priority_fee_quote="0.02"),
    )
    assert result.status == "net_positive_estimate"


def test_perpetual_leg_requires_funding_cost():
    result = evaluate(sell_market_family="cex_perp", costs=cex_costs())
    assert result.missing_required_costs == ("funding_cost_quote",)
    assert result.net_profit_quote is None


def test_cross_chain_requires_bridge_and_settlement_costs():
    result = evaluate(cross_chain=True, costs=cex_costs())
    assert set(result.missing_required_costs) == {"bridge_cost_quote", "settlement_cost_quote"}
    assert result.net_profit_quote is None

    result = evaluate(
        cross_chain=True,
        costs=cex_costs(bridge_cost_quote="1.25", settlement_cost_quote="0.25"),
    )
    assert result.status == "net_positive_estimate"
    assert result.net_profit_quote == Decimal("15.3")


def test_borrowed_capital_requires_explicit_borrow_cost():
    result = evaluate(uses_borrow=True, costs=cex_costs())
    assert result.missing_required_costs == ("borrow_cost_quote",)
    assert result.net_profit_quote is None


def test_asset_or_quote_currency_identity_mismatch_blocks_comparison():
    result = evaluate(base_asset_id_sell="asset:other:ETH")
    assert result.status == "asset_or_quote_identity_mismatch"
    assert result.gross_profit_quote is None
    assert result.net_profit_quote is None

    result = evaluate(quote_currency_id_sell="currency:USDT")
    assert result.status == "asset_or_quote_identity_mismatch"
    assert result.net_profit_quote is None


def test_stale_or_future_dated_quotes_block_net_profit_label():
    result = evaluate(buy_fetched_at=NOW - timedelta(seconds=6))
    assert result.status == "stale_data"
    assert result.net_profit_quote is None

    result = evaluate(sell_fetched_at=NOW + timedelta(seconds=3))
    assert result.status == "stale_data"
    assert result.net_profit_quote is None


def test_partial_depth_blocks_net_profit_even_when_costs_are_known():
    result = evaluate(buy_fill_fraction="0.95")
    assert result.status == "insufficient_depth"
    assert result.gross_profit_quote == Decimal("20")
    assert result.net_profit_quote is None


def test_negative_gross_spread_is_not_relabelled_as_opportunity():
    result = evaluate(sell_price_quote_per_base="99")
    assert result.status == "net_nonpositive"
    assert result.gross_profit_quote == Decimal("-10")
    assert result.net_profit_quote < 0


def test_float_costs_naive_timestamps_and_invalid_flags_are_rejected():
    with pytest.raises(OpportunityEvaluationError, match="not float"):
        evaluate(costs=cex_costs(buy_fee_bps=0.1))
    with pytest.raises(OpportunityEvaluationError, match="timezone-aware"):
        evaluate(buy_fetched_at=datetime(2026, 11, 2, 12, 0))
    with pytest.raises(OpportunityEvaluationError, match="max_quote_age_seconds"):
        evaluate(max_quote_age_seconds=0)
    with pytest.raises(OpportunityEvaluationError, match="fill fractions"):
        evaluate(sell_fill_fraction="1.1")
