"""Bounded multi-tick V3 swap simulation over an explicit caller-supplied snapshot.

This module performs no RPC calls and does not discover pool data. Callers
must provide all bitmap words and initialized tick liquidityNet values needed
for the simulated path, pinned to one block. Missing data fails closed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from .liquidity_math import apply_tick_crossing_liquidity
from .sqrt_price_math import MAX_UINT128
from .swap_math import compute_swap_step
from .tick_bitmap import (
    MAX_UINT256,
    MAX_INT16,
    MIN_INT16,
    next_initialized_tick_within_one_word,
    tick_bitmap_position,
)
from .v3_math import (
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
    get_sqrt_ratio_at_tick,
    get_tick_at_sqrt_ratio,
)


class V3SnapshotError(ValueError):
    """A caller-supplied V3 state snapshot is incomplete or inconsistent."""


class MissingTickData(V3SnapshotError):
    """A bitmap word or initialized tick record required for the path is absent."""


@dataclass(frozen=True, slots=True)
class V3PoolSnapshot:
    chain_id: int
    pool_address: str
    token0: str
    token1: str
    fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    block_number: int
    block_hash: str
    bitmap_words: Mapping[int, int]
    initialized_ticks: Mapping[int, int]
    rpc_verified: bool = False
    source: str = "caller_supplied"

    def __post_init__(self) -> None:
        if type(self.chain_id) is not int or self.chain_id <= 0:
            raise V3SnapshotError("chain_id must be a positive integer")
        address = re.compile(r"^0x[0-9a-fA-F]{40}$")
        if not isinstance(self.pool_address, str) or not address.fullmatch(self.pool_address):
            raise V3SnapshotError("pool_address must be a 20-byte EVM address")
        if not all(isinstance(token, str) and address.fullmatch(token) for token in (self.token0, self.token1)):
            raise V3SnapshotError("token addresses must be 20-byte EVM addresses")
        if any(int(value[2:], 16) == 0 for value in (self.pool_address, self.token0, self.token1)):
            raise V3SnapshotError("pool and token addresses cannot be the zero address")
        if self.token0.lower() >= self.token1.lower():
            raise V3SnapshotError("token0 and token1 must be distinct and address-sorted")
        if type(self.fee_pips) is not int or not 0 <= self.fee_pips < 1_000_000:
            raise V3SnapshotError("fee_pips must be below the V3 fee denominator")
        if type(self.tick_spacing) is not int or not 1 <= self.tick_spacing <= MAX_TICK:
            raise V3SnapshotError("tick_spacing must be a positive supported integer")
        if type(self.sqrt_price_x96) is not int or not MIN_SQRT_RATIO <= self.sqrt_price_x96 < MAX_SQRT_RATIO:
            raise V3SnapshotError("sqrt_price_x96 is outside the V3 price domain")
        if type(self.tick) is not int or not MIN_TICK <= self.tick <= MAX_TICK:
            raise V3SnapshotError("tick is outside the V3 global tick bounds")
        derived_tick = get_tick_at_sqrt_ratio(self.sqrt_price_x96)
        exact_boundary = self.sqrt_price_x96 == get_sqrt_ratio_at_tick(derived_tick)
        allowed_ticks = {derived_tick}
        if exact_boundary and derived_tick > MIN_TICK:
            # After a leftward crossing, pool slot0.tick is boundaryTick - 1.
            allowed_ticks.add(derived_tick - 1)
        if self.tick not in allowed_ticks:
            raise V3SnapshotError("tick is inconsistent with sqrt_price_x96")
        if type(self.liquidity) is not int or not 0 <= self.liquidity <= MAX_UINT128:
            raise V3SnapshotError("liquidity must be uint128")
        if type(self.block_number) is not int or self.block_number < 0:
            raise V3SnapshotError("block_number must be non-negative")
        if not isinstance(self.block_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", self.block_hash):
            raise V3SnapshotError("block_hash must be a 32-byte hexadecimal hash")
        if not isinstance(self.bitmap_words, Mapping) or not isinstance(self.initialized_ticks, Mapping):
            raise V3SnapshotError("bitmap_words and initialized_ticks must be mappings")
        if type(self.rpc_verified) is not bool:
            raise V3SnapshotError("rpc_verified must be bool")
        if not isinstance(self.source, str) or not self.source.strip():
            raise V3SnapshotError("snapshot source label is required")

        words = dict(self.bitmap_words)
        ticks = dict(self.initialized_ticks)
        for word_pos, bitmap in words.items():
            if type(word_pos) is not int or not MIN_INT16 <= word_pos <= MAX_INT16:
                raise V3SnapshotError("bitmap word keys must fit signed int16")
            if type(bitmap) is not int or not 0 <= bitmap <= MAX_UINT256:
                raise V3SnapshotError("bitmap word values must fit uint256")
        for tick, liquidity_net in ticks.items():
            if type(tick) is not int or not MIN_TICK <= tick <= MAX_TICK:
                raise V3SnapshotError("initialized tick keys must be within global tick bounds")
            if tick % self.tick_spacing != 0:
                raise V3SnapshotError("initialized tick is not aligned to pool tick spacing")
            if type(liquidity_net) is not int or not -(1 << 127) <= liquidity_net < (1 << 127):
                raise V3SnapshotError("liquidityNet values must fit int128")
            compressed = tick // self.tick_spacing
            word_pos, bit_pos = tick_bitmap_position(compressed)
            if word_pos in words and not (words[word_pos] & (1 << bit_pos)):
                raise V3SnapshotError("initialized tick record is absent from its supplied bitmap word")
        object.__setattr__(self, "bitmap_words", MappingProxyType(words))
        object.__setattr__(self, "initialized_ticks", MappingProxyType(ticks))


@dataclass(frozen=True, slots=True)
class V3SwapResult:
    chain_id: int
    pool_address: str
    block_number: int
    block_hash: str
    zero_for_one: bool
    exact_input: bool
    amount_specified: int
    amount_in: int
    amount_out: int
    fee_amount: int
    amount_remaining: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    crossed_ticks: tuple[int, ...]
    steps: int
    complete: bool
    stop_reason: str
    classification: str


def simulate_v3_swap(
    snapshot: V3PoolSnapshot,
    *,
    amount_specified: int,
    zero_for_one: bool,
    sqrt_price_limit_x96: int,
    max_steps: int = 512,
) -> V3SwapResult:
    """Simulate exact input (positive) or exact output (negative) across ticks.

    All required bitmap words and initialized tick records must be included
    in snapshot and pinned to its block. The result is math-only and does not
    validate that the supplied snapshot came from a canonical RPC response.
    """
    if not isinstance(snapshot, V3PoolSnapshot):
        raise ValueError("snapshot must be a validated V3PoolSnapshot")
    if type(amount_specified) is not int or not -(1 << 255) <= amount_specified < (1 << 255):
        raise ValueError("amount_specified must fit int256")
    if type(zero_for_one) is not bool:
        raise ValueError("zero_for_one must be bool")
    if type(sqrt_price_limit_x96) is not int or not MIN_SQRT_RATIO < sqrt_price_limit_x96 < MAX_SQRT_RATIO:
        raise ValueError("sqrt_price_limit_x96 must be strictly within global price bounds")
    if zero_for_one and not sqrt_price_limit_x96 < snapshot.sqrt_price_x96:
        raise ValueError("zeroForOne price limit must be below the current price")
    if not zero_for_one and not sqrt_price_limit_x96 > snapshot.sqrt_price_x96:
        raise ValueError("oneForZero price limit must be above the current price")
    if type(max_steps) is not int or not 1 <= max_steps <= 2048:
        raise ValueError("max_steps must be between 1 and 2048")

    current_price = snapshot.sqrt_price_x96
    current_tick = snapshot.tick
    liquidity = snapshot.liquidity
    remaining = amount_specified
    exact_input = amount_specified >= 0
    total_in = total_out = total_fee = steps = 0
    crossed: list[int] = []

    if amount_specified == 0:
        return V3SwapResult(
            snapshot.chain_id, snapshot.pool_address, snapshot.block_number, snapshot.block_hash,
            zero_for_one, True, amount_specified, 0, 0, 0, 0, current_price,
            current_tick, liquidity, (), 0, True, "amount_filled",
            (
                "rpc_snapshot_factory_registered_block_hash_rechecked"
                if snapshot.rpc_verified else "caller_supplied_snapshot_not_rpc_verified"
            ),
        )

    stop_reason = "step_limit"
    while steps < max_steps and remaining != 0 and current_price != sqrt_price_limit_x96:
        compressed = current_tick // snapshot.tick_spacing
        search_tick = compressed if zero_for_one else compressed + 1
        word_pos, _ = tick_bitmap_position(search_tick)
        try:
            bitmap_word = snapshot.bitmap_words[word_pos]
        except KeyError as exc:
            raise MissingTickData(f"missing bitmap word {word_pos} at block {snapshot.block_number}") from exc

        next_tick, initialized = next_initialized_tick_within_one_word(
            current_tick, snapshot.tick_spacing, bitmap_word, zero_for_one,
        )
        bounded_tick = min(MAX_TICK, max(MIN_TICK, next_tick))
        if bounded_tick != next_tick:
            initialized = False
        sqrt_at_tick = get_sqrt_ratio_at_tick(bounded_tick)
        target_price = (
            max(sqrt_at_tick, sqrt_price_limit_x96)
            if zero_for_one
            else min(sqrt_at_tick, sqrt_price_limit_x96)
        )

        previous_price = current_price
        previous_tick = current_tick
        next_price, amount_in, amount_out, fee = compute_swap_step(
            current_price, target_price, liquidity, remaining, snapshot.fee_pips,
        )
        if exact_input:
            remaining -= amount_in + fee
            if remaining < 0:
                raise ArithmeticError("exact-input step consumed more than the remaining amount")
        else:
            remaining += amount_out
            if remaining > 0:
                raise ArithmeticError("exact-output step exceeded the requested output")
        total_in += amount_in
        total_out += amount_out
        total_fee += fee
        if any(value > MAX_UINT256 for value in (total_in, total_out, total_fee)):
            raise ArithmeticError("aggregate swap amount exceeds uint256")
        steps += 1

        if next_price == sqrt_at_tick:
            if initialized:
                try:
                    liquidity_net = snapshot.initialized_ticks[bounded_tick]
                except KeyError as exc:
                    raise MissingTickData(
                        f"missing liquidityNet for initialized tick {bounded_tick} at block {snapshot.block_number}"
                    ) from exc
                liquidity = apply_tick_crossing_liquidity(liquidity, liquidity_net, zero_for_one)
                crossed.append(bounded_tick)
            current_tick = bounded_tick - 1 if zero_for_one else bounded_tick
        elif next_price != current_price:
            current_tick = get_tick_at_sqrt_ratio(next_price)

        if next_price == previous_price and current_tick == previous_tick:
            raise ArithmeticError("swap simulation made no price or tick progress")
        current_price = next_price

        if remaining == 0:
            stop_reason = "amount_filled"
        elif current_price == sqrt_price_limit_x96:
            stop_reason = "price_limit_reached"

    complete = remaining == 0 or current_price == sqrt_price_limit_x96
    if complete and stop_reason == "step_limit":
        stop_reason = "amount_filled" if remaining == 0 else "price_limit_reached"

    return V3SwapResult(
        chain_id=snapshot.chain_id,
        pool_address=snapshot.pool_address,
        block_number=snapshot.block_number,
        block_hash=snapshot.block_hash,
        zero_for_one=zero_for_one,
        exact_input=exact_input,
        amount_specified=amount_specified,
        amount_in=total_in,
        amount_out=total_out,
        fee_amount=total_fee,
        amount_remaining=remaining,
        sqrt_price_x96=current_price,
        tick=current_tick,
        liquidity=liquidity,
        crossed_ticks=tuple(crossed),
        steps=steps,
        complete=complete,
        stop_reason=stop_reason,
        classification=(
            "rpc_snapshot_factory_registered_block_hash_rechecked"
            if snapshot.rpc_verified else "caller_supplied_snapshot_not_rpc_verified"
        ),
    )
