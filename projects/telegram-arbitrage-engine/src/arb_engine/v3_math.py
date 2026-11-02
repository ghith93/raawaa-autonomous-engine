"""Exact integer TickMath primitives for Uniswap V3-style concentrated liquidity.

This module implements the public protocol equations in Q64.96 fixed-point
arithmetic. It is deliberately math-only: no RPC, pool discovery, quoting,
or execution behavior is implied. See docs/research/v3-math-foundation.md.
"""
from __future__ import annotations

Q96 = 1 << 96
Q128 = 1 << 128
MAX_UINT256 = (1 << 256) - 1

MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

# Fixed-point factors for sqrt(1.0001 ** (2 ** bit)) in Q128.128.
# They are protocol constants; using integer multiplication preserves the
# exact rounding behavior required by the on-chain Q64.96 representation.
_TICK_FACTORS = (
    0xfffcb933bd6fad37aa2d162d1a594001,
    0xfff97272373d413259a46990580e213a,
    0xfff2e50f5f656932ef12357cf3c7fdcc,
    0xffe5caca7e10e4e61c3624eaa0941cd0,
    0xffcb9843d60f6159c9db58835c926644,
    0xff973b41fa98c081472e6896dfb254c0,
    0xff2ea16466c96a3843ec78b326b52861,
    0xfe5dee046a99a2a811c461f1969c3053,
    0xfcbe86c7900a88aedcffc83b479aa3a4,
    0xf987a7253ac413176f2b074cf7815e54,
    0xf3392b0822b70005940c7a398e4b70f3,
    0xe7159475a2c29b7443b29c7fa6e889d9,
    0xd097f3bdfd2022b8845ad8f792aa5825,
    0xa9f746462d870fdf8a65dc1f90e061e5,
    0x70d869a156d2a1b890bb3df62baf32f7,
    0x31be135f97d08fd981231505542fcfa6,
    0x9aa508b5b7a84e1c677de54f3e99bc9,
    0x5d6af8dedb81196699c329225ee604,
    0x2216e584f5fa1ea926041bedfe98,
    0x48a170391f7dc42444e8fa2,
)


def get_sqrt_ratio_at_tick(tick: int) -> int:
    """Return ceil(sqrt(1.0001**tick) * 2**96), exactly as an integer."""
    if type(tick) is not int or not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError("tick must be an integer between MIN_TICK and MAX_TICK")

    absolute = abs(tick)
    ratio = Q128
    bit = 0
    while absolute:
        if absolute & 1:
            ratio = (ratio * _TICK_FACTORS[bit]) >> 128
        absolute >>= 1
        bit += 1

    if tick > 0:
        ratio = MAX_UINT256 // ratio

    # Convert Q128.128 to Q128.96, rounding upward to preserve the
    # invariant that the inverse operation returns the originating tick.
    result = (ratio >> 32) + (1 if ratio & ((1 << 32) - 1) else 0)
    if not MIN_SQRT_RATIO <= result <= MAX_SQRT_RATIO:
        raise ArithmeticError("computed sqrt ratio is outside protocol bounds")
    return result


def get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int:
    """Return the greatest tick whose sqrt ratio is <= the supplied ratio.

    The maximum ratio is exclusive, matching the protocol's inverse domain.
    Binary search is intentionally used here: it is compact, auditable and
    bounded to at most 21 iterations, rather than relying on floating logs.
    """
    if type(sqrt_price_x96) is not int or not (
        MIN_SQRT_RATIO <= sqrt_price_x96 < MAX_SQRT_RATIO
    ):
        raise ValueError("sqrt_price_x96 is outside the supported inverse domain")

    low, high = MIN_TICK, MAX_TICK - 1
    while low < high:
        middle = (low + high + 1) // 2
        if get_sqrt_ratio_at_tick(middle) <= sqrt_price_x96:
            low = middle
        else:
            high = middle - 1
    return low
