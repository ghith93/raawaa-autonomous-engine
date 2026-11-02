"""Pure TickBitmap word traversal matching Uniswap V3 compressed-tick semantics."""
from __future__ import annotations

from .v3_math import MAX_TICK, MIN_TICK

MAX_UINT256 = (1 << 256) - 1
MIN_INT16 = -(1 << 15)
MAX_INT16 = (1 << 15) - 1


def _validate_tick(tick: int) -> int:
    if type(tick) is not int or not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError("tick must be an integer within the V3 global tick bounds")
    return tick


def _validate_spacing(tick_spacing: int) -> int:
    if type(tick_spacing) is not int or not 1 <= tick_spacing <= MAX_TICK:
        raise ValueError("tick_spacing must be a positive supported integer")
    return tick_spacing


def tick_bitmap_position(compressed_tick: int) -> tuple[int, int]:
    """Return signed int16 word position and uint8 bit position."""
    if type(compressed_tick) is not int or not -(1 << 23) <= compressed_tick < (1 << 23):
        raise ValueError("compressed_tick must fit int24")
    word_pos = compressed_tick >> 8
    bit_pos = compressed_tick & 0xFF
    if not MIN_INT16 <= word_pos <= MAX_INT16:
        raise ValueError("bitmap word position must fit int16")
    return word_pos, bit_pos


def next_initialized_tick_within_one_word(
    tick: int,
    tick_spacing: int,
    bitmap_word: int,
    lte: bool,
) -> tuple[int, bool]:
    """Return the next initialized tick in one supplied bitmap word.

    The returned boundary may be outside MIN_TICK..MAX_TICK, matching the
    core bitmap helper. A pool swap loop must clamp it to global tick bounds
    before passing it to TickMath.
    """
    current = _validate_tick(tick)
    spacing = _validate_spacing(tick_spacing)
    if type(bitmap_word) is not int or not 0 <= bitmap_word <= MAX_UINT256:
        raise ValueError("bitmap_word must be uint256")
    if type(lte) is not bool:
        raise ValueError("lte must be bool")

    # Python // is floor division, matching the explicit negative-tick
    # correction in v3-core's truncation-based integer division.
    compressed = current // spacing

    if lte:
        word_pos, bit_pos = tick_bitmap_position(compressed)
        mask = (1 << (bit_pos + 1)) - 1
        masked = bitmap_word & mask
        initialized = masked != 0
        if initialized:
            most_significant = masked.bit_length() - 1
            next_compressed = compressed - (bit_pos - most_significant)
        else:
            next_compressed = compressed - bit_pos
    else:
        compressed += 1
        word_pos, bit_pos = tick_bitmap_position(compressed)
        mask = MAX_UINT256 ^ ((1 << bit_pos) - 1)
        masked = bitmap_word & mask
        initialized = masked != 0
        if initialized:
            least_significant = (masked & -masked).bit_length() - 1
            next_compressed = compressed + (least_significant - bit_pos)
        else:
            next_compressed = compressed + (255 - bit_pos)

    # word_pos is intentionally computed to validate the int16 mapping key.
    _ = word_pos
    return next_compressed * spacing, initialized
