from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext


@dataclass(frozen=True, slots=True)
class V2PoolState:
    """Pinned constant-product pool state supplied by a trusted read-only RPC adapter."""

    chain_id: int
    pair_address: str
    token0: str
    token1: str
    reserve0_raw: int
    reserve1_raw: int
    fee_bps: int
    block_number: int
    block_hash: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class V2SwapQuote:
    pool_address: str
    token_in: str
    token_out: str
    amount_in_raw: int
    amount_out_raw: int
    fee_bps: int
    block_number: int
    block_hash: str


@dataclass(frozen=True, slots=True)
class V2RoundTrip:
    buy: V2SwapQuote
    sell: V2SwapQuote
    capital_quote_raw: int
    final_quote_raw: int
    gross_profit_quote_raw: int
    gross_return_bps: Decimal
    classification: str = "reserve_math_only_excludes_gas_tax_reverts_and_execution"


def _validate_pool(pool: V2PoolState) -> None:
    if not isinstance(pool, V2PoolState):
        raise ValueError("Pool must be a V2PoolState")
    if not isinstance(pool.chain_id, int) or isinstance(pool.chain_id, bool) or pool.chain_id <= 0:
        raise ValueError("Invalid EVM chain ID")
    if not all(isinstance(value, str) and value.strip() for value in (
        pool.pair_address, pool.token0, pool.token1, pool.block_hash,
    )):
        raise ValueError("Pool addresses and block hash are required")
    if pool.token0.casefold() == pool.token1.casefold():
        raise ValueError("Pool tokens must be different")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (pool.reserve0_raw, pool.reserve1_raw)):
        raise ValueError("Pool reserves must be positive integer raw units")
    if not isinstance(pool.fee_bps, int) or isinstance(pool.fee_bps, bool) or not 0 <= pool.fee_bps < 10000:
        raise ValueError("Pool fee must be integer basis points in [0, 10000)")
    if not isinstance(pool.block_number, int) or isinstance(pool.block_number, bool) or pool.block_number < 0:
        raise ValueError("Invalid block number")
    if not isinstance(pool.observed_at, datetime) or pool.observed_at.tzinfo is None or pool.observed_at.utcoffset() is None:
        raise ValueError("Pool observation timestamp must be timezone-aware")


def quote_exact_input(pool: V2PoolState, token_in: str, amount_in_raw: int) -> V2SwapQuote:
    """Calculate a standard V2 constant-product exact-input quote using integer math."""
    _validate_pool(pool)
    if not isinstance(token_in, str) or not token_in.strip():
        raise ValueError("token_in is required")
    if not isinstance(amount_in_raw, int) or isinstance(amount_in_raw, bool) or amount_in_raw <= 0:
        raise ValueError("amount_in_raw must be a positive integer")

    if token_in.casefold() == pool.token0.casefold():
        reserve_in, reserve_out, token_out = pool.reserve0_raw, pool.reserve1_raw, pool.token1
    elif token_in.casefold() == pool.token1.casefold():
        reserve_in, reserve_out, token_out = pool.reserve1_raw, pool.reserve0_raw, pool.token0
    else:
        raise ValueError("Input token is not part of this pool")

    amount_with_fee = amount_in_raw * (10000 - pool.fee_bps)
    denominator = reserve_in * 10000 + amount_with_fee
    amount_out = (amount_with_fee * reserve_out) // denominator
    if amount_out <= 0 or amount_out >= reserve_out:
        raise ValueError("Pool quote rounds to zero or exceeds available reserves")
    return V2SwapQuote(
        pool_address=pool.pair_address,
        token_in=token_in,
        token_out=token_out,
        amount_in_raw=amount_in_raw,
        amount_out_raw=amount_out,
        fee_bps=pool.fee_bps,
        block_number=pool.block_number,
        block_hash=pool.block_hash,
    )


def simulate_round_trip(
    buy_pool: V2PoolState,
    sell_pool: V2PoolState,
    *,
    quote_token: str,
    base_token: str,
    capital_quote_raw: int,
) -> V2RoundTrip:
    """Simulate quote-token -> base-token -> quote-token using same-block pool reserves."""
    _validate_pool(buy_pool)
    _validate_pool(sell_pool)
    if buy_pool.chain_id != sell_pool.chain_id:
        raise ValueError("Cross-chain pools cannot form an atomic round trip")
    if buy_pool.block_number != sell_pool.block_number or buy_pool.block_hash.casefold() != sell_pool.block_hash.casefold():
        raise ValueError("Both pools must be read at the same canonical block")
    if not isinstance(quote_token, str) or not isinstance(base_token, str) or not quote_token.strip() or not base_token.strip():
        raise ValueError("Base and quote token addresses are required")
    if quote_token.casefold() == base_token.casefold():
        raise ValueError("Base and quote token must be different")
    if not isinstance(capital_quote_raw, int) or isinstance(capital_quote_raw, bool) or capital_quote_raw <= 0:
        raise ValueError("Capital must be positive integer raw quote-token units")

    for pool in (buy_pool, sell_pool):
        pool_tokens = {pool.token0.casefold(), pool.token1.casefold()}
        if pool_tokens != {quote_token.casefold(), base_token.casefold()}:
            raise ValueError("Both pools must contain the exact same base/quote token addresses")

    buy = quote_exact_input(buy_pool, quote_token, capital_quote_raw)
    sell = quote_exact_input(sell_pool, base_token, buy.amount_out_raw)
    if sell.token_out.casefold() != quote_token.casefold():
        raise ValueError("Sell pool did not return the requested quote token")

    gross_profit = sell.amount_out_raw - capital_quote_raw
    with localcontext() as ctx:
        ctx.prec = 50
        gross_bps = (Decimal(gross_profit) / Decimal(capital_quote_raw)) * Decimal(10000)
    return V2RoundTrip(
        buy=buy,
        sell=sell,
        capital_quote_raw=capital_quote_raw,
        final_quote_raw=sell.amount_out_raw,
        gross_profit_quote_raw=gross_profit,
        gross_return_bps=gross_bps,
    )
