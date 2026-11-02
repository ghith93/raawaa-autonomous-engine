"""Exact integer SqrtPriceMath primitives for Uniswap V3-style Q64.96 prices.

Pure arithmetic only. This module does not read pool state, traverse ticks,
calculate swap fees, or claim to quote a complete swap. Bounds and rounding
follow the public v3-core SqrtPriceMath equations.
"""
from __future__ import annotations

from .v3_math import Q96

MAX_UINT128 = (1 << 128) - 1
MAX_UINT160 = (1 << 160) - 1
MAX_UINT256 = (1 << 256) - 1


def _uint(value: int, bits: int, name: str, *, allow_zero: bool = True) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1) or value >= (1 << bits):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {qualifier} uint{bits}")
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0 or numerator < 0:
        raise ValueError("ceil division requires a non-negative numerator and positive denominator")
    return (numerator + denominator - 1) // denominator


def get_amount0_delta(
    sqrt_ratio_a_x96: int,
    sqrt_ratio_b_x96: int,
    liquidity: int,
    round_up: bool,
) -> int:
    """Token0 delta between two Q64.96 prices, with protocol-compatible rounding."""
    a = _uint(sqrt_ratio_a_x96, 160, "sqrt_ratio_a_x96", allow_zero=False)
    b = _uint(sqrt_ratio_b_x96, 160, "sqrt_ratio_b_x96", allow_zero=False)
    liq = _uint(liquidity, 128, "liquidity")
    if type(round_up) is not bool:
        raise ValueError("round_up must be bool")
    lower, upper = sorted((a, b))
    numerator1 = liq << 96
    numerator2 = upper - lower
    if round_up:
        # Keep the two explicit rounding-up stages used by v3-core.
        return _ceil_div(_ceil_div(numerator1 * numerator2, upper), lower)
    return (numerator1 * numerator2 // upper) // lower


def get_amount1_delta(
    sqrt_ratio_a_x96: int,
    sqrt_ratio_b_x96: int,
    liquidity: int,
    round_up: bool,
) -> int:
    """Token1 delta between two Q64.96 prices, with protocol-compatible rounding."""
    a = _uint(sqrt_ratio_a_x96, 160, "sqrt_ratio_a_x96", allow_zero=False)
    b = _uint(sqrt_ratio_b_x96, 160, "sqrt_ratio_b_x96", allow_zero=False)
    liq = _uint(liquidity, 128, "liquidity")
    if type(round_up) is not bool:
        raise ValueError("round_up must be bool")
    numerator = liq * abs(b - a)
    return _ceil_div(numerator, Q96) if round_up else numerator // Q96


def _next_sqrt_from_amount0(
    sqrt_price_x96: int, liquidity: int, amount: int, *, add: bool,
) -> int:
    if amount == 0:
        return sqrt_price_x96
    numerator1 = liquidity << 96
    product = amount * sqrt_price_x96
    if add:
        if product <= MAX_UINT256:
            denominator = numerator1 + product
            if denominator <= MAX_UINT256 and denominator >= numerator1:
                result = _ceil_div(numerator1 * sqrt_price_x96, denominator)
            else:
                # Match v3-core's overflow-safe fallback, including its
                # intermediate floor division before the final ceil.
                result = _ceil_div(numerator1, numerator1 // sqrt_price_x96 + amount)
        else:
            result = _ceil_div(numerator1, numerator1 // sqrt_price_x96 + amount)
    else:
        if product > MAX_UINT256 or numerator1 <= product:
            raise ValueError("amount removes all available virtual token0 liquidity")
        denominator = numerator1 - product
        result = _ceil_div(numerator1 * sqrt_price_x96, denominator)
    return _uint(result, 160, "next sqrt price", allow_zero=False)


def _next_sqrt_from_amount1(
    sqrt_price_x96: int, liquidity: int, amount: int, *, add: bool,
) -> int:
    if amount == 0:
        return sqrt_price_x96
    quotient = (
        amount * Q96 // liquidity
        if add
        else _ceil_div(amount * Q96, liquidity)
    )
    result = sqrt_price_x96 + quotient if add else sqrt_price_x96 - quotient
    return _uint(result, 160, "next sqrt price", allow_zero=False)


def get_next_sqrt_price_from_input(
    sqrt_price_x96: int, liquidity: int, amount_in: int, zero_for_one: bool,
) -> int:
    """Next price for exact input; rounds to avoid moving beyond the input budget."""
    price = _uint(sqrt_price_x96, 160, "sqrt_price_x96", allow_zero=False)
    liq = _uint(liquidity, 128, "liquidity", allow_zero=False)
    amount = _uint(amount_in, 256, "amount_in")
    if type(zero_for_one) is not bool:
        raise ValueError("zero_for_one must be bool")
    return (
        _next_sqrt_from_amount0(price, liq, amount, add=True)
        if zero_for_one
        else _next_sqrt_from_amount1(price, liq, amount, add=True)
    )


def get_next_sqrt_price_from_output(
    sqrt_price_x96: int, liquidity: int, amount_out: int, zero_for_one: bool,
) -> int:
    """Next price for exact output; rounds to ensure the requested output is covered."""
    price = _uint(sqrt_price_x96, 160, "sqrt_price_x96", allow_zero=False)
    liq = _uint(liquidity, 128, "liquidity", allow_zero=False)
    amount = _uint(amount_out, 256, "amount_out")
    if type(zero_for_one) is not bool:
        raise ValueError("zero_for_one must be bool")
    return (
        _next_sqrt_from_amount1(price, liq, amount, add=False)
        if zero_for_one
        else _next_sqrt_from_amount0(price, liq, amount, add=False)
    )
