from __future__ import annotations

import pytest

from arb_engine.v3_math import (
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
    Q96,
    get_sqrt_ratio_at_tick,
    get_tick_at_sqrt_ratio,
)


@pytest.mark.parametrize(
    ("tick", "expected"),
    [
        (MIN_TICK, MIN_SQRT_RATIO),
        (-1, 79224201403219477170569942574),
        (0, Q96),
        (1, 79232123823359799118286999568),
        (MAX_TICK, MAX_SQRT_RATIO),
    ],
)
def test_tick_to_sqrt_ratio_matches_protocol_vectors(tick: int, expected: int) -> None:
    assert get_sqrt_ratio_at_tick(tick) == expected


@pytest.mark.parametrize("tick", [MIN_TICK, -800000, -120, -1, 0, 1, 120, 800000, MAX_TICK - 1])
def test_sqrt_ratio_inverse_returns_greatest_tick(tick: int) -> None:
    ratio = get_sqrt_ratio_at_tick(tick)
    assert get_tick_at_sqrt_ratio(ratio) == tick
    if tick < MAX_TICK - 1:
        assert get_sqrt_ratio_at_tick(tick + 1) > ratio


def test_inverse_uses_half_open_upper_bound() -> None:
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(MAX_SQRT_RATIO)


@pytest.mark.parametrize("tick", [MIN_TICK - 1, MAX_TICK + 1, True, 1.0, "1"])
def test_tick_input_rejects_out_of_range_and_non_integer_values(tick) -> None:
    with pytest.raises(ValueError):
        get_sqrt_ratio_at_tick(tick)


@pytest.mark.parametrize("ratio", [MIN_SQRT_RATIO - 1, MAX_SQRT_RATIO, True, 1.0, "79228162514264337593543950336"])
def test_inverse_rejects_invalid_ratio_values(ratio) -> None:
    with pytest.raises(ValueError):
        get_tick_at_sqrt_ratio(ratio)
