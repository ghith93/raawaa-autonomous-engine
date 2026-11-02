"""Signed V3 liquidity update arithmetic."""
from __future__ import annotations

MAX_UINT128 = (1 << 128) - 1
MIN_INT128 = -(1 << 127)
MAX_INT128 = (1 << 127) - 1


def add_liquidity_delta(liquidity: int, liquidity_delta: int) -> int:
    """Apply an int128 delta to uint128 liquidity with checked bounds."""
    if type(liquidity) is not int or not 0 <= liquidity <= MAX_UINT128:
        raise ValueError("liquidity must be uint128")
    if type(liquidity_delta) is not int or not MIN_INT128 <= liquidity_delta <= MAX_INT128:
        raise ValueError("liquidity_delta must be int128")
    result = liquidity + liquidity_delta
    if not 0 <= result <= MAX_UINT128:
        raise ValueError("liquidity delta underflows or overflows uint128")
    return result


def apply_tick_crossing_liquidity(
    liquidity: int,
    liquidity_net: int,
    zero_for_one: bool,
) -> int:
    """Apply liquidityNet when crossing a tick in the swap direction.

    Crossing leftward (zeroForOne) negates liquidityNet before applying it.
    """
    if type(zero_for_one) is not bool:
        raise ValueError("zero_for_one must be bool")
    delta = -liquidity_net if zero_for_one else liquidity_net
    # Negating int128 minimum is outside int128, but the resulting arithmetic
    # still must fit uint128; the protocol widens before sign inversion.
    return add_liquidity_delta(liquidity, delta)
