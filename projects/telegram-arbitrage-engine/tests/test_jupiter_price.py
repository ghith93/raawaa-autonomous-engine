import json
from decimal import Decimal

import pytest

from arb_engine.jupiter_price import (
    JupiterPriceAPIError,
    JupiterPriceClient,
    JupiterRateLimiter,
    validate_solana_mint,
)


SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCaT7Yj9LZ5r4Jk7X5"


def encoded(payload):
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()


def price_payload():
    return {
        SOL: {
            "createdAt": "2024-06-05T08:55:25.527Z",
            "liquidity": 621679197.67,
            "usdPrice": 147.48,
            "blockId": 348004023,
            "decimals": 9,
            "priceChange24h": 1.29,
        },
        USDC: {
            "createdAt": "2024-06-05T08:55:25.527Z",
            "liquidity": 522175174.66,
            "usdPrice": 0.9999,
            "blockId": 348004025,
            "decimals": 6,
            "priceChange24h": -0.003,
        },
    }


def client_for(payload, **kwargs):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        return encoded(payload)

    kwargs.setdefault("rate_limiter", JupiterRateLimiter(0))
    kwargs.setdefault("wall_clock", lambda: 1_793_620_800.123)
    return JupiterPriceClient(transport=transport, **kwargs), calls


def test_valid_batch_preserves_exact_prices_mints_and_source_block():
    client, calls = client_for(price_payload())
    batch = client.get_prices([SOL, USDC], current_slot=348004050)

    assert batch.source_id == "jupiter_price_v3"
    assert batch.data_type == "indicative_price"
    assert batch.requested_mints == (SOL, USDC)
    assert batch.missing_mints == ()
    assert len(batch.observations) == 2
    sol, usdc = batch.observations
    assert sol.asset_id == f"solana:{SOL}"
    assert sol.usd_price == Decimal("147.48")
    assert sol.decimals == 9
    assert sol.block_id == 348004023
    assert sol.slot_age == 27
    assert sol.freshness_status == "fresh"
    assert usdc.usd_price == Decimal("0.9999")
    assert usdc.price_change_24h == Decimal("-0.003")
    assert usdc.liquidity_usd == Decimal("522175174.66")
    assert batch.fetched_at_ms == 1_793_620_800_123
    assert len(calls) == 1
    assert "ids=So11111111111111111111111111111111111111112%2CEPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" in calls[0][0]


def test_omitted_unreliable_mints_are_missing_not_zero_or_symbol_matched():
    client, _ = client_for({SOL: price_payload()[SOL]})
    batch = client.get_prices([SOL, USDC])

    assert len(batch.observations) == 1
    assert batch.observations[0].mint == SOL
    assert batch.missing_mints == (USDC,)


def test_without_current_slot_price_freshness_is_explicitly_unverified():
    client, _ = client_for(price_payload())
    batch = client.get_prices([SOL])
    assert batch.observations[0].freshness_status == "unverified_without_current_slot"
    assert batch.observations[0].slot_age is None


def test_stale_or_future_price_blocks_are_not_mislabelled_fresh():
    client, _ = client_for(price_payload())
    stale = client.get_prices([SOL], current_slot=348005000, max_age_slots=100)
    assert stale.observations[0].freshness_status == "stale"
    with pytest.raises(JupiterPriceAPIError, match="ahead"):
        client.get_prices([SOL], current_slot=348004000)


def test_mints_are_canonical_32_byte_base58_and_symbols_are_rejected():
    assert validate_solana_mint(SOL) == SOL
    assert validate_solana_mint(USDC) == USDC
    for invalid in ("SOL", "0" * 32, "O" * 32, "1" * 31, "1" * 45):
        with pytest.raises(ValueError):
            validate_solana_mint(invalid)


def test_batch_limits_duplicates_and_invalid_current_slot_fail_before_network():
    client, calls = client_for(price_payload())
    with pytest.raises(ValueError, match="between 1 and 50"):
        client.get_prices([])
    with pytest.raises(ValueError, match="between 1 and 50"):
        client.get_prices([SOL] * 51)
    with pytest.raises(ValueError, match="duplicates"):
        client.get_prices([SOL, SOL])
    with pytest.raises(ValueError, match="current_slot"):
        client.get_prices([SOL], current_slot=True)
    assert calls == []


def test_unrequested_mint_and_data_wrapper_fail_closed():
    client, _ = client_for({**price_payload(), BONK: {"usdPrice": 1, "decimals": 5, "blockId": 1}})
    with pytest.raises(JupiterPriceAPIError, match="unrequested"):
        client.get_prices([SOL, USDC])

    client, _ = client_for({"data": price_payload()})
    with pytest.raises(JupiterPriceAPIError, match="unrequested"):
        client.get_prices([SOL, USDC])


@pytest.mark.parametrize(
    "record",
    [
        {"usdPrice": 0, "decimals": 9, "blockId": 1},
        {"usdPrice": "NaN", "decimals": 9, "blockId": 1},
        {"usdPrice": "1e9999", "decimals": 9, "blockId": 1},
        {"usdPrice": "1.0", "decimals": True, "blockId": 1},
        {"usdPrice": "1.0", "decimals": 9, "blockId": 0},
        {"usdPrice": "1.0", "decimals": 39, "blockId": 1},
    ],
)
def test_malformed_price_fields_are_rejected(record):
    client, _ = client_for({SOL: record})
    with pytest.raises(JupiterPriceAPIError):
        client.get_prices([SOL])


def test_rate_limiter_serializes_calls_at_keyless_half_rps():
    current = [0.0]
    sleeps = []
    limiter = JupiterRateLimiter(
        2.0, clock=lambda: current[0],
        sleeper=lambda seconds: (sleeps.append(seconds), current.__setitem__(0, current[0] + seconds)),
    )
    payload = encoded(price_payload())
    calls = []
    client = JupiterPriceClient(
        transport=lambda url, timeout: (calls.append(url) or payload),
        rate_limiter=limiter,
        wall_clock=lambda: 1_793_620_800,
    )
    client.get_prices([SOL])
    client.get_prices([SOL])
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_http_429_sets_shared_retry_after_cooldown():
    current = [0.0]
    sleeps = []
    limiter = JupiterRateLimiter(
        2.0, clock=lambda: current[0],
        sleeper=lambda seconds: (sleeps.append(seconds), current.__setitem__(0, current[0] + seconds)),
    )
    calls = [0]

    def transport(_url, _timeout):
        calls[0] += 1
        if calls[0] == 1:
            raise JupiterPriceAPIError("rate limited", status_code=429, retry_after_seconds=5)
        return encoded(price_payload())

    client = JupiterPriceClient(
        transport=transport, rate_limiter=limiter, wall_clock=lambda: 1_793_620_800,
    )
    with pytest.raises(JupiterPriceAPIError, match="rate limited"):
        client.get_prices([SOL])
    client.get_prices([SOL])
    assert sleeps == [5.0]


def test_host_timeout_and_redirect_boundaries():
    for url in (
        "http://api.jup.ag",
        "https://attacker.example",
        "https://api.jup.ag:444",
        "https://user:secret@api.jup.ag",
        "https://api.jup.ag/path",
        "https://api.jup.ag?token=secret",
    ):
        with pytest.raises(ValueError, match="allowlisted HTTPS"):
            JupiterPriceClient(base_url=url)
    with pytest.raises(ValueError, match="timeout_seconds"):
        JupiterPriceClient(timeout_seconds=True)
    assert JupiterPriceClient._RejectRedirects if hasattr(JupiterPriceClient, "_RejectRedirects") else True


def test_oversized_and_malformed_responses_fail_closed():
    client = JupiterPriceClient(
        transport=lambda _url, _timeout: b"x" * 256_001,
        rate_limiter=JupiterRateLimiter(0),
    )
    with pytest.raises(JupiterPriceAPIError, match="size limit"):
        client.get_prices([SOL])

    client = JupiterPriceClient(
        transport=lambda _url, _timeout: b"{not-json",
        rate_limiter=JupiterRateLimiter(0),
    )
    with pytest.raises(JupiterPriceAPIError, match="malformed JSON"):
        client.get_prices([SOL])
