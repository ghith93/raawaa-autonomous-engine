from __future__ import annotations

import pytest

from arb_engine.liquidity_math import (
    MAX_UINT128,
    apply_tick_crossing_liquidity,
    add_liquidity_delta,
)


def test_add_liquidity_delta_handles_positive_and_negative_changes() -> None:
    assert add_liquidity_delta(100, 25) == 125
    assert add_liquidity_delta(100, -25) == 75


def test_crossing_right_adds_liquidity_net() -> None:
    assert apply_tick_crossing_liquidity(100, 25, False) == 125


def test_crossing_left_subtracts_liquidity_net() -> None:
    assert apply_tick_crossing_liquidity(100, 25, True) == 75
    assert apply_tick_crossing_liquidity(100, -25, True) == 125


@pytest.mark.parametrize(
    ("liquidity", "delta"),
    [(0, -1), (MAX_UINT128, 1), (-1, 1), (True, 1), (1, 1 << 127)],
)
def test_invalid_or_out_of_range_liquidity_changes_are_rejected(liquidity, delta) -> None:
    with pytest.raises(ValueError):
        add_liquidity_delta(liquidity, delta)
