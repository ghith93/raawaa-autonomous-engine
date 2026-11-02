from __future__ import annotations

from dataclasses import dataclass

from .v2 import V2PoolState, _validate_pool, quote_exact_input


@dataclass(frozen=True, slots=True)
class V2FlashBorrow:
    """A modelled pair output and the opposite-token repayment required by its invariant."""

    pool_address: str
    borrowed_token: str
    repayment_token: str
    amount_borrowed_raw: int
    repayment_amount_raw: int
    fee_bps: int
    block_number: int
    block_hash: str


@dataclass(frozen=True, slots=True)
class V2FlashSwapSimulation:
    """Reserve-math estimate for borrowing one asset and repaying the source pair in the other."""

    borrow: V2FlashBorrow
    sale: object
    repayment_token: str
    repayment_amount_raw: int
    sale_proceeds_raw: int
    gross_profit_raw: int
    classification: str = (
        "flash_swap_reserve_math_only_excludes_gas_tax_reverts_token_behavior_and_execution"
    )


def _required_cross_token_repayment(
    pool: V2PoolState, borrowed_token: str, amount_borrowed_raw: int,
) -> tuple[str, int]:
    """Return the opposite token and minimum input satisfying the V2 pair invariant.

    The +1 rounding is intentional and matches Uniswap V2's getAmountIn convention.
    Fee basis points are explicit pool configuration, never inferred here.
    """
    if borrowed_token.casefold() == pool.token0.casefold():
        reserve_borrowed, reserve_repayment = pool.reserve0_raw, pool.reserve1_raw
        repayment_token = pool.token1
    elif borrowed_token.casefold() == pool.token1.casefold():
        reserve_borrowed, reserve_repayment = pool.reserve1_raw, pool.reserve0_raw
        repayment_token = pool.token0
    else:
        raise ValueError("Borrowed token is not part of the source pair")
    if amount_borrowed_raw >= reserve_borrowed:
        raise ValueError("Borrow amount must be smaller than the source pair reserve")
    fee_factor = 10000 - pool.fee_bps
    if fee_factor <= 0:
        raise ValueError("Source pair fee leaves no repayable input")
    numerator = reserve_repayment * amount_borrowed_raw * 10000
    denominator = (reserve_borrowed - amount_borrowed_raw) * fee_factor
    repayment = numerator // denominator + 1
    if repayment <= 0 or reserve_repayment + repayment >= 2**112:
        raise ValueError("Repayment would exceed the source pair uint112 reserve bound")
    return repayment_token, repayment


def simulate_cross_venue_flash_swap(
    source_pool: V2PoolState,
    destination_pool: V2PoolState,
    *,
    borrowed_token: str,
    amount_borrowed_raw: int,
) -> V2FlashSwapSimulation:
    """Model a V2 flash swap: borrow token, sell it on another pool, repay source in other token.

    This is deterministic integer reserve math only. It does not construct calldata,
    execute a callback, mutate a fork, estimate route gas, or submit a transaction.
    """
    _validate_pool(source_pool)
    _validate_pool(destination_pool)
    if source_pool.chain_id != destination_pool.chain_id:
        raise ValueError("Cross-chain pools cannot form an atomic flash-swap route")
    if (
        source_pool.block_number != destination_pool.block_number
        or source_pool.block_hash.casefold() != destination_pool.block_hash.casefold()
    ):
        raise ValueError("Both pools must be read at the same canonical block")
    if source_pool.pair_address.casefold() == destination_pool.pair_address.casefold():
        raise ValueError("Flash-swap route requires two different pools")
    if not isinstance(borrowed_token, str) or not borrowed_token.strip():
        raise ValueError("Borrowed token address is required")
    if (
        not isinstance(amount_borrowed_raw, int)
        or isinstance(amount_borrowed_raw, bool)
        or not 0 < amount_borrowed_raw < 2**256
    ):
        raise ValueError("Borrow amount must be a positive uint256 raw-unit value")

    source_tokens = {source_pool.token0.casefold(), source_pool.token1.casefold()}
    destination_tokens = {
        destination_pool.token0.casefold(), destination_pool.token1.casefold()
    }
    if borrowed_token.casefold() not in source_tokens:
        raise ValueError("Borrowed token is not part of the source pair")
    repayment_token = (
        source_pool.token1
        if borrowed_token.casefold() == source_pool.token0.casefold()
        else source_pool.token0
    )
    if destination_tokens != {borrowed_token.casefold(), repayment_token.casefold()}:
        raise ValueError("Destination pair must contain the borrowed and repayment tokens")

    repayment_token, repayment_amount = _required_cross_token_repayment(
        source_pool, borrowed_token, amount_borrowed_raw,
    )
    sale = quote_exact_input(destination_pool, borrowed_token, amount_borrowed_raw)
    if sale.token_out.casefold() != repayment_token.casefold():
        raise ValueError("Destination swap did not return the source repayment token")
    borrow = V2FlashBorrow(
        pool_address=source_pool.pair_address,
        borrowed_token=borrowed_token,
        repayment_token=repayment_token,
        amount_borrowed_raw=amount_borrowed_raw,
        repayment_amount_raw=repayment_amount,
        fee_bps=source_pool.fee_bps,
        block_number=source_pool.block_number,
        block_hash=source_pool.block_hash,
    )
    return V2FlashSwapSimulation(
        borrow=borrow,
        sale=sale,
        repayment_token=repayment_token,
        repayment_amount_raw=repayment_amount,
        sale_proceeds_raw=sale.amount_out_raw,
        gross_profit_raw=sale.amount_out_raw - repayment_amount,
    )
