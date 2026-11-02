import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.funding import (
    FundingMarketDataError,
    FundingRateLimited,
    PublicPerpetualFundingClient,
    _RateLimiter,
)


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def client(venue, responses, **kwargs):
    calls = []
    queue = list(responses)

    def transport(url, timeout):
        calls.append((url, timeout))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return encoded(item)

    kwargs.setdefault("rate_limiter", _RateLimiter())
    return PublicPerpetualFundingClient(
        venue_id=venue, transport=transport, **kwargs
    ), calls


def test_binance_usdm_history_preserves_signed_decimal_mark_and_adjusted_interval():
    history = [{
        "symbol": "BTCUSDT",
        "fundingRate": "-0.00012500",
        "fundingTime": 1793620800000,
        "markPrice": "101234.56789012",
        "rateType": "Regular",
    }]
    info = [{"symbol": "BTCUSDT", "fundingIntervalHours": 4}]
    api, calls = client("binance", [history, info])
    rows = api.fetch_funding_history(symbol="BTCUSDT", limit=10)

    assert len(rows) == 1
    item = rows[0]
    assert item.venue_id == "binance"
    assert item.market_family == "perpetual"
    assert item.contract_type == "linear"
    assert item.funding_rate == Decimal("-0.00012500")
    assert item.funding_time_ms == 1793620800000
    assert item.mark_price == Decimal("101234.56789012")
    assert item.funding_interval_hours == 4
    assert item.rate_type == "Regular"
    assert item.fetched_at.tzinfo is not None
    assert urlsplit(calls[0][0]).hostname == "fapi.binance.com"
    assert urlsplit(calls[0][0]).path == "/fapi/v1/fundingRate"
    assert parse_qs(urlsplit(calls[0][0]).query) == {"symbol": ["BTCUSDT"], "limit": ["10"]}
    assert urlsplit(calls[1][0]).path == "/fapi/v1/fundingInfo"


def test_binance_unknown_interval_is_not_filled_with_an_assumed_default():
    history = [{
        "symbol": "ETHUSDT", "fundingRate": "0.0001",
        "fundingTime": 1793620800000, "markPrice": "3000", "rateType": "Regular",
    }]
    api, _ = client("binance", [history, []])
    row = api.fetch_funding_history(symbol="ETHUSDT")[0]
    assert row.funding_interval_hours is None


@pytest.mark.parametrize("category", ["linear", "inverse"])
def test_bybit_funding_history_normalizes_category_and_settlement_timestamp(category):
    payload = {
        "retCode": 0, "retMsg": "OK",
        "result": {
            "category": category,
            "list": [{
                "symbol": "BTCUSDT",
                "fundingRate": "0.00010000",
                "fundingRateTimestamp": "1793620800000",
            }],
        },
    }
    instrument = {
        "retCode": 0, "result": {
            "category": category,
            "list": [{
                "symbol": "BTCUSDT",
                "contractType": "LinearPerpetual" if category == "linear" else "InversePerpetual",
                "status": "Trading",
                "fundingInterval": 480,
            }],
        },
    }
    api, calls = client("bybit", [payload, instrument])
    rows = api.fetch_funding_history(symbol="BTCUSDT", contract_type=category, limit=20)
    assert len(rows) == 1
    assert rows[0].venue_id == "bybit"
    assert rows[0].contract_type == category
    assert rows[0].funding_rate == Decimal("0.00010000")
    assert rows[0].funding_time_ms == 1793620800000
    assert rows[0].mark_price is None
    assert rows[0].funding_interval_hours == 8
    assert parse_qs(urlsplit(calls[0][0]).query) == {
        "category": [category], "symbol": ["BTCUSDT"], "limit": ["20"],
    }
    assert urlsplit(calls[1][0]).path == "/v5/market/instruments-info"


def test_funding_rows_reject_wrong_symbols_duplicate_times_and_malformed_rates():
    api, _ = client("binance", [[{
        "symbol": "ETHUSDT", "fundingRate": "0.001",
        "fundingTime": 1793620800000, "markPrice": "1", "rateType": "Regular",
    }], []])
    with pytest.raises(FundingMarketDataError, match="symbol"):
        api.fetch_funding_history(symbol="BTCUSDT")

    duplicate = [
        {"symbol": "BTCUSDT", "fundingRate": "0.001", "fundingTime": 1793620800000},
        {"symbol": "BTCUSDT", "fundingRate": "0.002", "fundingTime": 1793620800000},
    ]
    api, _ = client("binance", [duplicate, []])
    with pytest.raises(FundingMarketDataError, match="duplicate"):
        api.fetch_funding_history(symbol="BTCUSDT")

    api, _ = client("bybit", [{
        "retCode": 0, "result": {"category": "linear", "list": [{
            "symbol": "BTCUSDT", "fundingRate": "NaN",
            "fundingRateTimestamp": "1793620800000",
        ]},
    }}])
    with pytest.raises(FundingMarketDataError, match="fundingRate"):
        api.fetch_funding_history(symbol="BTCUSDT")


def test_funding_client_rejects_unsupported_symbols_hosts_categories_and_limits():
    with pytest.raises(ValueError, match="official venue"):
        PublicPerpetualFundingClient(venue_id="binance", base_url="https://attacker.example")
    api, calls = client("bybit", [])
    with pytest.raises(ValueError, match="contract_type"):
        api.fetch_funding_history(symbol="BTCUSDT", contract_type="spot")
    with pytest.raises(ValueError, match="uppercase"):
        api.fetch_funding_history(symbol="btcusdt")
    with pytest.raises(ValueError, match="1 and 200"):
        api.fetch_funding_history(symbol="BTCUSDT", limit=201)
    assert calls == []


def test_binance_rejects_inverse_until_coin_margined_adapter_is_implemented():
    api, calls = client("binance", [])
    with pytest.raises(ValueError, match="USD-M linear"):
        api.fetch_funding_history(symbol="BTCUSDT", contract_type="inverse")
    assert calls == []


def test_http_429_defers_shared_limiter_and_does_not_retry_automatically():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = _RateLimiter(clock=lambda: now[0], sleeper=sleep)
    api, _ = client("bybit", [
        FundingRateLimited(9),
        {"retCode": 0, "result": {"category": "linear", "list": []}},
    ], rate_limiter=limiter)
    with pytest.raises(FundingRateLimited):
        api.fetch_funding_history(symbol="BTCUSDT")
    api.fetch_funding_history(symbol="BTCUSDT")
    assert sleeps == [9.0]



def test_bybit_json_rate_limit_code_defers_without_retry():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = _RateLimiter(clock=lambda: now[0], sleeper=sleep)
    api, _ = client("bybit", [
        {"retCode": 10006, "retMsg": "Too many visits", "result": {}},
        {"retCode": 0, "result": {"category": "linear", "list": []}},
        {"retCode": 0, "result": {"category": "linear", "list": [{
            "symbol": "BTCUSDT", "contractType": "LinearPerpetual",
            "status": "Trading", "fundingInterval": 480,
        }]}},
    ], rate_limiter=limiter)
    with pytest.raises(FundingRateLimited):
        api.fetch_funding_history(symbol="BTCUSDT")
    api.fetch_funding_history(symbol="BTCUSDT")
    assert sleeps == [2.0]
