from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_engine.economics import calculate_profit
from arb_engine.models import MarketQuote, Opportunity
from arb_engine.risk import validate_opportunity

D = Decimal


def quote(venue: str, price: str, *, liquidity="100000", age=0, chain="ethereum", base="TOKEN", quote_token="USDC", fee="25", slip="15", gas="0.04"):
    return MarketQuote(venue, chain, base, quote_token, D(price), D(liquidity), D(fee), D(slip), D(gas), datetime.now(timezone.utc) - timedelta(seconds=age))


def opportunity(buy=None, sell=None, capital="50"):
    return Opportunity(D(capital), buy or quote("dex-a", "100"), sell or quote("dex-b", "101.20"))


def test_capital_includes_buy_fee():
    op = opportunity()
    b = calculate_profit(op)
    assert b.buy_notional_usd + b.buy_fee_usd == op.capital_usd
    assert b.buy_notional_usd < op.capital_usd


def test_unprofitable_spread_is_rejected_at_35_bps():
    result = validate_opportunity(opportunity())
    assert not result.accepted
    assert result.breakdown is not None
    assert result.breakdown.net_bps < D("35")


def test_different_chains_rejected():
    result = validate_opportunity(opportunity(sell=quote("dex-b", "102", chain="polygon")))
    assert not result.accepted
    assert "different chains" in result.reason


def test_different_pairs_rejected():
    result = validate_opportunity(opportunity(sell=quote("dex-b", "102", base="OTHER")))
    assert not result.accepted
    assert "same token pair" in result.reason


def test_stale_buy_quote_rejected():
    result = validate_opportunity(opportunity(buy=quote("dex-a", "100", age=31)))
    assert not result.accepted
    assert "stale" in result.reason


def test_future_timestamp_rejected():
    future = MarketQuote("dex-a", "ethereum", "TOKEN", "USDC", D("100"), D("10000"), D("25"), D("15"), D("0.04"), datetime.now(timezone.utc) + timedelta(seconds=3))
    result = validate_opportunity(opportunity(buy=future))
    assert not result.accepted
    assert "future" in result.reason


def test_insufficient_buy_liquidity_rejected():
    result = validate_opportunity(opportunity(buy=quote("dex-a", "100", liquidity="1")))
    assert not result.accepted
    assert "buy-side" in result.reason


def test_insufficient_sell_liquidity_rejected():
    result = validate_opportunity(opportunity(sell=quote("dex-b", "101.2", liquidity="1")))
    assert not result.accepted
    assert "sell-side" in result.reason


@pytest.mark.parametrize("capital", ["0", "-1", "NaN", "Infinity"])
def test_invalid_capital_rejected(capital):
    result = validate_opportunity(opportunity(capital=capital))
    assert not result.accepted


def test_naive_timestamp_rejected():
    q = MarketQuote("dex-a", "ethereum", "TOKEN", "USDC", D("100"), D("1000"), D("25"), D("15"), D("0.04"), datetime.now())
    result = validate_opportunity(opportunity(buy=q))
    assert not result.accepted
    assert "timezone-aware" in result.reason


def test_invalid_price_rejected():
    result = validate_opportunity(opportunity(buy=quote("dex-a", "0")))
    assert not result.accepted
    assert "positive" in result.reason


def test_no_live_execution_surface():
    import arb_engine
    assert not hasattr(arb_engine, "execute_trade")
    assert not hasattr(arb_engine, "sign_transaction")


def test_float_money_values_rejected():
    q = MarketQuote("dex-a", "ethereum", "TOKEN", "USDC", 100.0, D("10000"), D("25"), D("15"), D("0.04"), datetime.now(timezone.utc))
    result = validate_opportunity(opportunity(buy=q))
    assert not result.accepted
    assert "Decimal" in result.reason


def test_float_capital_rejected():
    op = Opportunity(50.0, quote("dex-a", "100"), quote("dex-b", "102"))
    result = validate_opportunity(op)
    assert not result.accepted
    assert "Decimal" in result.reason
