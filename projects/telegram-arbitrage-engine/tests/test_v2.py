from datetime import datetime, timezone

import pytest

from arb_engine.v2 import V2PoolState, quote_exact_input, simulate_round_trip


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
BLOCK_HASH = "0x" + "a" * 64


def pool(address, token0, token1, reserve0, reserve1, *, fee=30, chain=1, block=100):
    return V2PoolState(
        chain_id=chain,
        pair_address=address,
        token0=token0,
        token1=token1,
        reserve0_raw=reserve0,
        reserve1_raw=reserve1,
        fee_bps=fee,
        block_number=block,
        block_hash=BLOCK_HASH,
        observed_at=datetime.now(timezone.utc),
    )


def test_exact_input_quote_uses_integer_constant_product_math():
    p = pool("0x" + "3" * 40, QUOTE, BASE, 1_000_000_000_000, 10**24)
    result = quote_exact_input(p, QUOTE, 1_000_000_000)
    assert result.amount_in_raw == 1_000_000_000
    assert result.amount_out_raw > 0
    assert result.amount_out_raw < 10**24
    assert result.fee_bps == 30


def test_exact_input_quote_supports_reverse_token_order():
    p = pool("0x" + "5" * 40, QUOTE, BASE, 1_000_000_000_000, 10**24)
    result = quote_exact_input(p, BASE, 1_000_000_000)
    assert result.token_out == QUOTE


def test_same_block_two_pool_round_trip_is_calculated_in_raw_units():
    buy = pool("0x" + "3" * 40, QUOTE, BASE, 1_000_000_000_000, 10**24)
    sell = pool("0x" + "4" * 40, BASE, QUOTE, 10**24, 1_010_000_000_000)
    result = simulate_round_trip(
        buy, sell, quote_token=QUOTE, base_token=BASE, capital_quote_raw=1_000_000_000
    )
    assert result.buy.token_out == BASE
    assert result.sell.token_out == QUOTE
    assert result.final_quote_raw == result.sell.amount_out_raw
    assert result.gross_profit_quote_raw == result.final_quote_raw - result.capital_quote_raw
    assert result.classification.startswith("reserve_math_only")


def test_cross_chain_or_different_block_pools_are_rejected():
    buy = pool("0x" + "3" * 40, QUOTE, BASE, 1_000_000_000_000, 10**24)
    other_chain = pool("0x" + "4" * 40, BASE, QUOTE, 10**24, 1_010_000_000_000, chain=137)
    with pytest.raises(ValueError, match="Cross-chain"):
        simulate_round_trip(buy, other_chain, quote_token=QUOTE, base_token=BASE, capital_quote_raw=1000)

    other_block = pool("0x" + "4" * 40, BASE, QUOTE, 10**24, 1_010_000_000_000, block=101)
    with pytest.raises(ValueError, match="same canonical block"):
        simulate_round_trip(buy, other_block, quote_token=QUOTE, base_token=BASE, capital_quote_raw=1000)


def test_same_pool_cannot_be_used_as_two_venues():
    p = pool("0x" + "3" * 40, QUOTE, BASE, 1_000_000_000_000, 10**24)
    with pytest.raises(ValueError, match="different pools"):
        simulate_round_trip(p, p, quote_token=QUOTE, base_token=BASE, capital_quote_raw=1000)


@pytest.mark.parametrize("fee", [-1, 10000, True])
def test_invalid_pool_fee_is_rejected(fee):
    p = pool("0x" + "5" * 40, QUOTE, BASE, 10000, 10000, fee=fee)
    with pytest.raises(ValueError, match="fee"):
        quote_exact_input(p, QUOTE, 100)


def test_wrong_input_token_and_non_integer_amount_are_rejected():
    p = pool("0x" + "5" * 40, QUOTE, BASE, 10000, 10000)
    with pytest.raises(ValueError, match="not part"):
        quote_exact_input(p, "0x" + "3" * 40, 100)
    with pytest.raises(ValueError, match="positive integer"):
        quote_exact_input(p, QUOTE, 100.0)


def test_stale_pool_state_is_rejected():
    from dataclasses import replace
    from datetime import timedelta

    current = pool("0x" + "5" * 40, QUOTE, BASE, 10000, 10000)
    stale = replace(current, observed_at=datetime.now(timezone.utc) - timedelta(seconds=31))
    with pytest.raises(ValueError, match="stale"):
        quote_exact_input(stale, QUOTE, 100)


def test_reserves_must_fit_v2_uint112():
    p = pool("0x" + "3" * 40, QUOTE, BASE, 2**112, 10000)
    with pytest.raises(ValueError, match="uint112"):
        quote_exact_input(p, QUOTE, 100)


def test_price_impact_is_separate_from_pool_fee_and_increases_with_size():
    p = pool("0x" + "3" * 40, QUOTE, BASE, 10**12, 10**24, fee=30)
    small = quote_exact_input(p, QUOTE, 10**8)
    large = quote_exact_input(p, QUOTE, 10**11)
    assert small.price_impact_bps >= 0
    assert large.price_impact_bps > small.price_impact_bps
    assert small.fee_bps == 30


def test_zero_addresses_and_unsorted_v2_tokens_are_rejected():
    with pytest.raises(ValueError, match="Zero address"):
        quote_exact_input(
            pool("0x" + "0" * 40, QUOTE, BASE, 10000, 10000), QUOTE, 100,
        )
    with pytest.raises(ValueError, match="lower-sorted"):
        quote_exact_input(
            pool("0x" + "3" * 40, BASE, QUOTE, 10000, 10000), QUOTE, 100,
        )


def test_exact_input_amount_must_fit_uint256():
    p = pool("0x" + "3" * 40, QUOTE, BASE, 10000, 10000)
    with pytest.raises(ValueError, match="uint256"):
        quote_exact_input(p, QUOTE, 2**256)
