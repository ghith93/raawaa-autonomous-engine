from __future__ import annotations

from dataclasses import dataclass
import re

from .flash_swap import V2FlashSwapSimulation
from .v2 import V2PoolState, _validate_pool


@dataclass(frozen=True, slots=True)
class V2FlashSwapEconomics:
    """Explicit gas-price scenario applied to a gross reserve-math result."""

    gross_profit_raw: int
    gas_used: int
    gas_price_wei: int
    gas_cost_native_raw: int
    gas_cost_profit_token_raw: int
    net_profit_raw: int
    break_even_gas_price_wei: int
    profit_token: str
    native_token: str
    native_token_decimals: int
    profit_token_decimals: int
    price_source_pair: str | None
    price_block_number: int
    price_block_hash: str
    classification: str = (
        "gas_price_scenario_estimate_excludes_mev_inclusion_risk_transfer_tax_and_production_overhead"
    )

    @property
    def net_positive(self) -> bool:
        return self.net_profit_raw > 0


def _validate_decimals(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
        raise ValueError(f"{name} must be an integer in [0, 255]")


def estimate_flash_swap_economics(
    simulation: V2FlashSwapSimulation,
    *,
    gas_used: int,
    gas_price_wei: int,
    native_token: str,
    native_token_decimals: int,
    profit_token_decimals: int,
    native_quote_pool: V2PoolState | None = None,
) -> V2FlashSwapEconomics:
    """Estimate gas in repayment-token raw units using explicit same-block reserve pricing.

    Decimal counts are required audit metadata. The conversion itself uses the ratio of
    raw reserves directly: the decimal scales cancel when converting raw native units to
    raw repayment-token units. For example, this correctly maps WETH wei to USDC micro-units
    for an 18/6 pool. gas_used must include the caller's chosen transaction/calldata overhead.
    gas_price_wei
    must be an explicit scenario (for example base fee plus a chosen tip). If repayment is
    not in the wrapped native token, native_quote_pool must contain exactly those two
    tokens and be pinned to the flash-swap block.

    Conversion uses the pool's spot reserve ratio and ceiling rounding. This is a
    scenario estimate, not an executable quote or a profitability guarantee.
    """
    if not isinstance(simulation, V2FlashSwapSimulation):
        raise ValueError("simulation must be a V2FlashSwapSimulation")
    if (
        not isinstance(gas_used, int) or isinstance(gas_used, bool) or gas_used <= 0
    ):
        raise ValueError("gas_used must be a positive integer including explicit overhead")
    if (
        not isinstance(gas_price_wei, int)
        or isinstance(gas_price_wei, bool)
        or gas_price_wei < 0
    ):
        raise ValueError("gas_price_wei must be a non-negative integer scenario")
    if not isinstance(native_token, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", native_token):
        raise ValueError("native_token must be a 20-byte EVM address")
    if int(native_token[2:], 16) == 0:
        raise ValueError("native_token cannot be the zero address")
    _validate_decimals(native_token_decimals, "native_token_decimals")
    _validate_decimals(profit_token_decimals, "profit_token_decimals")

    gross = simulation.gross_profit_raw
    if not isinstance(gross, int) or isinstance(gross, bool):
        raise ValueError("simulation gross profit must be integer raw token units")
    native_gas_cost = gas_used * gas_price_wei
    profit_token = simulation.repayment_token

    if profit_token.casefold() == native_token.casefold():
        gas_cost = native_gas_cost
        break_even = max(0, gross // gas_used)
        price_pair = None
    else:
        if native_quote_pool is None:
            raise ValueError("A same-block native/repayment-token pool is required for gas conversion")
        _validate_pool(native_quote_pool)
        if native_quote_pool.chain_id != simulation.borrow.chain_id:
            raise ValueError("Gas price pool and flash-swap must be on the same chain")
        if (
            native_quote_pool.block_number != simulation.borrow.block_number
            or native_quote_pool.block_hash.casefold() != simulation.borrow.block_hash.casefold()
        ):
            raise ValueError("Gas price pool must use the same canonical block as the flash-swap")
        pool_tokens = {
            native_quote_pool.token0.casefold(),
            native_quote_pool.token1.casefold(),
        }
        if pool_tokens != {native_token.casefold(), profit_token.casefold()}:
            raise ValueError("Gas price pool must contain exactly the native and repayment tokens")

        if native_quote_pool.token0.casefold() == native_token.casefold():
            native_reserve = native_quote_pool.reserve0_raw
            profit_reserve = native_quote_pool.reserve1_raw
        else:
            native_reserve = native_quote_pool.reserve1_raw
            profit_reserve = native_quote_pool.reserve0_raw

        # Both sides are raw units: raw repayment = raw native * reserve_quote / reserve_native.
        # Token decimal scales cancel exactly; retaining them above is audit metadata.
        conversion_numerator = native_gas_cost * profit_reserve
        gas_cost = (conversion_numerator + native_reserve - 1) // native_reserve

        # Largest integer gas price whose ceiling-rounded raw-token cost does not exceed gross.
        break_even_denominator = gas_used * profit_reserve
        break_even = (
            max(0, (gross * native_reserve) // break_even_denominator)
            if gross > 0 else 0
        )
        price_pair = native_quote_pool.pair_address

    return V2FlashSwapEconomics(
        gross_profit_raw=gross,
        gas_used=gas_used,
        gas_price_wei=gas_price_wei,
        gas_cost_native_raw=native_gas_cost,
        gas_cost_profit_token_raw=gas_cost,
        net_profit_raw=gross - gas_cost,
        break_even_gas_price_wei=break_even,
        profit_token=profit_token,
        native_token=native_token,
        native_token_decimals=native_token_decimals,
        profit_token_decimals=profit_token_decimals,
        price_source_pair=price_pair,
        price_block_number=simulation.borrow.block_number,
        price_block_hash=simulation.borrow.block_hash,
    )
