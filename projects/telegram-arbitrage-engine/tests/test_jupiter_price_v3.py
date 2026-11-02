import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.jupiter import (
    JupiterPriceError,
    JupiterPriceRateLimited,
    JupiterPriceV3Client,
    JupiterQuoteOnlyClient,
    _KeylessRateLimiter,
)


SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def price_payload(**overrides):
    item = {
        "usdPrice": "147.481234567890123456",
        "decimals": 9,
        "blockId": 348004023,
        "priceChange24h": "-2.31",
        "liquidity": "621679197.67",
        "createdAt": "2026-11-02T12:00:00.000Z",
    }
    item.update(overrides)
    return {SOL: item}


def make_client(payload, **kwargs):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return encoded(payload)

    kwargs.setdefault("rate_limiter", _KeylessRateLimiter())
    return JupiterPriceV3Client(transport=transport, **kwargs), calls


def test_price_v3_fetches_exact_mints_and_preserves_decimal_and_provenance():
    client, calls = make_client(price_payload(), min_interval_seconds=2)
    result = client.fetch_prices([SOL, USDC])

    assert result.requested_mints == (SOL, USDC)
    assert len(result.observations) == 1
    assert result.missing_mints == (USDC,)
    price = result.observations[0]
    assert price.source == "jupiter"
    assert price.network == "solana"
    assert price.mint == SOL
    assert price.usd_price == Decimal("147.481234567890123456")
    assert price.decimals == 9
    assert price.block_id == 348004023
    assert price.price_change_24h == Decimal("-2.31")
    assert price.liquidity_usd == Decimal("621679197.67")
    assert price.created_at == datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert price.fetched_at.tzinfo is not None
    assert price.data_class == "indicative_price"
    assert result.data_class == "indicative_price"

    parsed = urlsplit(calls[0][0])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.jup.ag"
    assert parsed.path == "/price/v3"
    assert parse_qs(parsed.query) == {"ids": [f"{SOL},{USDC}"]}
    assert "apiKey" not in calls[0][0] and "x-api-key" not in calls[0][0]


def test_price_v3_missing_and_unpriced_mints_are_not_zero():
    client, _ = make_client({
        SOL: price_payload()[SOL],
        USDC: {"decimals": 6, "blockId": 348004025},
        JUP: None,
    })
    result = client.fetch_prices([SOL, USDC, JUP])
    assert [item.mint for item in result.observations] == [SOL]
    assert result.missing_mints == (USDC, JUP)
    assert all(item.usd_price > 0 for item in result.observations)


def test_price_v3_rejects_unrequested_response_mints_and_malformed_records():
    client, _ = make_client({JUP: price_payload()[SOL]})
    with pytest.raises(JupiterPriceError, match="unrequested mint"):
        client.fetch_prices([SOL])

    client, _ = make_client({SOL: "not-an-object"})
    with pytest.raises(JupiterPriceError, match="invalid mint observation"):
        client.fetch_prices([SOL])


@pytest.mark.parametrize(("field", "value", "message"), [
    ("usdPrice", 0, "usdPrice"),
    ("usdPrice", "NaN", "usdPrice"),
    ("decimals", -1, "decimals"),
    ("decimals", 19, "decimals"),
    ("decimals", True, "decimals"),
    ("blockId", 0, "blockId"),
    ("blockId", "348", "blockId"),
    ("liquidity", -1, "liquidity"),
    ("createdAt", "not-a-date", "createdAt"),
    ("createdAt", "2026-11-02T12:00:00", "timezone"),
])
def test_price_v3_rejects_invalid_price_metadata(field, value, message):
    client, _ = make_client(price_payload(**{field: value}))
    with pytest.raises(JupiterPriceError, match=message):
        client.fetch_prices([SOL])


def test_price_v3_validates_canonical_32_byte_base58_mints_and_batch_bounds():
    client, calls = make_client(price_payload())
    with pytest.raises(ValueError, match="32-byte"):
        client.fetch_prices(["1" * 40])
    with pytest.raises(ValueError, match="duplicates"):
        client.fetch_prices([SOL, SOL])
    with pytest.raises(ValueError, match="between 1 and 50"):
        client.fetch_prices([])
    with pytest.raises(ValueError, match="between 1 and 50"):
        client.fetch_prices([SOL] * 51)
    assert calls == []


def test_price_v3_supports_exactly_50_unique_valid_mints():
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

    def encode_32_byte_integer(number):
        chars = []
        while number:
            number, remainder = divmod(number, 58)
            chars.append(alphabet[remainder])
        encoded_value = "".join(reversed(chars)) or "1"
        return encoded_value.rjust(32, "1")

    mints = [encode_32_byte_integer(i + 1) for i in range(50)]
    assert len(set(mints)) == 50
    client, calls = make_client({})
    result = client.fetch_prices(mints)
    assert result.requested_mints == tuple(mints)
    assert result.missing_mints == tuple(mints)
    assert len(calls) == 1


def test_price_v3_uses_shared_half_request_per_second_limiter_across_clients():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _KeylessRateLimiter(clock=lambda: current[0], sleeper=sleep)
    first, _ = make_client(price_payload(), rate_limiter=limiter)
    second, _ = make_client(price_payload(), rate_limiter=limiter)
    first.fetch_prices([SOL])
    second.fetch_prices([SOL])
    assert sleeps == [2.0]


def test_price_v3_429_defers_the_shared_limiter_before_next_request():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _KeylessRateLimiter(clock=lambda: current[0], sleeper=sleep)
    responses = [JupiterPriceRateLimited(7), encoded(price_payload())]

    def transport(_url, _timeout):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = JupiterPriceV3Client(transport=transport, rate_limiter=limiter)
    with pytest.raises(JupiterPriceRateLimited, match="deferred"):
        client.fetch_prices([SOL])
    client.fetch_prices([SOL])
    assert sleeps == [7.0]


def test_price_v3_rejects_unapproved_hosts_and_unsafe_rate_limits():
    for url in (
        "http://api.jup.ag",
        "https://attacker.example",
        "https://api.jup.ag?token=secret",
        "https://user:pass@api.jup.ag",
        "https://api.jup.ag/price",
    ):
        with pytest.raises(ValueError, match="official Jupiter HTTPS"):
            JupiterPriceV3Client(base_url=url, rate_limiter=_KeylessRateLimiter())
    with pytest.raises(ValueError, match="0.5 RPS"):
        JupiterPriceV3Client(min_interval_seconds=1, rate_limiter=_KeylessRateLimiter())
    with pytest.raises(ValueError, match="timeout_seconds"):
        JupiterPriceV3Client(timeout_seconds=0, rate_limiter=_KeylessRateLimiter())


def test_price_v3_rejects_malformed_and_oversized_responses():
    client = JupiterPriceV3Client(
        transport=lambda _url, _timeout: b"{not-json",
        rate_limiter=_KeylessRateLimiter(),
    )
    with pytest.raises(JupiterPriceError, match="malformed JSON"):
        client.fetch_prices([SOL])

    client = JupiterPriceV3Client(
        transport=lambda _url, _timeout: b"x" * 1_000_001,
        rate_limiter=_KeylessRateLimiter(),
    )
    with pytest.raises(JupiterPriceError, match="oversized"):
        client.fetch_prices([SOL])


def test_price_v3_parses_json_number_precision_without_binary_float_rounding():
    raw = (
        f'{{"{SOL}":{{"usdPrice":147.481234567890123456,'
        '"decimals":9,"blockId":348004023}}}'
    ).encode("utf-8")
    client = JupiterPriceV3Client(
        transport=lambda _url, _timeout: raw,
        rate_limiter=_KeylessRateLimiter(),
    )
    result = client.fetch_prices([SOL])
    assert result.observations[0].usd_price == Decimal("147.481234567890123456")


def test_price_and_quote_clients_share_the_same_injected_keyless_limiter():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _KeylessRateLimiter(clock=lambda: current[0], sleeper=sleep)
    price, _ = make_client(price_payload(), rate_limiter=limiter)
    quote_payload = {
        "router": "metis",
        "inAmount": "100000000",
        "outAmount": "11680143",
        "transaction": None,
    }
    quote = JupiterQuoteOnlyClient(
        transport=lambda _url, _timeout: encoded(quote_payload),
        rate_limiter=limiter,
    )
    price.fetch_prices([SOL])
    quote.quote(
        input_mint=SOL,
        output_mint=USDC,
        amount_raw="100000000",
    )
    assert sleeps == [2.0]


def test_price_v3_default_transport_defers_shared_limiter_from_http_429(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    limiter = _KeylessRateLimiter(clock=lambda: current[0], sleeper=sleep)

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(
                request.full_url, 429, "Too Many Requests",
                {"Retry-After": "9"}, BytesIO(b""),
            )

    monkeypatch.setattr(
        "arb_engine.market_sources.jupiter.urllib.request.build_opener",
        lambda *_handlers: Opener(),
    )
    client = JupiterPriceV3Client(rate_limiter=limiter)
    with pytest.raises(JupiterPriceRateLimited) as error:
        client.fetch_prices([SOL])
    assert error.value.retry_after_seconds == 9
    # A subsequent request cannot be sent before the shared 429 cooldown expires.
    with pytest.raises(JupiterPriceRateLimited):
        client.fetch_prices([SOL])
    assert sleeps == [9.0]
