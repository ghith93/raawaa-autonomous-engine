import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.binance import BinanceOrderBookSnapshot
from arb_engine.market_sources.cross_venue import (
    InstrumentRegistry,
    InstrumentSpec,
    compare_cex_spot_orderbooks,
)
from arb_engine.market_sources.kraken import (
    KrakenMarketDataError,
    KrakenPublicMarketData,
    KrakenRateLimited,
    _RateLimiter,
)
from arb_engine.opportunity_costs import OpportunityCosts


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def asset_pairs_payload(**overrides):
    item = {
        "altname": "XBTUSDT",
        "wsname": "XBT/USDT",
        "base": "XXBT",
        "quote": "USDT",
        "lot": "unit",
        "pair_decimals": 2,
        "lot_decimals": 8,
        "lot_multiplier": 1,
        "ordermin": "0.0001",
        "costmin": "0.5",
        "status": "online",
    }
    item.update(overrides)
    return {"error": [], "result": {"XXBTUSDT": item}}


def depth_payload(**overrides):
    item = {
        "asks": [["101.20", "1.5", 1793628001], ["101.30", "2", 1793628000]],
        "bids": [["101.10", "1", 1793628002], ["101.00", "2", 1793628001]],
    }
    item.update(overrides)
    return {"error": [], "result": {"XXBTUSDT": item}}


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
    return KrakenPublicMarketData(transport=transport, **kwargs), calls


def test_fetch_instrument_resolves_alias_to_internal_id_and_precision():
    client, calls = make_client([asset_pairs_payload()])
    metadata = client.fetch_instrument(pair="xbtusdt")

    assert metadata.venue_id == "kraken"
    assert metadata.instrument_id == "XXBTUSDT"
    assert metadata.market_family == "cex_spot"
    assert metadata.base_asset_code == "XXBT"
    assert metadata.quote_asset_code == "USDT"
    assert metadata.base_step_size == Decimal("0.00000001")
    assert metadata.price_tick_size == Decimal("0.01")
    assert metadata.min_base_quantity == Decimal("0.0001")
    assert metadata.min_quote_notional == Decimal("0.5")
    assert metadata.status == "TRADING"
    parsed = urlsplit(calls[0][0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.kraken.com"
    assert parsed.path == "/0/public/AssetPairs"
    assert parse_qs(parsed.query) == {"pair": ["XBTUSDT"]}


def test_fetch_instrument_accepts_canonical_id_or_wsname_and_rejects_mismatch():
    client, _ = make_client([asset_pairs_payload()])
    assert client.fetch_instrument(pair="XXBTUSDT").instrument_id == "XXBTUSDT"

    client, _ = make_client([asset_pairs_payload()])
    assert client.fetch_instrument(pair="XBT/USDT").instrument_id == "XXBTUSDT"

    client, _ = make_client([asset_pairs_payload(altname="ETHUSDT")])
    with pytest.raises(KrakenMarketDataError, match="did not match"):
        client.fetch_instrument(pair="XBTUSDT")


def test_fetch_instrument_rejects_ambiguous_or_incomplete_metadata():
    client, _ = make_client([{"error": [], "result": {
        "XXBTUSDT": asset_pairs_payload()["result"]["XXBTUSDT"],
        "XETHUSDT": asset_pairs_payload()["result"]["XXBTUSDT"],
    }}])
    with pytest.raises(KrakenMarketDataError, match="exactly one"):
        client.fetch_instrument(pair="XBTUSDT")

    client, _ = make_client([asset_pairs_payload(lot_decimals=99)])
    with pytest.raises(KrakenMarketDataError, match="lot_decimals"):
        client.fetch_instrument(pair="XBTUSDT")

    client, _ = make_client([asset_pairs_payload(base="")])
    with pytest.raises(KrakenMarketDataError, match="asset codes"):
        client.fetch_instrument(pair="XBTUSDT")


def test_fetch_orderbook_preserves_decimal_levels_exchange_times_and_local_time():
    client, calls = make_client([depth_payload()])
    book = client.fetch_orderbook(pair="XXBTUSDT", count=50)

    assert book.source == "kraken"
    assert book.category == "spot"
    assert book.symbol == "XXBTUSDT"
    assert book.asks == (
        (Decimal("101.20"), Decimal("1.5")),
        (Decimal("101.30"), Decimal("2")),
    )
    assert book.bids == (
        (Decimal("101.10"), Decimal("1")),
        (Decimal("101.00"), Decimal("2")),
    )
    assert book.ask_level_timestamps_ms == (1793628001000, 1793628000000)
    assert book.bid_level_timestamps_ms == (1793628002000, 1793628001000)
    assert book.exchange_timestamp_ms == 1793628002000
    assert book.fetched_at.tzinfo is not None
    parsed = urlsplit(calls[0][0])
    assert parsed.path == "/0/public/Depth"
    assert parse_qs(parsed.query) == {"pair": ["XXBTUSDT"], "count": ["50"]}


def test_fetch_orderbook_rejects_alias_mismatch_bad_levels_and_unsorted_books():
    client, _ = make_client([depth_payload()])
    with pytest.raises(KrakenMarketDataError, match="canonical instrument_id"):
        client.fetch_orderbook(pair="XBTUSDT")

    client, _ = make_client([depth_payload(asks=[["101.2", "1", 1], ["101.1", "1", 1]])])
    with pytest.raises(KrakenMarketDataError, match="not sorted lowest-first"):
        client.fetch_orderbook(pair="XXBTUSDT")

    client, _ = make_client([depth_payload(bids=[["NaN", "1", 1]])])
    with pytest.raises(KrakenMarketDataError, match="bids price"):
        client.fetch_orderbook(pair="XXBTUSDT")

    client, _ = make_client([depth_payload(asks=[["101", "1"]])])
    with pytest.raises(KrakenMarketDataError, match="level"):
        client.fetch_orderbook(pair="XXBTUSDT")


def test_fetch_orderbook_validates_count_and_pair_before_network():
    client, calls = make_client([depth_payload()])
    with pytest.raises(ValueError, match="count"):
        client.fetch_orderbook(pair="XXBTUSDT", count=501)
    with pytest.raises(ValueError, match="pair"):
        client.fetch_orderbook(pair="xbt-usdt")
    assert calls == []


def test_registry_and_cross_venue_comparator_accept_kraken_books_with_explicit_aliases():
    client, _ = make_client([asset_pairs_payload(), depth_payload()])
    metadata = client.fetch_instrument(pair="XBTUSDT")
    book = client.fetch_orderbook(pair=metadata.instrument_id)
    registry = InstrumentRegistry.from_metadata(
        [metadata],
        {
            ("kraken", "XXBT"): BTC,
            ("kraken", "USDT"): USDT,
        },
    )
    binance_book = BinanceOrderBookSnapshot(
        source="binance",
        symbol="BTCUSDT",
        bids=((Decimal("101.4"), Decimal("2")),),
        asks=((Decimal("101.5"), Decimal("2")),),
        last_update_id=123,
        fetched_at=NOW,
    )
    registry.register(InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"))
    known_costs = OpportunityCosts(
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
            ("kraken", "binance"): known_costs,
            ("binance", "kraken"): known_costs,
        },
        now=book.fetched_at,
    )
    assert result.buy_venue_id == "kraken"
    assert result.sell_venue_id == "binance"
    assert result.status == "net_positive_estimate"


def test_shared_rate_limiter_paces_multiple_kraken_clients_at_one_request_per_second():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    first, _ = make_client([depth_payload()], rate_limiter=limiter)
    second, _ = make_client([depth_payload()], rate_limiter=limiter)
    first.fetch_orderbook(pair="XXBTUSDT")
    second.fetch_orderbook(pair="XXBTUSDT")
    assert sleeps == [1.0]


def test_http_429_defers_shared_kraken_limiter():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    responses = [KrakenRateLimited(8), depth_payload()]

    def transport(_url, _timeout):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return encoded(value)

    client = KrakenPublicMarketData(transport=transport, rate_limiter=limiter)
    with pytest.raises(KrakenRateLimited):
        client.fetch_orderbook(pair="XXBTUSDT")
    client.fetch_orderbook(pair="XXBTUSDT")
    assert sleeps == [8.0]


def test_kraken_json_rate_limit_error_defers_shared_limiter():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    client, _ = make_client([
        {"error": ["EAPI:Rate limit exceeded"], "result": {}},
    ], rate_limiter=limiter)
    with pytest.raises(KrakenRateLimited):
        client.fetch_orderbook(pair="XXBTUSDT")
    assert client._rate_limiter is limiter


def test_host_allowlist_timeout_and_minimum_public_rate_limit():
    for url in (
        "http://api.kraken.com",
        "https://attacker.example",
        "https://api.kraken.com/0/private",
        "https://user:pass@api.kraken.com",
        "https://api.kraken.com?token=secret",
    ):
        with pytest.raises(ValueError, match="official Kraken"):
            KrakenPublicMarketData(base_url=url, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="timeout_seconds"):
        KrakenPublicMarketData(timeout_seconds=0, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="min_interval_seconds"):
        KrakenPublicMarketData(min_interval_seconds=0.5, rate_limiter=_RateLimiter())


def test_malformed_and_oversized_responses_fail_closed():
    client = KrakenPublicMarketData(
        transport=lambda _url, _timeout: b"{not-json",
        rate_limiter=_RateLimiter(),
    )
    with pytest.raises(KrakenMarketDataError, match="malformed JSON"):
        client.fetch_orderbook(pair="XXBTUSDT")

    client = KrakenPublicMarketData(
        transport=lambda _url, _timeout: b"x" * 1_000_001,
        rate_limiter=_RateLimiter(),
    )
    with pytest.raises(KrakenMarketDataError, match="oversized"):
        client.fetch_orderbook(pair="XXBTUSDT")
