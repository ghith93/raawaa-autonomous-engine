import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.coinbase import (
    CoinbaseExchangePublicMarketData,
    CoinbaseMarketDataError,
    CoinbaseRateLimited,
    _RateLimiter,
)
from arb_engine.market_sources.cross_venue import (
    InstrumentRegistry,
    InstrumentSpec,
    compare_cex_spot_orderbooks,
)
from arb_engine.market_sources.binance import BinanceOrderBookSnapshot
from arb_engine.opportunity_costs import OpportunityCosts


BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def product_payload(**overrides):
    item = {
        "id": "BTC-USDT",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "quote_increment": "0.01",
        "base_increment": "0.00000001",
        "base_min_size": "0.0001",
        "min_market_funds": "10",
        "margin_enabled": False,
        "post_only": False,
        "limit_only": False,
        "cancel_only": False,
        "trading_disabled": False,
        "status": "online",
        "status_message": "",
        "auction_mode": False,
    }
    item.update(overrides)
    return item


def book_payload(**overrides):
    item = {
        "sequence": 13051505638,
        "bids": [["101.80", "1.25", 2], ["101.70", "2", 1]],
        "asks": [["101.90", "1.5", 3], ["102.00", "2", 2]],
        "time": "2026-11-02T12:00:00.123Z",
    }
    item.update(overrides)
    return item


def make_client(responses, **kwargs):
    calls = []
    queue = list(responses)

    def transport(url, timeout):
        calls.append((url, timeout))
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return encoded(value) if isinstance(value, dict) else value

    kwargs.setdefault("rate_limiter", _RateLimiter())
    return CoinbaseExchangePublicMarketData(transport=transport, **kwargs), calls


def test_fetch_instrument_preserves_exact_product_precision_and_minimums():
    client, calls = make_client([product_payload()])
    metadata = client.fetch_instrument(product_id="btc-usdt")

    assert metadata.venue_id == "coinbase"
    assert metadata.instrument_id == "BTC-USDT"
    assert metadata.market_family == "cex_spot"
    assert metadata.base_asset_code == "BTC"
    assert metadata.quote_asset_code == "USDT"
    assert metadata.base_step_size == Decimal("0.00000001")
    assert metadata.price_tick_size == Decimal("0.01")
    assert metadata.min_base_quantity == Decimal("0.0001")
    assert metadata.min_quote_notional == Decimal("10")
    assert metadata.status == "TRADING"

    parsed = urlsplit(calls[0][0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.exchange.coinbase.com"
    assert parsed.path == "/products/BTC-USDT"
    assert parsed.query == ""
    assert "api-key" not in calls[0][0].lower()


def test_fetch_instrument_fails_closed_for_mismatch_and_restricted_products():
    client, _ = make_client([product_payload(id="ETH-USDT")])
    with pytest.raises(CoinbaseMarketDataError, match="did not match"):
        client.fetch_instrument(product_id="BTC-USDT")

    client, _ = make_client([product_payload(post_only=True)])
    assert client.fetch_instrument(product_id="BTC-USDT").status == "RESTRICTED"

    client, _ = make_client([product_payload(auction_mode=True)])
    assert client.fetch_instrument(product_id="BTC-USDT").status == "AUCTION"

    client, _ = make_client([product_payload(status="offline")])
    assert client.fetch_instrument(product_id="BTC-USDT").status == "OFFLINE"


def test_fetch_instrument_rejects_missing_precision_and_invalid_flags():
    client, _ = make_client([product_payload(base_currency="")])
    with pytest.raises(CoinbaseMarketDataError, match="base or quote"):
        client.fetch_instrument(product_id="BTC-USDT")

    client, _ = make_client([product_payload(base_increment="0")])
    with pytest.raises(CoinbaseMarketDataError, match="base_increment"):
        client.fetch_instrument(product_id="BTC-USDT")

    client, _ = make_client([product_payload(auction_mode="false")])
    with pytest.raises(CoinbaseMarketDataError, match="product flag"):
        client.fetch_instrument(product_id="BTC-USDT")


def test_fetch_orderbook_preserves_decimal_levels_sequence_and_exchange_time():
    client, calls = make_client([book_payload()])
    book = client.fetch_orderbook(product_id="BTC-USDT", level=2)

    assert book.source == "coinbase"
    assert book.category == "spot"
    assert book.symbol == "BTC-USDT"
    assert book.sequence == 13051505638
    assert book.bids == (
        (Decimal("101.80"), Decimal("1.25")),
        (Decimal("101.70"), Decimal("2")),
    )
    assert book.asks == (
        (Decimal("101.90"), Decimal("1.5")),
        (Decimal("102.00"), Decimal("2")),
    )
    assert book.bid_order_counts == (2, 1)
    assert book.ask_order_counts == (3, 2)
    assert book.exchange_timestamp_ms == 1793620800123
    assert book.fetched_at.tzinfo is not None

    parsed = urlsplit(calls[0][0])
    assert parsed.path == "/products/BTC-USDT/book"
    assert parse_qs(parsed.query) == {"level": ["2"]}


def test_fetch_orderbook_rejects_bad_sequence_levels_order_and_product_id():
    client, _ = make_client([book_payload(sequence=0)])
    with pytest.raises(CoinbaseMarketDataError, match="sequence"):
        client.fetch_orderbook(product_id="BTC-USDT")

    client, _ = make_client([book_payload(asks=[["102", "1", 1], ["101", "1", 1]])])
    with pytest.raises(CoinbaseMarketDataError, match="not sorted lowest-first"):
        client.fetch_orderbook(product_id="BTC-USDT")

    client, _ = make_client([book_payload(bids=[["NaN", "1", 1]])])
    with pytest.raises(CoinbaseMarketDataError, match="bids price"):
        client.fetch_orderbook(product_id="BTC-USDT")

    client, _ = make_client([book_payload(asks=[["102", "1", 0]])])
    with pytest.raises(CoinbaseMarketDataError, match="order count"):
        client.fetch_orderbook(product_id="BTC-USDT")

    client, calls = make_client([book_payload()])
    with pytest.raises(ValueError, match="BASE-QUOTE"):
        client.fetch_orderbook(product_id="BTC/USDT")
    assert calls == []


def test_registry_and_comparator_use_coinbase_metadata_and_minimum_constraints():
    client, _ = make_client([product_payload(), book_payload()])
    metadata = client.fetch_instrument(product_id="BTC-USDT")
    book = client.fetch_orderbook(product_id=metadata.instrument_id)
    registry = InstrumentRegistry.from_metadata(
        [metadata],
        {
            ("coinbase", "BTC"): BTC,
            ("coinbase", "USDT"): USDT,
        },
    )
    assert registry.resolve("coinbase", "BTC-USDT").min_base_quantity == Decimal("0.0001")
    assert registry.resolve("coinbase", "BTC-USDT").min_quote_notional == Decimal("10")

    binance_book = BinanceOrderBookSnapshot(
        source="binance",
        symbol="BTCUSDT",
        bids=((Decimal("102.20"), Decimal("2")),),
        asks=((Decimal("102.30"), Decimal("2")),),
        last_update_id=123,
        fetched_at=book.fetched_at,
    )
    registry.register(InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"))
    costs = OpportunityCosts(
        buy_fee_bps="10",
        sell_fee_bps="10",
        buy_slippage_bps="0",
        sell_slippage_bps="0",
        latency_cost_bps="1",
    )
    result, _ = compare_cex_spot_orderbooks(
        book_a=book,
        book_b=binance_book,
        registry=registry,
        base_quantity="0.5",
        costs_by_direction={
            ("coinbase", "binance"): costs,
            ("binance", "coinbase"): costs,
        },
        now=book.fetched_at,
    )
    assert result.buy_venue_id == "coinbase"
    assert result.sell_venue_id == "binance"
    assert result.status == "net_positive_estimate"


def test_shared_limiter_paces_multiple_coinbase_clients_and_defers_after_429():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    first, _ = make_client([book_payload()], rate_limiter=limiter)
    second, _ = make_client([book_payload()], rate_limiter=limiter)
    first.fetch_orderbook(product_id="BTC-USDT")
    second.fetch_orderbook(product_id="BTC-USDT")
    assert sleeps == [1.0]

    responses = [CoinbaseRateLimited(8), book_payload()]

    def transport(_url, _timeout):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return encoded(value)

    rate_limited = CoinbaseExchangePublicMarketData(
        transport=transport,
        rate_limiter=limiter,
    )
    with pytest.raises(CoinbaseRateLimited):
        rate_limited.fetch_orderbook(product_id="BTC-USDT")
    rate_limited.fetch_orderbook(product_id="BTC-USDT")
    assert sleeps[-1] == 8.0


def test_json_rate_limit_error_defers_shared_limiter():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    client, _ = make_client([
        {"message": "Rate limit exceeded"},
    ], rate_limiter=limiter)
    with pytest.raises(CoinbaseRateLimited):
        client.fetch_orderbook(product_id="BTC-USDT")
    assert client._rate_limiter is limiter


def test_host_allowlist_timeout_and_pacing_are_conservative():
    for url in (
        "http://api.exchange.coinbase.com",
        "https://attacker.example",
        "https://api.exchange.coinbase.com/private",
        "https://user:pass@api.exchange.coinbase.com",
        "https://api.exchange.coinbase.com?token=secret",
    ):
        with pytest.raises(ValueError, match="official Coinbase"):
            CoinbaseExchangePublicMarketData(base_url=url, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="timeout_seconds"):
        CoinbaseExchangePublicMarketData(timeout_seconds=0, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="min_interval_seconds"):
        CoinbaseExchangePublicMarketData(min_interval_seconds=0.5, rate_limiter=_RateLimiter())


def test_malformed_and_oversized_coinbase_responses_fail_closed():
    client = CoinbaseExchangePublicMarketData(
        transport=lambda _url, _timeout: b"{not-json",
        rate_limiter=_RateLimiter(),
    )
    with pytest.raises(CoinbaseMarketDataError, match="malformed JSON"):
        client.fetch_orderbook(product_id="BTC-USDT")

    client = CoinbaseExchangePublicMarketData(
        transport=lambda _url, _timeout: b"x" * 1_000_001,
        rate_limiter=_RateLimiter(),
    )
    with pytest.raises(CoinbaseMarketDataError, match="oversized"):
        client.fetch_orderbook(product_id="BTC-USDT")
