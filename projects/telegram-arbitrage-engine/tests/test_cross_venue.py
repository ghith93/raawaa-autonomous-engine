from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_engine.market_sources.binance import BinanceOrderBookSnapshot
from arb_engine.market_sources.bybit import OrderBookSnapshot as BybitOrderBookSnapshot
from arb_engine.market_sources.coinbase import CoinbaseOrderBookSnapshot
from arb_engine.market_sources.kraken import KrakenOrderBookSnapshot
from arb_engine.market_sources.cross_venue import (
    InstrumentMappingError,
    InstrumentRegistry,
    InstrumentSpec,
    compare_cex_spot_orderbooks,
    compare_cex_spot_orderbook_universe,
)
from arb_engine.opportunity_costs import OpportunityCosts


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"


def bybit_book(*, asks=None, bids=None, fetched_at=NOW):
    return BybitOrderBookSnapshot(
        source="bybit",
        category="spot",
        symbol="BTCUSDT",
        bids=tuple(bids or ((Decimal("99.9"), Decimal("1")), (Decimal("99.8"), Decimal("1")))),
        asks=tuple(asks or ((Decimal("100.1"), Decimal("0.6")), (Decimal("100.2"), Decimal("1")))),
        exchange_timestamp_ms=1793628000000,
        matching_engine_timestamp_ms=1793627999990,
        fetched_at=fetched_at,
        update_id=12345,
        cross_sequence=98765,
    )


def binance_book(*, asks=None, bids=None, fetched_at=NOW):
    return BinanceOrderBookSnapshot(
        source="binance",
        symbol="BTCUSDT",
        bids=tuple(bids or ((Decimal("100.5"), Decimal("0.4")), (Decimal("100.4"), Decimal("1")))),
        asks=tuple(asks or ((Decimal("100.6"), Decimal("0.5")), (Decimal("100.7"), Decimal("1")))),
        last_update_id=98765,
        fetched_at=fetched_at,
    )


def registry(*, bybit_base=BTC, binance_base=BTC, bybit_quote=USDT, binance_quote=USDT):
    return InstrumentRegistry([
        InstrumentSpec("bybit", "BTCUSDT", bybit_base, bybit_quote, "0.001"),
        InstrumentSpec("binance", "BTCUSDT", binance_base, binance_quote, "0.001"),
    ])


def known_costs(**overrides):
    fields = {
        "buy_fee_bps": "10",
        "sell_fee_bps": "10",
        "buy_slippage_bps": "0",
        "sell_slippage_bps": "0",
        "latency_cost_bps": "1",
    }
    fields.update(overrides)
    return OpportunityCosts(**fields)


def test_depth_sweep_compares_both_directions_and_uses_canonical_identity():
    books_a = bybit_book()
    books_b = binance_book()
    results = compare_cex_spot_orderbooks(
        book_a=books_a,
        book_b=books_b,
        registry=registry(),
        base_quantity="0.5",
        costs_by_direction={
            ("bybit", "binance"): known_costs(),
            ("binance", "bybit"): known_costs(),
        },
        now=NOW,
    )

    buy_bybit_sell_binance, buy_binance_sell_bybit = results
    assert buy_bybit_sell_binance.status == "net_positive_estimate"
    assert buy_bybit_sell_binance.buy_vwap == Decimal("100.1")
    assert buy_bybit_sell_binance.sell_vwap == Decimal("100.48")
    assert buy_bybit_sell_binance.buy_quote_spent == Decimal("50.05")
    assert buy_bybit_sell_binance.sell_quote_received == Decimal("50.24")
    assert buy_bybit_sell_binance.gross_profit_quote == Decimal("0.19")
    assert buy_bybit_sell_binance.evaluation.net_profit_quote == Decimal("0.084895")
    assert buy_bybit_sell_binance.evaluation.execution_verified is False

    assert buy_binance_sell_bybit.status == "net_nonpositive"
    assert buy_binance_sell_bybit.gross_profit_quote < 0


def test_missing_fee_profile_never_defaults_to_zero_cost():
    result, _ = compare_cex_spot_orderbooks(
        book_a=bybit_book(),
        book_b=binance_book(),
        registry=registry(),
        base_quantity="0.5",
        now=NOW,
    )
    assert result.status == "incomplete_costs"
    assert result.gross_profit_quote == Decimal("0.19")
    assert result.evaluation.net_profit_quote is None
    assert set(result.evaluation.missing_required_costs) == {
        "buy_fee_bps", "sell_fee_bps", "buy_slippage_bps",
        "sell_slippage_bps", "latency_cost_bps",
    }


def test_mismatched_canonical_asset_or_quote_currency_blocks_symbol_match():
    asset_mismatch = compare_cex_spot_orderbooks(
        book_a=bybit_book(),
        book_b=binance_book(),
        registry=registry(binance_base="asset:canonical:wrapped-BTC"),
        base_quantity="0.5",
        now=NOW,
    )
    assert all(x.status == "asset_or_quote_identity_mismatch" for x in asset_mismatch)
    assert all(x.evaluation is None and x.gross_profit_quote is None for x in asset_mismatch)

    quote_mismatch = compare_cex_spot_orderbooks(
        book_a=bybit_book(),
        book_b=binance_book(),
        registry=registry(binance_quote="currency:canonical:USDC"),
        base_quantity="0.5",
        now=NOW,
    )
    assert all(x.status == "asset_or_quote_identity_mismatch" for x in quote_mismatch)


def test_missing_explicit_instrument_mapping_is_not_inferred_from_symbol():
    partial = InstrumentRegistry([
        InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.001"),
    ])
    with pytest.raises(InstrumentMappingError, match="no explicit canonical mapping"):
        compare_cex_spot_orderbooks(
            book_a=bybit_book(),
            book_b=binance_book(),
            registry=partial,
            base_quantity="0.5",
        )


def test_insufficient_depth_on_either_venue_blocks_profit_label():
    shallow = bybit_book(asks=((Decimal("100.1"), Decimal("0.2")),))
    result, _ = compare_cex_spot_orderbooks(
        book_a=shallow,
        book_b=binance_book(),
        registry=registry(),
        base_quantity="0.5",
        costs_by_direction={("bybit", "binance"): known_costs()},
        now=NOW,
    )
    assert result.status == "insufficient_depth"
    assert result.buy_filled_base_quantity == Decimal("0.2")
    assert result.buy_fill_fraction == Decimal("0.4")
    assert result.gross_profit_quote is None
    assert result.evaluation is None


def test_stale_observation_blocks_net_profit_estimate():
    result, _ = compare_cex_spot_orderbooks(
        book_a=bybit_book(fetched_at=NOW - timedelta(seconds=10)),
        book_b=binance_book(),
        registry=registry(),
        base_quantity="0.5",
        costs_by_direction={("bybit", "binance"): known_costs()},
        now=NOW,
    )
    assert result.status == "stale_data"
    assert result.gross_profit_quote == Decimal("0.19")
    assert result.evaluation.net_profit_quote is None


def test_quantity_must_match_both_venues_lot_steps():
    wrong_step = InstrumentRegistry([
        InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.01"),
        InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"),
    ])
    with pytest.raises(InstrumentMappingError, match="venue A quantity step"):
        compare_cex_spot_orderbooks(
            book_a=bybit_book(),
            book_b=binance_book(),
            registry=wrong_step,
            base_quantity="0.005",
        )


def test_duplicate_or_non_spot_instrument_mappings_are_rejected():
    with pytest.raises(InstrumentMappingError, match="duplicate instrument mapping"):
        InstrumentRegistry([
            InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.001"),
            InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.001"),
        ])
    with pytest.raises(InstrumentMappingError, match="only cex_spot"):
        InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.001", market_family="cex_perp")


def test_cross_venue_blocks_quantities_below_either_venues_minimum_order_quantity():
    minimum_registry = InstrumentRegistry([
        InstrumentSpec(
            "bybit", "BTCUSDT", BTC, USDT, "0.001",
            min_base_quantity="0.6",
        ),
        InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"),
    ])
    result, reverse = compare_cex_spot_orderbooks(
        book_a=bybit_book(),
        book_b=binance_book(),
        registry=minimum_registry,
        base_quantity="0.5",
        costs_by_direction={
            ("bybit", "binance"): known_costs(),
            ("binance", "bybit"): known_costs(),
        },
        now=NOW,
    )
    assert result.status == "below_minimum_order_quantity"
    assert reverse.status == "below_minimum_order_quantity"
    assert result.gross_profit_quote == Decimal("0.19")
    assert result.evaluation is None


def test_cross_venue_blocks_quote_notional_below_either_venues_minimum():
    minimum_registry = InstrumentRegistry([
        InstrumentSpec(
            "bybit", "BTCUSDT", BTC, USDT, "0.001",
            min_quote_notional="60",
        ),
        InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"),
    ])
    result, _ = compare_cex_spot_orderbooks(
        book_a=bybit_book(),
        book_b=binance_book(),
        registry=minimum_registry,
        base_quantity="0.5",
        costs_by_direction={
            ("bybit", "binance"): known_costs(),
            ("binance", "bybit"): known_costs(),
        },
        now=NOW,
    )
    assert result.status == "below_minimum_order_notional"
    assert result.gross_profit_quote == Decimal("0.19")
    assert result.evaluation is None


def test_instrument_spec_rejects_invalid_minimum_order_constraints():
    with pytest.raises(InstrumentMappingError, match="min_base_quantity"):
        InstrumentSpec(
            "bybit", "BTCUSDT", BTC, USDT, "0.001",
            min_base_quantity="0",
        )
    with pytest.raises(InstrumentMappingError, match="min_quote_notional"):
        InstrumentSpec(
            "bybit", "BTCUSDT", BTC, USDT, "0.001",
            min_quote_notional="-1",
        )



def coinbase_book(*, asks=None, bids=None, fetched_at=NOW):
    return CoinbaseOrderBookSnapshot(
        source="coinbase",
        category="spot",
        symbol="BTC-USD",
        bids=tuple(bids or ((Decimal("100.2"), Decimal("1")),)),
        asks=tuple(asks or ((Decimal("100.3"), Decimal("1")),)),
        sequence=1001,
        fetched_at=fetched_at,
        exchange_timestamp_ms=1793628000000,
        bid_order_counts=(3,),
        ask_order_counts=(2,),
    )


def kraken_book(*, asks=None, bids=None, fetched_at=NOW):
    return KrakenOrderBookSnapshot(
        source="kraken",
        category="spot",
        symbol="XBT/USDT",
        bids=tuple(bids or ((Decimal("100.3"), Decimal("1")),)),
        asks=tuple(asks or ((Decimal("100.4"), Decimal("1")),)),
        fetched_at=fetched_at,
        exchange_timestamp_ms=1793628000000,
        bid_level_timestamps_ms=(1793628000000,),
        ask_level_timestamps_ms=(1793628000000,),
    )


def four_venue_registry(*, coinbase_quote="currency:canonical:USD"):
    return InstrumentRegistry([
        InstrumentSpec("bybit", "BTCUSDT", BTC, USDT, "0.001"),
        InstrumentSpec("binance", "BTCUSDT", BTC, USDT, "0.001"),
        InstrumentSpec("coinbase", "BTC-USD", BTC, coinbase_quote, "0.00000001"),
        InstrumentSpec("kraken", "XBT/USDT", BTC, USDT, "0.00000001"),
    ])


def all_pair_costs(venue_ids):
    return {
        (buy, sell): known_costs()
        for buy in venue_ids
        for sell in venue_ids
        if buy != sell
    }


def test_multi_venue_comparison_never_equates_coinbase_usd_with_usdt():
    books = [bybit_book(), binance_book(), coinbase_book(), kraken_book()]
    venue_ids = ["bybit", "binance", "coinbase", "kraken"]
    result = compare_cex_spot_orderbook_universe(
        books=books,
        registry=four_venue_registry(),
        base_quantity="0.5",
        costs_by_direction=all_pair_costs(venue_ids),
        now=NOW,
    )
    assert result.status == "partial"
    assert result.venue_ids == tuple(venue_ids)
    assert result.compared_pair_count == 3
    assert len(result.opportunities) == 6
    assert result.skipped_pairs == (
        ("bybit", "coinbase", "canonical_base_or_quote_identity_mismatch"),
        ("binance", "coinbase", "canonical_base_or_quote_identity_mismatch"),
        ("coinbase", "kraken", "canonical_base_or_quote_identity_mismatch"),
    )
    assert result.source_failures == ()
    assert {
        (item.buy_venue_id, item.sell_venue_id)
        for item in result.opportunities
    } == {
        (buy, sell)
        for buy in ("bybit", "binance", "kraken")
        for sell in ("bybit", "binance", "kraken")
        if buy != sell
    }
    assert all(item.evaluation is not None for item in result.opportunities)


def test_multi_venue_comparison_preserves_partial_sources_and_skips_identity_collisions():
    books = [bybit_book(), binance_book(), coinbase_book()]
    result = compare_cex_spot_orderbook_universe(
        books=books,
        registry=four_venue_registry(coinbase_quote="currency:canonical:USD"),
        base_quantity="0.5",
        source_failures={"kraken": "public_api_timeout"},
        now=NOW,
    )
    assert result.status == "partial"
    assert result.venue_ids == ("bybit", "binance", "coinbase")
    assert result.compared_pair_count == 1
    assert len(result.opportunities) == 2
    assert result.skipped_pairs == (
        ("bybit", "coinbase", "canonical_base_or_quote_identity_mismatch"),
        ("binance", "coinbase", "canonical_base_or_quote_identity_mismatch"),
    )
    assert result.source_failures == (("kraken", "public_api_timeout"),)


def test_multi_venue_comparison_reports_insufficient_sources_and_enforces_bounds():
    result = compare_cex_spot_orderbook_universe(
        books=[bybit_book()],
        registry=four_venue_registry(),
        base_quantity="0.5",
        source_failures={"kraken": "rate_limited"},
        now=NOW,
    )
    assert result.status == "insufficient_sources"
    assert result.opportunities == ()

    with pytest.raises(InstrumentMappingError, match="duplicate order-book source"):
        compare_cex_spot_orderbook_universe(
            books=[bybit_book(), bybit_book()],
            registry=four_venue_registry(),
            base_quantity="0.5",
        )
    with pytest.raises(InstrumentMappingError, match="exceeds the 2-venue"):
        compare_cex_spot_orderbook_universe(
            books=[bybit_book(), binance_book(), coinbase_book()],
            registry=four_venue_registry(),
            base_quantity="0.5",
            max_venues=2,
        )
    with pytest.raises(InstrumentMappingError, match="both available and failed"):
        compare_cex_spot_orderbook_universe(
            books=[bybit_book(), binance_book()],
            registry=four_venue_registry(),
            base_quantity="0.5",
            source_failures={"bybit": "timeout"},
        )
