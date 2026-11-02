from dataclasses import replace
from datetime import datetime, timezone

import pytest

from arb_engine.flash_swap import simulate_cross_venue_flash_swap
from arb_engine.flash_swap_economics import estimate_flash_swap_economics
from arb_engine.v2 import V2PoolState


QUOTE = "0x" + "1" * 40
NATIVE = "0x" + "2" * 40
HASH = "0x" + "a" * 64


def pool(address, token0, token1, reserve0, reserve1, *, chain=1, block=100, block_hash=HASH):
    return V2PoolState(
        chain_id=chain, pair_address=address, token0=token0, token1=token1,
        reserve0_raw=reserve0, reserve1_raw=reserve1, fee_bps=30,
        block_number=block, block_hash=block_hash,
        observed_at=datetime.now(timezone.utc),
    )


def simulation():
    source = pool("0x" + "3" * 40, QUOTE, NATIVE, 1_000_000, 1_000_000)
    destination = pool("0x" + "4" * 40, QUOTE, NATIVE, 1_050_000, 1_000_000)
    return simulate_cross_venue_flash_swap(
        source, destination, borrowed_token=NATIVE, amount_borrowed_raw=10_000,
    )


def native_quote_pool(*, quote_reserve=2_000_000 * 10**6, native_reserve=1_000 * 10**18,
                      chain=1, block=100, block_hash=HASH):
    return pool(
        "0x" + "5" * 40, QUOTE, NATIVE, quote_reserve, native_reserve,
        chain=chain, block=block, block_hash=block_hash,
    )


def test_converts_weth_gas_cost_to_six_decimal_usdc_with_break_even_price():
    sim = replace(simulation(), gross_profit_raw=10_000_000)
    result = estimate_flash_swap_economics(
        sim,
        gas_used=200_000,
        gas_price_wei=20_000_000_000,
        native_token=NATIVE,
        native_quote_pool=native_quote_pool(),
    )
    assert result.gas_cost_native_raw == 4 * 10**15
    assert result.gas_cost_profit_token_raw == 8_000_000
    assert result.net_profit_raw == 2_000_000
    assert result.break_even_gas_price_wei == 25_000_000_000
    assert result.net_positive is True
    assert result.price_source_pair == "0x" + "5" * 40
    assert result.price_block_number == 100
    assert result.classification.startswith("gas_price_scenario_estimate")


def test_uses_ceiling_rounding_so_gas_cost_is_not_understated():
    sim = replace(simulation(), gross_profit_raw=100)
    tiny_price_pool = native_quote_pool(quote_reserve=3, native_reserve=2)
    result = estimate_flash_swap_economics(
        sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
        native_quote_pool=tiny_price_pool,
    )
    assert result.gas_cost_profit_token_raw == 2


def test_native_token_profit_needs_no_conversion_pool():
    sim = replace(simulation(), repayment_token=NATIVE, gross_profit_raw=10**16)
    result = estimate_flash_swap_economics(
        sim, gas_used=200_000, gas_price_wei=20_000_000_000,
        native_token=NATIVE,
    )
    assert result.gas_cost_profit_token_raw == 4 * 10**15
    assert result.net_profit_raw == 6 * 10**15
    assert result.break_even_gas_price_wei == 50_000_000_000
    assert result.price_source_pair is None


def test_reports_negative_net_scenario_without_hiding_gross_surplus():
    sim = replace(simulation(), gross_profit_raw=1_000_000)
    result = estimate_flash_swap_economics(
        sim, gas_used=200_000, gas_price_wei=20_000_000_000,
        native_token=NATIVE, native_quote_pool=native_quote_pool(),
    )
    assert result.gross_profit_raw == 1_000_000
    assert result.gas_cost_profit_token_raw == 8_000_000
    assert result.net_profit_raw == -7_000_000
    assert result.net_positive is False


def test_rejects_price_pool_from_another_chain_or_block():
    sim = simulation()
    with pytest.raises(ValueError, match="same chain"):
        estimate_flash_swap_economics(
            sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
            native_quote_pool=native_quote_pool(chain=137),
        )
    with pytest.raises(ValueError, match="same canonical block"):
        estimate_flash_swap_economics(
            sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
            native_quote_pool=native_quote_pool(block=101),
        )
    with pytest.raises(ValueError, match="same canonical block"):
        estimate_flash_swap_economics(
            sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
            native_quote_pool=native_quote_pool(block_hash="0x" + "b" * 64),
        )


def test_rejects_wrong_price_pair_and_missing_quote_pool():
    sim = simulation()
    with pytest.raises(ValueError, match="required"):
        estimate_flash_swap_economics(
            sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
        )
    wrong = pool(
        "0x" + "6" * 40, QUOTE, "0x" + "7" * 40, 2_000_000, 1_000_000,
    )
    with pytest.raises(ValueError, match="exactly the native"):
        estimate_flash_swap_economics(
            sim, gas_used=1, gas_price_wei=1, native_token=NATIVE,
            native_quote_pool=wrong,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"gas_used": True, "gas_price_wei": 1, "native_token": NATIVE}, "gas_used"),
        ({"gas_used": 1, "gas_price_wei": -1, "native_token": NATIVE}, "gas_price_wei"),
        ({"gas_used": 1, "gas_price_wei": 1, "native_token": "not-an-address"}, "20-byte"),
        ({"gas_used": 1, "gas_price_wei": 1, "native_token": "0x" + "0" * 40}, "zero address"),
    ],
)
def test_rejects_invalid_gas_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        estimate_flash_swap_economics(simulation(), **kwargs)
