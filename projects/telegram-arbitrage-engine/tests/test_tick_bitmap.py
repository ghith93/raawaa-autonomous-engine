from __future__ import annotations

import pytest

from arb_engine.tick_bitmap import (
    next_initialized_tick_within_one_word,
    tick_bitmap_position,
)


def test_position_uses_signed_word_and_unsigned_bit_for_negative_ticks() -> None:
    assert tick_bitmap_position(-1) == (-1, 255)
    assert tick_bitmap_position(-256) == (-1, 0)
    assert tick_bitmap_position(256) == (1, 0)


def test_lte_returns_most_significant_initialized_bit_at_or_below_tick() -> None:
    bitmap = (1 << 2) | (1 << 5) | (1 << 9)
    assert next_initialized_tick_within_one_word(10, 1, bitmap, True) == (9, True)
    assert next_initialized_tick_within_one_word(5, 1, bitmap, True) == (5, True)


def test_lte_empty_word_returns_left_boundary() -> None:
    assert next_initialized_tick_within_one_word(10, 1, 0, True) == (0, False)


def test_gt_returns_least_significant_initialized_bit_above_tick() -> None:
    bitmap = (1 << 2) | (1 << 5) | (1 << 9)
    assert next_initialized_tick_within_one_word(1, 1, bitmap, False) == (2, True)
    assert next_initialized_tick_within_one_word(5, 1, bitmap, False) == (9, True)


def test_gt_empty_word_returns_right_boundary() -> None:
    assert next_initialized_tick_within_one_word(10, 1, 0, False) == (255, False)


def test_negative_tick_compression_matches_floor_semantics() -> None:
    assert next_initialized_tick_within_one_word(-1, 10, 1 << 255, True) == (-10, True)
    assert next_initialized_tick_within_one_word(-1, 10, 1, False) == (0, True)
    assert next_initialized_tick_within_one_word(-1, 10, 0, True) == (-2560, False)


def test_search_starts_in_next_word_when_moving_right_from_word_end() -> None:
    assert next_initialized_tick_within_one_word(255, 1, 1, False) == (256, True)


def test_tick_spacing_scales_raw_tick_distance() -> None:
    bitmap = 1 << 4
    assert next_initialized_tick_within_one_word(20, 5, bitmap, False) == (20, True)


@pytest.mark.parametrize("tick", [True, 1.0, "1", -887273, 887273])
def test_invalid_ticks_are_rejected(tick) -> None:
    with pytest.raises(ValueError):
        next_initialized_tick_within_one_word(tick, 1, 0, True)


@pytest.mark.parametrize("bitmap", [True, -1, 1 << 256, 1.0])
def test_invalid_bitmap_words_are_rejected(bitmap) -> None:
    with pytest.raises(ValueError):
        next_initialized_tick_within_one_word(0, 1, bitmap, True)
