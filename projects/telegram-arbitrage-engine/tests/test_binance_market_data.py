import json
from datetime import timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.binance import (
    BinanceMarketDataError,
    BinancePublicMarketData,
)


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def orderbook_payload(**overrides):
    payload = {
        "lastUpdateId": 123456789,
        "bids": [["65000.10", "1.25"], ["65000.00", "0.50"]],
        "asks": [["65000.20", "0.75"], ["65001.00", "2.0"]],
    }
    payload.update(overrides)
    return payload


def make_client(payload, **kwargs):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return encoded(payload)

    kwargs.setdefault("min_interval_seconds", 1.0)
    return BinancePublicMarketData(transport=transport, **kwargs), calls


def test_orderbook_preserves_exact_decimal_levels_and_update_id():
    client, calls = make_client(orderbook_payload())
    book = client.fetch_orderbook(symbol="BTCUSDT", limit=50)

    assert book.source == "binance"
    assert book.symbol == "BTCUSDT"
    assert book.bids == (
        (Decimal("65000.10"), Decimal("1.25")),
        (Decimal("65000.00"), Decimal("0.50")),
    )
    assert book.asks[0] == (Decimal("65000.20"), Decimal("0.75"))
    assert book.last_update_id == 123456789
    assert book.exchange_timestamp_ms is None
    assert book.fetched_at.tzinfo == timezone.utc
    assert len(calls) == 1
    parsed = urlsplit(calls[0][0])
    assert parsed.netloc == "data-api.binance.vision"
    assert parsed.path == "/api/v3/depth"
    assert parse_qs(parsed.query) == {"symbol": ["BTCUSDT"], "limit": ["50"]}


def test_orderbook_rejects_crossed_unsorted_and_invalid_levels():
    client, _ = make_client(orderbook_payload(bids=[["65000", "1"], ["65001", "1"]]))
    with pytest.raises(BinanceMarketDataError, match="not sorted"):
        client.fetch_orderbook(symbol="BTCUSDT")

    client, _ = make_client(orderbook_payload(asks=[["64999", "1"], ["65001", "1"]]))
    with pytest.raises(BinanceMarketDataError, match="crossed"):
        client.fetch_orderbook(symbol="BTCUSDT")

    client, _ = make_client(orderbook_payload(bids=[["NaN", "1"]]))
    with pytest.raises(BinanceMarketDataError, match="invalid bid price"):
        client.fetch_orderbook(symbol="BTCUSDT")


def test_book_ticker_normalizes_best_bid_and_ask():
    client, calls = make_client({
        "symbol": "BTCUSDT",
        "bidPrice": "65000.00",
        "bidQty": "1.5",
        "askPrice": "65000.20",
        "askQty": "2.0",
    })
    ticker = client.fetch_book_ticker(symbol="BTCUSDT")
    assert ticker.bid_price == Decimal("65000.00")
    assert ticker.bid_size == Decimal("1.5")
    assert ticker.ask_price == Decimal("65000.20")
    assert ticker.ask_size == Decimal("2.0")
    assert ticker.exchange_timestamp_ms is None
    assert "/api/v3/ticker/bookTicker?symbol=BTCUSDT" in calls[0][0]


def test_book_ticker_rejects_symbol_mismatch_and_invalid_prices():
    client, _ = make_client({
        "symbol": "ETHUSDT", "bidPrice": "1", "bidQty": "1",
        "askPrice": "2", "askQty": "1",
    })
    with pytest.raises(BinanceMarketDataError, match="symbol did not match"):
        client.fetch_book_ticker(symbol="BTCUSDT")

    client, _ = make_client({
        "symbol": "BTCUSDT", "bidPrice": "NaN", "bidQty": "1",
        "askPrice": "2", "askQty": "1",
    })
    with pytest.raises(BinanceMarketDataError, match="bidPrice"):
        client.fetch_book_ticker(symbol="BTCUSDT")


def test_symbol_and_depth_limits_are_validated_before_network():
    client, calls = make_client(orderbook_payload())
    with pytest.raises(ValueError, match="uppercase ASCII"):
        client.fetch_orderbook(symbol="btcusdt")
    with pytest.raises(ValueError, match="one of 5, 10, 20, 50 or 100"):
        client.fetch_orderbook(symbol="BTCUSDT", limit=500)
    assert calls == []


def test_api_host_is_allowlisted_and_https_only():
    for url in (
        "http://data-api.binance.vision",
        "https://api.binance.com",
        "https://attacker.example",
        "https://data-api.binance.vision?token=secret",
        "https://user:pass@data-api.binance.vision",
        "https://data-api.binance.vision/path",
    ):
        with pytest.raises(ValueError, match="market-data-only HTTPS"):
            BinancePublicMarketData(base_url=url)


def test_conservative_rate_limit_is_one_request_per_second():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    client, calls = make_client(
        {
            "symbol": "BTCUSDT",
            "bidPrice": "65000.00",
            "bidQty": "1.0",
            "askPrice": "65000.20",
            "askQty": "1.0",
        },
        clock=lambda: current[0],
        sleeper=sleep,
    )
    client.fetch_book_ticker(symbol="BTCUSDT")
    client.fetch_book_ticker(symbol="BTCUSDT")
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_error_payload_malformed_and_oversized_responses_fail_closed():
    client, _ = make_client({"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(BinanceMarketDataError, match="error payload"):
        client.fetch_book_ticker(symbol="BTCUSDT")

    client = BinancePublicMarketData(
        transport=lambda _url, _timeout: b"{not-json",
        min_interval_seconds=1,
    )
    with pytest.raises(BinanceMarketDataError, match="malformed JSON"):
        client.fetch_book_ticker(symbol="BTCUSDT")

    client = BinancePublicMarketData(
        transport=lambda _url, _timeout: b"x" * 1_000_001,
        min_interval_seconds=1,
    )
    with pytest.raises(BinanceMarketDataError, match="oversized"):
        client.fetch_book_ticker(symbol="BTCUSDT")
