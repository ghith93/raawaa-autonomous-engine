"""Single-range V3 swap-step arithmetic with integer fee and rounding semantics.

This is one price-range step only. It does not traverse initialized ticks,
apply liquidityNet, fetch pool state, or quote an entire pool swap.
"""
from __future__ import annotations

from .sqrt_price_math import (
    MAX_UINT128,
    MAX_UINT160,
    MAX_UINT256,
    get_amount0_delta,
    get_amount1_delta,
    get_next_sqrt_price_from_input,
    get_next_sqrt_price_from_output,
)

FEE_DENOMINATOR = 1_000_000
MAX_UINT24 = (1 << 24) - 1
MIN_INT256 = -(1 << 255)
MAX_INT256 = (1 << 255) - 1


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def compute_swap_step(
    sqrt_ratio_current_x96: int,
    sqrt_ratio_target_x96: int,
    liquidity: int,
    amount_remaining: int,
    fee_pips: int,
) -> tuple[int, int, int, int]:
    """Return (next_sqrt_price_x96, amount_in, amount_out, fee_amount).

    Positive amount_remaining means exact input; negative means exact output.
    The target is a hard price boundary for this single step.
    """
    current = sqrt_ratio_current_x96
    target = sqrt_ratio_target_x96
    if type(current) is not int or not 0 < current <= MAX_UINT160:
        raise ValueError("current sqrt price must be a positive uint160")
    if type(target) is not int or not 0 < target <= MAX_UINT160:
        raise ValueError("target sqrt price must be a positive uint160")
    if type(liquidity) is not int or not 0 < liquidity <= MAX_UINT128:
        raise ValueError("liquidity must be a positive uint128")
    if type(amount_remaining) is not int or not MIN_INT256 <= amount_remaining <= MAX_INT256:
        raise ValueError("amount_remaining must fit int256")
    if type(fee_pips) is not int or not 0 <= fee_pips < FEE_DENOMINATOR or fee_pips > MAX_UINT24:
        raise ValueError("fee_pips must be a uint24 below the 1,000,000 denominator")

    zero_for_one = current >= target
    exact_input = amount_remaining >= 0

    if exact_input:
        amount_less_fee = amount_remaining * (FEE_DENOMINATOR - fee_pips) // FEE_DENOMINATOR
        amount_in_to_target = (
            get_amount0_delta(target, current, liquidity, True)
            if zero_for_one
            else get_amount1_delta(current, target, liquidity, True)
        )
        if amount_less_fee >= amount_in_to_target:
            next_price = target
        else:
            next_price = get_next_sqrt_price_from_input(
                current, liquidity, amount_less_fee, zero_for_one,
            )
    else:
        amount_out_to_target = (
            get_amount1_delta(target, current, liquidity, False)
            if zero_for_one
            else get_amount0_delta(current, target, liquidity, False)
        )
        if -amount_remaining >= amount_out_to_target:
            next_price = target
        else:
            next_price = get_next_sqrt_price_from_output(
                current, liquidity, -amount_remaining, zero_for_one,
            )

    max_reached = next_price == target
    if zero_for_one:
        amount_in = (
            amount_in_to_target
            if max_reached and exact_input
            else get_amount0_delta(next_price, current, liquidity, True)
        )
        amount_out = (
            amount_out_to_target
            if max_reached and not exact_input
            else get_amount1_delta(next_price, current, liquidity, False)
        )
    else:
        amount_in = (
            amount_in_to_target
            if max_reached and exact_input
            else get_amount1_delta(current, next_price, liquidity, True)
        )
        amount_out = (
            amount_out_to_target
            if max_reached and not exact_input
            else get_amount0_delta(current, next_price, liquidity, False)
        )

    if not exact_input and amount_out > -amount_remaining:
        amount_out = -amount_remaining

    if exact_input and not max_reached:
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = _ceil_div(amount_in * fee_pips, FEE_DENOMINATOR - fee_pips)

    if any(type(value) is not int or value < 0 or value > MAX_UINT256
           for value in (amount_in, amount_out, fee_amount)):
        raise ArithmeticError("swap-step amount is outside uint256 bounds")
    return next_price, amount_in, amount_out, fee_amount
