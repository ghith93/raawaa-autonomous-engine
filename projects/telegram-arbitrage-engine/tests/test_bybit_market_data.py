import json
from decimal import Decimal

import pytest

from arb_engine.market_sources.bybit import (
    BybitMarketDataError,
    BybitPublicMarketData,
)


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def book_payload(**overrides):
    result = {
        "s": "BTCUSDT",
        "b": [["65000.10", "1.25"], ["65000.00", "0.50"]],
        "a": [["65000.20", "0.75"], ["65001.00", "2.0"]],
        "ts": 1793628000000,
        "u": 12345,
        "seq": 98765,
        "cts": 1793627999990,
    }
    result.update(overrides)
    return {"retCode": 0, "retMsg": "OK", "result": result}


def client_with(payload, **kwargs):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return encoded(payload)

    return BybitPublicMarketData(
        transport=transport, min_interval_seconds=0, **kwargs,
    ), calls


def test_orderbook_normalizes_exact_decimal_levels_and_preserves_provenance():
    client, calls = client_with(book_payload())
    book = client.fetch_orderbook(category="spot", symbol="BTCUSDT", limit=2)

    assert book.source == "bybit"
    assert book.category == "spot"
    assert book.symbol == "BTCUSDT"
    assert book.bids == (
        (Decimal("65000.10"), Decimal("1.25")),
        (Decimal("65000.00"), Decimal("0.50")),
    )
    assert book.asks[0] == (Decimal("65000.20"), Decimal("0.75"))
    assert book.exchange_timestamp_ms == 1793628000000
    assert book.matching_engine_timestamp_ms == 1793627999990
    assert book.update_id == 12345
    assert book.cross_sequence == 98765
    assert len(calls) == 1
    assert "/v5/market/orderbook?category=spot&symbol=BTCUSDT&limit=2" in calls[0][0]


def test_orderbook_rejects_crossed_unsorted_and_mismatched_data():
    client, _ = client_with(book_payload(b=[["65000", "1"], ["65001", "1"]]))
    with pytest.raises(BybitMarketDataError, match="not sorted"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT")

    client, _ = client_with(book_payload(a=[["64999", "1"], ["65001", "1"]]))
    with pytest.raises(BybitMarketDataError, match="crossed"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT")

    client, _ = client_with(book_payload(s="ETHUSDT"))
    with pytest.raises(BybitMarketDataError, match="did not match"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT")


def test_orderbook_rejects_bad_precision_and_missing_provenance():
    client, _ = client_with(book_payload(b=[["NaN", "1"]]))
    with pytest.raises(BybitMarketDataError, match="invalid bid price"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT")

    client, _ = client_with(book_payload(u=0))
    with pytest.raises(BybitMarketDataError, match="update ID"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT")


def test_tickers_normalize_spot_and_perpetual_market_fields():
    payload = {
        "retCode": 0,
        "retMsg": "OK",
        "time": 1793628000000,
        "result": {
            "category": "linear",
            "list": [{
                "symbol": "BTCUSDT",
                "lastPrice": "65000.10",
                "bid1Price": "65000.00",
                "bid1Size": "1.5",
                "ask1Price": "65000.20",
                "ask1Size": "2.0",
                "volume24h": "1234.5",
                "turnover24h": "80000000",
                "markPrice": "65000.15",
                "indexPrice": "64999.90",
                "openInterest": "2345",
                "fundingRate": "0.0001",
                "basis": "0.15",
            }],
        },
    }
    client, calls = client_with(payload)
    tickers = client.fetch_tickers(category="linear", symbol="BTCUSDT")

    assert len(tickers) == 1
    ticker = tickers[0]
    assert ticker.last_price == Decimal("65000.10")
    assert ticker.bid_price == Decimal("65000.00")
    assert ticker.ask_size == Decimal("2.0")
    assert ticker.funding_rate == Decimal("0.0001")
    assert ticker.mark_price == Decimal("65000.15")
    assert ticker.index_price == Decimal("64999.90")
    assert ticker.open_interest == Decimal("2345")
    assert ticker.exchange_timestamp_ms == 1793628000000
    assert "category=linear&symbol=BTCUSDT" in calls[0][0]


def test_ticker_symbol_mismatch_and_unsuccessful_api_response_fail_closed():
    payload = {
        "retCode": 0,
        "time": 1793628000000,
        "result": {"list": [{"symbol": "ETHUSDT", "lastPrice": "1"}]},
    }
    client, _ = client_with(payload)
    with pytest.raises(BybitMarketDataError, match="did not match"):
        client.fetch_tickers(category="spot", symbol="BTCUSDT")

    client, _ = client_with({"retCode": 10006, "retMsg": "Too many visits!"})
    with pytest.raises(BybitMarketDataError, match="unsuccessful"):
        client.fetch_tickers(category="spot", symbol="BTCUSDT")


def test_category_symbol_and_depth_validation_happens_before_network():
    client, calls = client_with(book_payload())
    with pytest.raises(ValueError, match="category"):
        client.fetch_orderbook(category="ethereum", symbol="BTCUSDT")
    with pytest.raises(ValueError, match="uppercase"):
        client.fetch_orderbook(category="spot", symbol="btcusdt")
    with pytest.raises(ValueError, match="between 1 and 25"):
        client.fetch_orderbook(category="option", symbol="BTC-30DEC26-100000-C", limit=26)
    with pytest.raises(ValueError, match="between 1 and 1000"):
        client.fetch_orderbook(category="spot", symbol="BTCUSDT", limit=1001)
    assert calls == []


def test_api_host_is_allowlisted_and_https_only():
    for url in (
        "http://api.bybit.com",
        "https://attacker.example",
        "https://api.bybit.com?token=secret",
        "https://user:pass@api.bybit.com",
        "https://api.bybit.com/path",
    ):
        with pytest.raises(ValueError, match="allowlisted HTTPS"):
            BybitPublicMarketData(base_url=url)


def test_rate_limit_serializes_requests_at_a_conservative_one_per_second():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    payload = {"retCode": 0, "time": 1793628000000, "result": {"list": []}}
    client, calls = client_with(
        payload,
        min_interval_seconds=1.0,
        clock=lambda: current[0],
        sleeper=sleep,
    )
    client.fetch_tickers(category="spot")
    client.fetch_tickers(category="spot")

    assert len(calls) == 2
    assert sleeps == [1.0]


def test_oversized_or_malformed_responses_fail_closed():
    client = BybitPublicMarketData(
        transport=lambda _url, _timeout: b"x" * 1_000_001,
        min_interval_seconds=0,
    )
    with pytest.raises(BybitMarketDataError, match="oversized"):
        client.fetch_tickers(category="spot")

    client = BybitPublicMarketData(
        transport=lambda _url, _timeout: b"{not-json",
        min_interval_seconds=0,
    )
    with pytest.raises(BybitMarketDataError, match="malformed JSON"):
        client.fetch_tickers(category="spot")
