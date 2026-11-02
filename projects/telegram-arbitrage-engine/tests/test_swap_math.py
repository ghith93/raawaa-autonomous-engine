from __future__ import annotations

import pytest

from arb_engine.v3_math import Q96
from arb_engine.sqrt_price_math import get_amount0_delta, get_amount1_delta
from arb_engine.swap_math import FEE_DENOMINATOR, compute_swap_step


LIQUIDITY = 10**18


def test_exact_input_token0_to_token1_reaches_target_and_accounts_fee() -> None:
    target = Q96 - Q96 // 100
    amount_to_target = get_amount0_delta(target, Q96, LIQUIDITY, True)
    amount_remaining = amount_to_target * 2
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, LIQUIDITY, amount_remaining, 3000,
    )
    assert next_price == target
    assert amount_in == amount_to_target
    assert amount_out == get_amount1_delta(target, Q96, LIQUIDITY, False)
    assert fee == (amount_in * 3000 + (FEE_DENOMINATOR - 3000) - 1) // (FEE_DENOMINATOR - 3000)


def test_exact_input_partial_step_consumes_remainder_as_fee() -> None:
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, Q96 // 2, LIQUIDITY, 10**12, 3000,
    )
    assert Q96 // 2 < next_price < Q96
    assert amount_in + fee == 10**12
    assert amount_out > 0


def test_exact_output_token1_reaches_target_and_caps_requested_output() -> None:
    target = Q96 - Q96 // 100
    available = get_amount1_delta(target, Q96, LIQUIDITY, False)
    requested = available + 100
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, target, LIQUIDITY, -requested, 500,
    )
    assert next_price == target
    assert amount_out == available
    assert amount_in > 0
    assert fee > 0


def test_exact_output_partial_step_returns_exact_requested_output() -> None:
    requested = 10**12
    next_price, amount_in, amount_out, fee = compute_swap_step(
        Q96, Q96 // 2, LIQUIDITY, -requested, 3000,
    )
    assert Q96 // 2 < next_price < Q96
    assert amount_out == requested
    assert amount_in > 0
    assert fee == (amount_in * 3000 + 996999) // 997000


def test_zero_fee_exact_input_has_zero_fee() -> None:
    _, _, _, fee = compute_swap_step(Q96, Q96 // 2, LIQUIDITY, 10**12, 0)
    assert fee == 0


@pytest.mark.parametrize("fee", [-1, 1_000_000, 1 << 24, True, 1.0])
def test_invalid_fee_is_rejected(fee) -> None:
    with pytest.raises(ValueError):
        compute_swap_step(Q96, Q96 // 2, LIQUIDITY, 1000, fee)


@pytest.mark.parametrize("amount", [True, 1.0, "10", 1 << 255, -(1 << 255) - 1])
def test_invalid_amount_remaining_is_rejected(amount) -> None:
    with pytest.raises(ValueError):
        compute_swap_step(Q96, Q96 // 2, LIQUIDITY, amount, 3000)


def test_zero_liquidity_and_zero_target_are_rejected() -> None:
    with pytest.raises(ValueError):
        compute_swap_step(Q96, Q96 // 2, 0, 1000, 3000)
    with pytest.raises(ValueError):
        compute_swap_step(Q96, 0, LIQUIDITY, 1000, 3000)
