import json
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.jupiter import (
    JupiterQuoteError,
    JupiterQuoteOnlyClient,
)


SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def encoded(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def quote_payload(**overrides):
    payload = {
        "inputMint": SOL,
        "outputMint": USDC,
        "inAmount": "100000000",
        "outAmount": "11680143",
        "otherAmountThreshold": "11621743",
        "feeBps": 2,
        "feeMint": USDC,
        "priceImpactPct": "0.0001",
        "router": "metis",
        "routePlan": [{"swapInfo": {"ammKey": "pool-id", "label": "Orca"}}],
        "requestId": "quote-request-1",
        "contextSlot": 345678900,
        "transaction": None,
    }
    payload.update(overrides)
    return payload


def make_client(payload, **kwargs):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return encoded(payload)

    kwargs.setdefault("min_interval_seconds", 2.0)
    return JupiterQuoteOnlyClient(transport=transport, **kwargs), calls


def test_quote_only_request_uses_exact_mints_and_never_requests_transaction():
    client, calls = make_client(quote_payload())
    quote = client.quote(input_mint=SOL, output_mint=USDC, amount_raw="100000000")

    assert quote.source == "jupiter"
    assert quote.network == "solana"
    assert quote.input_mint == SOL
    assert quote.output_mint == USDC
    assert quote.input_amount_raw == 100000000
    assert quote.output_amount_raw == 11680143
    assert quote.other_amount_threshold_raw == 11621743
    assert quote.fee_bps == 2
    assert quote.fee_mint == USDC
    assert quote.price_impact_pct == Decimal("0.0001")
    assert quote.router == "metis"
    assert quote.route_plan[0]["swapInfo"]["label"] == "Orca"
    assert quote.transaction_present is False
    assert len(calls) == 1

    parsed = urlsplit(calls[0][0])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.jup.ag"
    assert parsed.path == "/swap/v2/order"
    assert query == {"inputMint": [SOL], "outputMint": [USDC], "amount": ["100000000"]}
    assert "taker" not in query
    assert "apiKey" not in query and "x-api-key" not in query


def test_quote_accepts_documented_minimal_quote_response_without_mint_echo():
    payload = {
        "router": "metis",
        "inAmount": "100000000",
        "outAmount": "11680143",
        "feeBps": 2,
        "transaction": None,
    }
    client, _ = make_client(payload)
    quote = client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)
    assert quote.output_amount_raw == 11680143
    assert quote.route_plan == ()


@pytest.mark.parametrize("payload", [
    quote_payload(transaction="unsigned-base64-transaction"),
    {key: value for key, value in quote_payload().items() if key != "transaction"},
])
def test_quote_rejects_any_response_that_is_not_explicitly_quote_only(payload):
    client, _ = make_client(payload)
    with pytest.raises(JupiterQuoteError, match="transaction=null"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)


def test_quote_rejects_mint_amount_and_output_identity_mismatches():
    client, _ = make_client(quote_payload(inputMint=USDC))
    with pytest.raises(JupiterQuoteError, match="mint identity"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)

    client, _ = make_client(quote_payload(inAmount="999"))
    with pytest.raises(JupiterQuoteError, match="input amount"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)

    client, _ = make_client(quote_payload(outAmount="0"))
    with pytest.raises(JupiterQuoteError, match="outAmount"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)


def test_quote_validates_mints_and_uint64_amount_before_network():
    client, calls = make_client(quote_payload())
    with pytest.raises(ValueError, match="mint address"):
        client.quote(input_mint="not-a-mint", output_mint=USDC, amount_raw=100)
    with pytest.raises(ValueError, match="different"):
        client.quote(input_mint=SOL, output_mint=SOL, amount_raw=100)
    for amount in (0, -1, 2**64, 1.0, True, "1.5", "x"):
        with pytest.raises(ValueError, match="amount_raw"):
            client.quote(input_mint=SOL, output_mint=USDC, amount_raw=amount)
    assert calls == []


def test_quote_rejects_api_errors_and_malformed_response():
    client, _ = make_client({"error": "NO_ROUTES_FOUND", "transaction": None})
    with pytest.raises(JupiterQuoteError, match="unsuccessful"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100)

    client = JupiterQuoteOnlyClient(
        transport=lambda _url, _timeout: b"{not-json",
        min_interval_seconds=2,
    )
    with pytest.raises(JupiterQuoteError, match="malformed JSON"):
        client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100)


def test_quote_enforces_keyless_rate_limit_of_at_most_half_request_per_second():
    current = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += seconds

    client, calls = make_client(
        quote_payload(),
        clock=lambda: current[0],
        sleeper=sleep,
    )
    client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)
    client.quote(input_mint=SOL, output_mint=USDC, amount_raw=100000000)
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_jupiter_host_is_allowlisted_and_keyless_pacing_cannot_be_raised_unsafely():
    for url in (
        "http://api.jup.ag",
        "https://attacker.example",
        "https://api.jup.ag?token=secret",
        "https://user:pass@api.jup.ag",
        "https://api.jup.ag/swap/v2",
    ):
        with pytest.raises(ValueError, match="official Jupiter HTTPS"):
            JupiterQuoteOnlyClient(base_url=url)

    with pytest.raises(ValueError, match="0.5 RPS"):
        JupiterQuoteOnlyClient(min_interval_seconds=1.0)
