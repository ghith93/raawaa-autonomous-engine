from dataclasses import replace
from datetime import datetime, timezone

import pytest

from arb_engine.v2 import V2PoolState
from arb_engine.flash_swap import simulate_cross_venue_flash_swap


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
HASH = "0x" + "a" * 64


def pool(address, token0, token1, reserve0, reserve1, *, fee=30, chain=1, block=100):
    return V2PoolState(
        chain_id=chain, pair_address=address, token0=token0, token1=token1,
        reserve0_raw=reserve0, reserve1_raw=reserve1, fee_bps=fee,
        block_number=block, block_hash=HASH, observed_at=datetime.now(timezone.utc),
    )


def route(*, source_fee=30, destination_fee=30):
    source = pool("0x" + "3" * 40, QUOTE, BASE, 1_000_000, 1_000_000, fee=source_fee)
    destination = pool(
        "0x" + "4" * 40, QUOTE, BASE, 1_050_000, 1_000_000,
        fee=destination_fee,
    )
    return source, destination


def test_flash_swap_repayment_matches_v2_get_amount_in_rounding():
    source, destination = route()
    result = simulate_cross_venue_flash_swap(
        source, destination, borrowed_token=BASE, amount_borrowed_raw=10_000,
    )
    expected = (1_000_000 * 10_000 * 10_000) // ((1_000_000 - 10_000) * 9_970) + 1
    assert result.repayment_token == QUOTE
    assert result.repayment_amount_raw == expected
    assert result.borrow.amount_borrowed_raw == 10_000
    assert result.borrow.amount_in_raw == expected


def test_profitable_and_unprofitable_routes_are_reported_without_execution():
    source, destination = route()
    profitable = simulate_cross_venue_flash_swap(
        source, destination, borrowed_token=BASE, amount_borrowed_raw=10_000,
    )
    assert profitable.gross_profit_raw == (
        profitable.sale_proceeds_raw - profitable.repayment_amount_raw
    )
    assert profitable.gross_profit_raw > 0
    assert profitable.classification.startswith("flash_swap_reserve_math_only")

    poor_destination = replace(destination, reserve0_raw=900_000)
    unprofitable = simulate_cross_venue_flash_swap(
        source, poor_destination, borrowed_token=BASE, amount_borrowed_raw=10_000,
    )
    assert unprofitable.gross_profit_raw < 0


def test_reverse_borrow_direction_is_supported():
    source, destination = route()
    result = simulate_cross_venue_flash_swap(
        source, destination, borrowed_token=QUOTE, amount_borrowed_raw=10_000,
    )
    assert result.repayment_token == BASE
    assert result.sale.token_out == BASE


def test_rejects_same_pool_cross_chain_and_different_block():
    source, destination = route()
    with pytest.raises(ValueError, match="different pools"):
        simulate_cross_venue_flash_swap(
            source, source, borrowed_token=BASE, amount_borrowed_raw=100,
        )
    with pytest.raises(ValueError, match="Cross-chain"):
        simulate_cross_venue_flash_swap(
            source, replace(destination, chain_id=137),
            borrowed_token=BASE, amount_borrowed_raw=100,
        )
    with pytest.raises(ValueError, match="same canonical block"):
        simulate_cross_venue_flash_swap(
            source, replace(destination, block_number=101),
            borrowed_token=BASE, amount_borrowed_raw=100,
        )


def test_rejects_bad_amount_or_pair_assets():
    source, destination = route()
    with pytest.raises(ValueError, match="positive uint256"):
        simulate_cross_venue_flash_swap(
            source, destination, borrowed_token=BASE, amount_borrowed_raw=True,
        )
    wrong_pair = replace(destination, token1="0x" + "5" * 40)
    with pytest.raises(ValueError, match="Destination pair"):
        simulate_cross_venue_flash_swap(
            source, wrong_pair, borrowed_token=BASE, amount_borrowed_raw=100,
        )
    with pytest.raises(ValueError, match="smaller than"):
        simulate_cross_venue_flash_swap(
            source, destination, borrowed_token=BASE, amount_borrowed_raw=1_000_000,
        )


def test_repayment_uses_source_fee_and_destination_quote_uses_destination_fee():
    source, destination = route(source_fee=100, destination_fee=30)
    result = simulate_cross_venue_flash_swap(
        source, destination, borrowed_token=BASE, amount_borrowed_raw=10_000,
    )
    assert result.repayment_amount_raw == (
        (1_000_000 * 10_000 * 10_000) // (990_000 * 9_900) + 1
    )
    assert result.sale.fee_bps == 30
