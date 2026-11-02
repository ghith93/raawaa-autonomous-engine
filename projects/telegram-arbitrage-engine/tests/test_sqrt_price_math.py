from __future__ import annotations

import pytest

from arb_engine.v3_math import Q96
from arb_engine.sqrt_price_math import (
    MAX_UINT128,
    MAX_UINT160,
    get_amount0_delta,
    get_amount1_delta,
    get_next_sqrt_price_from_input,
    get_next_sqrt_price_from_output,
)


def test_amount_deltas_at_unit_price_are_exact() -> None:
    assert get_amount0_delta(Q96, 2 * Q96, 1, False) == Q96 // 2
    assert get_amount1_delta(Q96, 2 * Q96, 1, False) == Q96


def test_amount_delta_round_up_is_never_below_round_down() -> None:
    for lower, upper, liquidity in [
        (Q96, 2 * Q96, 1),
        (Q96, Q96 + 1, 1),
        (Q96 // 3, Q96 * 3, 123456789),
        (123456789, 987654321, MAX_UINT128),
    ]:
        assert get_amount0_delta(lower, upper, liquidity, True) >= get_amount0_delta(lower, upper, liquidity, False)
        assert get_amount1_delta(lower, upper, liquidity, True) >= get_amount1_delta(lower, upper, liquidity, False)


def test_amount_deltas_are_order_independent() -> None:
    assert get_amount0_delta(Q96, 3 * Q96, 1000, True) == get_amount0_delta(3 * Q96, Q96, 1000, True)
    assert get_amount1_delta(Q96, 3 * Q96, 1000, False) == get_amount1_delta(3 * Q96, Q96, 1000, False)


def test_input_token0_decreases_price_and_token1_increases_price() -> None:
    token0_input = get_next_sqrt_price_from_input(Q96, 10**18, 10**15, True)
    token1_input = get_next_sqrt_price_from_input(Q96, 10**18, 10**15, False)
    assert Q96 < token0_input < Q96 + 1  # tiny but positive movement rounds in Q64.96
    assert token1_input > Q96


def test_output_token1_decreases_price_and_token0_increases_price() -> None:
    token1_output = get_next_sqrt_price_from_output(Q96, 10**18, 10**15, True)
    token0_output = get_next_sqrt_price_from_output(Q96, 10**18, 10**15, False)
    assert token1_output < Q96
    assert token0_output > Q96


def test_zero_amount_preserves_exact_price() -> None:
    assert get_next_sqrt_price_from_input(Q96, 1, 0, True) == Q96
    assert get_next_sqrt_price_from_output(Q96, 1, 0, False) == Q96


@pytest.mark.parametrize("bad", [True, 1.0, "1", -1, 1 << 160])
def test_invalid_price_inputs_are_rejected(bad) -> None:
    with pytest.raises(ValueError):
        get_amount0_delta(bad, Q96, 1, True)


def test_zero_liquidity_and_overflowing_output_are_rejected() -> None:
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_input(Q96, 0, 1, True)
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_output(Q96, 1, 1 << 255, True)


def test_output_token0_rejects_non_positive_denominator() -> None:
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_output(Q96, 1, 1 << 160, False)


def test_next_price_must_fit_uint160() -> None:
    with pytest.raises(ValueError):
        get_next_sqrt_price_from_input(MAX_UINT160, MAX_UINT128, (1 << 256) - 1, False)
