from __future__ import annotations

import pytest

from arb_engine.sqrt_price_math import (
    MAX_UINT128,
    MAX_UINT160,
    MAX_UINT256,
    get_next_sqrt_price_from_input,
    get_next_sqrt_price_from_output,
)
from arb_engine.swap_math import compute_swap_step
from arb_engine.v3_math import Q96


def test_official_sqrt_price_math_input_vectors() -> None:
    liquidity = 10**18
    amount_in = 10**17
    assert get_next_sqrt_price_from_input(Q96, liquidity, amount_in, True) == (
        72025602285694852357767227579
    )
    assert get_next_sqrt_price_from_input(Q96, liquidity, amount_in, False) == (
        87150978765690771352898345369
    )


def test_official_sqrt_price_math_output_vectors() -> None:
    liquidity = 10**18
    amount_out = 10**17
    assert get_next_sqrt_price_from_output(Q96, liquidity, amount_out, True) == (
        71305346262837903834189555302
    )
    assert get_next_sqrt_price_from_output(Q96, liquidity, amount_out, False) == (
        88031291682515930659493278152
    )


def test_official_sqrt_price_math_uint256_overflow_fallback_vector() -> None:
    sqrt_price = MAX_UINT160
    liquidity = MAX_UINT128
    amount = MAX_UINT256 - ((liquidity << 96) // sqrt_price)
    assert get_next_sqrt_price_from_input(sqrt_price, liquidity, amount, True) == 1


def test_official_sqrt_price_math_rejects_exact_virtual_reserve_output() -> None:
    sqrt_price = 20282409603651670423947251286016
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_output(sqrt_price, 1024, 4, False)
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_output(sqrt_price, 1024, 262144, True)


def test_official_swap_math_exact_input_capped_vector() -> None:
    # Uniswap v3-core test/SwapMath.spec.ts:
    # encodePriceSqrt(101, 100), liquidity=2e18, amount=1e18, fee=600.
    target = 79623317895830914510639640423
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, 2 * 10**18, 10**18, 600,
    )
    assert next_price == target
    assert amount_in == 9975124224178055
    assert fee == 5988667735148
    assert amount_out == 9925619580021728


def test_official_swap_math_exact_output_capped_vector() -> None:
    target = 79623317895830914510639640423
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, 2 * 10**18, -(10**18), 600,
    )
    assert next_price == target
    assert amount_in == 9975124224178055
    assert fee == 5988667735148
    assert amount_out == 9925619580021728


def test_official_swap_math_exact_input_partial_vector() -> None:
    # encodePriceSqrt(1000, 100), which is sqrt(10) * Q96 rounded down.
    target = 250541448375047931186413801569
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, 2 * 10**18, 10**18, 600,
    )
    assert next_price < target
    assert amount_in == 999400000000000000
    assert fee == 600000000000000
    assert amount_out == 666399946655997866


def test_official_swap_math_exact_output_partial_vector() -> None:
    target = 10 * Q96
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, 2 * 10**18, -(10**18), 600,
    )
    assert next_price < target
    assert amount_in == 2 * 10**18
    assert amount_out == 10**18
    assert fee == 1200720432259356


def test_official_swap_math_one_raw_unit_output_cap_vector() -> None:
    next_price, amount_in, amount_out, fee = compute_swap_step(
        417332158212080721273783715441582,
        1452870262520218020823638996,
        159344665391607089467575320103,
        -1,
        1,
    )
    assert next_price == 417332158212080721273783715441581
    assert amount_in == 1
    assert amount_out == 1
    assert fee == 1


def test_official_swap_math_entire_input_can_be_fee_vector() -> None:
    assert compute_swap_step(
        2413, 79887613182836312, 1985041575832132834610021537970, 10, 1872,
    ) == (2413, 0, 0, 10)


def test_official_swap_math_intermediate_insufficient_liquidity_vectors() -> None:
    sqrt_price = 20282409603651670423947251286016
    assert compute_swap_step(
        sqrt_price, sqrt_price * 11 // 10, 1024, -4, 3000,
    ) == (
        sqrt_price * 11 // 10,
        26215,
        0,
        79,
    )
    assert compute_swap_step(
        sqrt_price, sqrt_price * 9 // 10, 1024, -263000, 3000,
    ) == (
        sqrt_price * 9 // 10,
        1,
        26214,
        1,
    )
