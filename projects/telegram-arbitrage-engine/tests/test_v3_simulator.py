from __future__ import annotations

import pytest

from arb_engine.sqrt_price_math import get_amount1_delta
from arb_engine.v3_math import Q96, get_sqrt_ratio_at_tick
from arb_engine.v3_simulator import (
    MissingTickData,
    V3PoolSnapshot,
    V3SnapshotError,
    simulate_v3_swap,
)

POOL = "0x0000000000000000000000000000000000000010"
TOKEN0 = "0x0000000000000000000000000000000000000001"
TOKEN1 = "0x0000000000000000000000000000000000000002"
BLOCK_HASH = "0x" + "ab" * 32


def snapshot(*, liquidity=100_000, bitmap=None, ticks=None):
    return V3PoolSnapshot(
        chain_id=1,
        pool_address=POOL,
        token0=TOKEN0,
        token1=TOKEN1,
        fee_pips=3000,
        tick_spacing=60,
        sqrt_price_x96=Q96,
        tick=0,
        liquidity=liquidity,
        block_number=20_000_000,
        block_hash=BLOCK_HASH,
        bitmap_words={0: (1 << 1) | (1 << 2)} if bitmap is None else bitmap,
        initialized_ticks={60: 500, 120: -500} if ticks is None else ticks,
    )


def test_exact_input_simulates_across_two_initialized_ticks() -> None:
    state = snapshot()
    result = simulate_v3_swap(
        state,
        amount_specified=10**9,
        zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
    )
    assert result.complete is True
    assert result.stop_reason == "price_limit_reached"
    assert result.crossed_ticks == (60, 120)
    assert result.tick == 120
    assert result.liquidity == 100_000
    assert result.amount_in > 0
    assert result.amount_out > 0
    assert result.fee_amount > 0
    assert result.amount_in + result.fee_amount > result.amount_in
    assert result.amount_remaining > 0
    assert result.block_number == state.block_number
    assert result.block_hash == BLOCK_HASH
    assert result.classification == "caller_supplied_snapshot_not_rpc_verified"


def test_exact_output_partial_step_fills_requested_amount_without_crossing() -> None:
    result = simulate_v3_swap(
        snapshot(),
        amount_specified=-(10**2),
        zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
    )
    assert result.complete is True
    assert result.stop_reason == "amount_filled"
    assert result.amount_out == 10**2
    assert result.amount_remaining == 0
    assert result.crossed_ticks == ()
    assert result.amount_in > 0
    assert result.fee_amount > 0


def test_zero_liquidity_gap_crosses_to_next_initialized_tick() -> None:
    state = snapshot(liquidity=1000, ticks={60: -1000, 120: 1000})
    result = simulate_v3_swap(
        state,
        amount_specified=10**9,
        zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(180),
    )
    assert result.complete is True
    assert result.crossed_ticks == (60, 120)
    assert result.liquidity == 1000
    assert result.tick == 180
    assert result.amount_out > 0


def test_step_limit_returns_explicitly_incomplete_result() -> None:
    result = simulate_v3_swap(
        snapshot(),
        amount_specified=10**9,
        zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        max_steps=1,
    )
    assert result.complete is False
    assert result.stop_reason == "step_limit"
    assert result.crossed_ticks == (60,)
    assert result.amount_remaining > 0


def test_missing_bitmap_word_fails_closed() -> None:
    state = snapshot(bitmap={})
    with pytest.raises(MissingTickData):
        simulate_v3_swap(
            state, amount_specified=1000, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        )


def test_missing_initialized_tick_liquidity_net_fails_closed() -> None:
    state = snapshot(ticks={})
    with pytest.raises(MissingTickData):
        simulate_v3_swap(
            state, amount_specified=10**9, zero_for_one=False,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        )


def test_zero_for_one_uses_leftward_bitmap_and_tick_crossing() -> None:
    # Tick -60 is represented by compressed tick -1, word -1, bit 255.
    state = V3PoolSnapshot(
        chain_id=1, pool_address=POOL, token0=TOKEN0, token1=TOKEN1,
        fee_pips=3000, tick_spacing=60, sqrt_price_x96=Q96, tick=0,
        liquidity=100_000, block_number=20_000_000, block_hash=BLOCK_HASH,
        bitmap_words={0: 0, -1: 1 << 255}, initialized_ticks={-60: 500},
    )
    result = simulate_v3_swap(
        state, amount_specified=10**9, zero_for_one=True,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(-60),
    )
    assert result.complete is True
    assert result.crossed_ticks == (-60,)
    assert result.tick == -61
    assert result.liquidity == 99_500


def test_invalid_snapshot_tick_price_relation_is_rejected() -> None:
    with pytest.raises(V3SnapshotError):
        V3PoolSnapshot(
            chain_id=1, pool_address=POOL, token0=TOKEN0, token1=TOKEN1,
            fee_pips=3000, tick_spacing=60, sqrt_price_x96=Q96, tick=1,
            liquidity=100_000, block_number=1, block_hash=BLOCK_HASH,
            bitmap_words={0: 0}, initialized_ticks={},
        )


def test_snapshot_requires_bitmap_bit_for_supplied_initialized_tick() -> None:
    with pytest.raises(V3SnapshotError):
        snapshot(bitmap={0: 0}, ticks={60: 500})


def test_price_limit_must_be_on_requested_side() -> None:
    with pytest.raises(ValueError):
        simulate_v3_swap(
            snapshot(), amount_specified=1000, zero_for_one=True,
            sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
        )


def test_zero_amount_is_a_complete_noop() -> None:
    result = simulate_v3_swap(
        snapshot(), amount_specified=0, zero_for_one=False,
        sqrt_price_limit_x96=get_sqrt_ratio_at_tick(120),
    )
    assert result.complete is True
    assert result.classification == "caller_supplied_snapshot_not_rpc_verified"
    assert result.steps == 0
    assert result.amount_in == result.amount_out == result.fee_amount == 0


def test_amount1_delta_helper_is_used_for_fixture_sanity() -> None:
    assert get_amount1_delta(Q96, get_sqrt_ratio_at_tick(60), 100_000, True) > 0

def test_snapshot_rejects_zero_pool_or_token_address() -> None:
    with pytest.raises(V3SnapshotError):
        V3PoolSnapshot(
            chain_id=1, pool_address="0x" + "00" * 20, token0=TOKEN0, token1=TOKEN1,
            fee_pips=3000, tick_spacing=60, sqrt_price_x96=Q96, tick=0,
            liquidity=100_000, block_number=1, block_hash=BLOCK_HASH,
            bitmap_words={0: 0}, initialized_ticks={},
        )
