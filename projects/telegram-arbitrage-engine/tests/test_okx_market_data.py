import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.okx import (
    OKXMarketDataError,
    OKXPublicSpotMarketData,
    OKXRateLimited,
    _RateLimiter,
)


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def instrument_payload(**overrides):
    item = {
        "instType": "SPOT",
        "instId": "BTC-USDT",
        "baseCcy": "BTC",
        "quoteCcy": "USDT",
        "lotSz": "0.0001",
        "tickSz": "0.1",
        "minSz": "0.0001",
        "state": "live",
    }
    item.update(overrides)
    return {"code": "0", "msg": "", "data": [item]}


def orderbook_payload(**overrides):
    item = {
        "instId": "BTC-USDT",
        "asks": [["101.20", "1.50000000", "0", "2"], ["101.30", "2", "0", "1"]],
        "bids": [["101.10", "1.25000000", "0", "3"], ["101.00", "2", "0", "1"]],
        "ts": "1793628000000",
        "seqId": 3235851742,
    }
    item.update(overrides)
    return {"code": "0", "msg": "", "data": [item]}


def make_client(responses, **kwargs):
    calls = []
    queue = list(responses)

    def transport(url, timeout):
        calls.append((url, timeout))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return encoded(item) if isinstance(item, dict) else item

    kwargs.setdefault("rate_limiter", _RateLimiter())
    return OKXPublicSpotMarketData(transport=transport, **kwargs), calls


def test_fetch_instrument_preserves_spot_identity_increments_and_minimum():
    client, calls = make_client([instrument_payload()])
    item = client.fetch_instrument(inst_id="BTC-USDT")

    assert item.venue_id == "okx"
    assert item.instrument_id == "BTC-USDT"
    assert item.market_family == "cex_spot"
    assert item.base_asset_code == "BTC"
    assert item.quote_asset_code == "USDT"
    assert item.base_step_size == Decimal("0.0001")
    assert item.price_tick_size == Decimal("0.1")
    assert item.min_base_quantity == Decimal("0.0001")
    assert item.min_quote_notional is None
    assert item.status == "TRADING"
    parsed = urlsplit(calls[0][0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.okx.com"
    assert parsed.path == "/api/v5/public/instruments"
    assert parse_qs(parsed.query) == {"instType": ["SPOT"], "instId": ["BTC-USDT"]}
    assert "apiKey" not in calls[0][0]


def test_fetch_instrument_preserves_suspended_state_and_rejects_identity_mismatch():
    client, _ = make_client([instrument_payload(state="suspend")])
    item = client.fetch_instrument(inst_id="BTC-USDT")
    assert item.status == "SUSPEND"

    client, _ = make_client([instrument_payload(instId="ETH-USDT")])
    with pytest.raises(OKXMarketDataError, match="identity"):
        client.fetch_instrument(inst_id="BTC-USDT")


def test_fetch_instrument_rejects_invalid_precision_and_currency_metadata():
    client, _ = make_client([instrument_payload(lotSz="NaN")])
    with pytest.raises(OKXMarketDataError, match="lotSz"):
        client.fetch_instrument(inst_id="BTC-USDT")

    client, _ = make_client([instrument_payload(baseCcy="btc")])
    with pytest.raises(OKXMarketDataError, match="currency"):
        client.fetch_instrument(inst_id="BTC-USDT")


def test_fetch_orderbook_preserves_decimal_depth_sequence_and_order_counts():
    client, calls = make_client([orderbook_payload()])
    book = client.fetch_orderbook(inst_id="BTC-USDT", depth=2)

    assert book.source == "okx"
    assert book.category == "spot"
    assert book.symbol == "BTC-USDT"
    assert book.bids == (
        (Decimal("101.10"), Decimal("1.25000000")),
        (Decimal("101.00"), Decimal("2")),
    )
    assert book.asks == (
        (Decimal("101.20"), Decimal("1.50000000")),
        (Decimal("101.30"), Decimal("2")),
    )
    assert book.bid_order_counts == (3, 1)
    assert book.ask_order_counts == (2, 1)
    assert book.bid_liquidation_order_counts == (0, 0)
    assert book.ask_liquidation_order_counts == (0, 0)
    assert book.sequence == 3235851742
    assert book.exchange_timestamp_ms == 1793628000000
    assert book.fetched_at.tzinfo is not None
    parsed = urlsplit(calls[0][0])
    assert parsed.path == "/api/v5/market/books"
    assert parse_qs(parsed.query) == {"instId": ["BTC-USDT"], "sz": ["2"]}


@pytest.mark.parametrize(("payload", "message"), [
    (orderbook_payload(instId="ETH-USDT"), "instrument"),
    (orderbook_payload(asks=[["101.20", "1", "0", "1"], ["101.30", "1", "0", "1"]][::-1]), "sorted"),
    (orderbook_payload(bids=[["101.30", "1", "0", "1"]]), "crossed"),
    (orderbook_payload(asks=[["NaN", "1", "0", "1"]]), "asks price"),
    (orderbook_payload(bids=[["101.10", "0", "0", "1"]]), "bids size"),
    (orderbook_payload(asks=[["101.20", "1", "0", "0"]]), "order count"),
])
def test_fetch_orderbook_rejects_malformed_or_untrusted_snapshots(payload, message):
    client, _ = make_client([payload])
    with pytest.raises(OKXMarketDataError, match=message):
        client.fetch_orderbook(inst_id="BTC-USDT")


def test_okx_api_rate_limit_defers_shared_limiter_before_next_request():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)
    responses = [
        {"code": "50011", "msg": "Rate limit reached", "data": []},
        instrument_payload(),
    ]

    def transport(_url, _timeout):
        item = responses.pop(0)
        return encoded(item)

    client = OKXPublicSpotMarketData(transport=transport, rate_limiter=limiter)
    with pytest.raises(OKXRateLimited):
        client.fetch_instrument(inst_id="BTC-USDT")
    client.fetch_instrument(inst_id="BTC-USDT")
    assert sleeps == [2.0]


def test_http_429_uses_retry_after_and_defers_shared_limiter():
    from io import BytesIO
    from urllib.error import HTTPError

    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _RateLimiter(clock=lambda: current[0], sleeper=sleep)

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, BytesIO(b""))

    import arb_engine.market_sources.okx as okx_module
    original = okx_module.urllib.request.build_opener
    okx_module.urllib.request.build_opener = lambda *_handlers: Opener()
    try:
        client = OKXPublicSpotMarketData(rate_limiter=limiter)
        with pytest.raises(OKXRateLimited) as error:
            client.fetch_instrument(inst_id="BTC-USDT")
        assert error.value.retry_after_seconds == 7
        assert sleeps == []
        with pytest.raises(OKXRateLimited):
            client.fetch_instrument(inst_id="BTC-USDT")
        assert sleeps == [7.0]
    finally:
        okx_module.urllib.request.build_opener = original


def test_okx_rejects_unapproved_hosts_unsafe_limits_and_invalid_ids():
    for url in (
        "http://www.okx.com",
        "https://attacker.example",
        "https://user:pass@www.okx.com",
        "https://www.okx.com/path",
        "https://www.okx.com?apiKey=secret",
    ):
        with pytest.raises(ValueError, match="regional HTTPS"):
            OKXPublicSpotMarketData(base_url=url, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="min_interval_seconds"):
        OKXPublicSpotMarketData(min_interval_seconds=0.5, rate_limiter=_RateLimiter())
    with pytest.raises(ValueError, match="timeout_seconds"):
        OKXPublicSpotMarketData(timeout_seconds=0, rate_limiter=_RateLimiter())
    client, calls = make_client([])
    with pytest.raises(ValueError, match="instrument ID"):
        client.fetch_instrument(inst_id="btcusdt")
    with pytest.raises(ValueError, match="depth"):
        client.fetch_orderbook(inst_id="BTC-USDT", depth=401)
    assert calls == []


def test_okx_rejects_malformed_json_and_oversized_response():
    client, _ = make_client([b"{not-json"])
    with pytest.raises(OKXMarketDataError, match="malformed JSON"):
        client.fetch_instrument(inst_id="BTC-USDT")

    client, _ = make_client([b"x" * 1_000_001])
    with pytest.raises(OKXMarketDataError, match="oversized"):
        client.fetch_instrument(inst_id="BTC-USDT")
