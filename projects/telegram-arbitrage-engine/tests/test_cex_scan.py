from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_engine.cex_scan import CexScanError, scan_cex_spot_depth
from arb_engine.market_sources.binance import BinanceOrderBookSnapshot
from arb_engine.market_sources.bybit import OrderBookSnapshot as BybitOrderBookSnapshot
from arb_engine.market_sources.instruments import InstrumentMetadata


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def metadata(venue, instrument_id, *, base="BTC", quote="USDT", status="TRADING"):
    return InstrumentMetadata(
        venue_id=venue,
        instrument_id=instrument_id,
        market_family="cex_spot",
        base_asset_code=base,
        quote_asset_code=quote,
        base_step_size="0.001",
        price_tick_size="0.01",
        status=status,
        fetched_at=NOW,
        min_base_quantity="0.001",
        min_quote_notional="5",
    )


def binance_book(symbol="BTCUSDT"):
    return BinanceOrderBookSnapshot(
        source="binance",
        symbol=symbol,
        bids=((Decimal("100.4"), Decimal("1")),),
        asks=((Decimal("100.5"), Decimal("1")),),
        last_update_id=123,
        fetched_at=NOW,
    )


def bybit_book(symbol="BTCUSDT"):
    return BybitOrderBookSnapshot(
        source="bybit",
        category="spot",
        symbol=symbol,
        bids=((Decimal("100.1"), Decimal("1")),),
        asks=((Decimal("100.2"), Decimal("1")),),
        exchange_timestamp_ms=1793707200000,
        matching_engine_timestamp_ms=1793707199990,
        fetched_at=NOW,
        update_id=456,
        cross_sequence=789,
    )


class FakeBinance:
    def fetch_instrument(self, *, symbol):
        return metadata("binance", symbol)

    def fetch_orderbook(self, *, symbol, limit):
        assert limit == 20
        return binance_book(symbol)


class FakeBybit:
    def fetch_instrument(self, *, symbol, category):
        assert category == "spot"
        return metadata("bybit", symbol)

    def fetch_orderbook(self, *, category, symbol, limit):
        assert category == "spot"
        assert limit == 50
        return bybit_book(symbol)


class FailedVenue:
    def fetch_instrument(self, *, symbol):
        raise RuntimeError("private or provider detail must not leak")


def base_args():
    return {
        "venue_instruments": {"binance": "BTCUSDT", "bybit": "BTCUSDT"},
        "base_code": "BTC",
        "quote_code": "USDT",
        "base_asset_id": "asset:canonical:BTC",
        "quote_currency_id": "currency:canonical:USDT",
        "base_quantity": "0.1",
        "now": NOW,
        "client_factories": {
            "binance": FakeBinance,
            "bybit": FakeBybit,
        },
    }


def test_two_venue_scan_sweeps_depth_but_never_claims_net_profit():
    result = scan_cex_spot_depth(**base_args())

    assert result["mode"] == "read_only_cex_spot_depth_scan"
    assert result["status"] == "complete"
    assert result["venue_ids"] == ["binance", "bybit"]
    assert result["compared_pair_count"] == 1
    assert result["fee_profile"] == "not_supplied"
    assert result["net_profit_claim"] is False
    assert result["execution_verified"] is False
    assert result["live_execution"] is False
    assert len(result["opportunities"]) == 2

    buy_binance = next(x for x in result["opportunities"] if x["buy_venue"] == "binance")
    assert buy_binance["status"] == "incomplete_costs"
    assert buy_binance["gross_profit_quote"] == "0.01"
    assert buy_binance["evaluation"]["net_profit_quote"] is None
    assert set(buy_binance["evaluation"]["missing_required_costs"]) == {
        "buy_fee_bps",
        "sell_fee_bps",
        "buy_slippage_bps",
        "sell_slippage_bps",
        "latency_cost_bps",
    }


def test_one_provider_failure_preserves_partial_coverage_without_leaking_exception():
    args = base_args()
    args["venue_instruments"] = {
        "binance": "BTCUSDT",
        "bybit": "BTCUSDT",
        "coinbase": "BTC-USDT",
    }
    args["client_factories"] = {
        "binance": FakeBinance,
        "bybit": FakeBybit,
        "coinbase": FailedVenue,
    }

    result = scan_cex_spot_depth(**args)

    assert result["status"] == "partial"
    assert result["compared_pair_count"] == 1
    assert result["source_failures"] == {"coinbase": "provider_request_failed"}
    assert "private or provider detail" not in str(result)


def test_asset_code_mismatch_fails_closed_and_is_not_joined_by_symbol():
    class MismatchedBybit(FakeBybit):
        def fetch_instrument(self, *, symbol, category):
            return metadata("bybit", symbol, base="WBTC")

    args = base_args()
    args["client_factories"] = {
        "binance": FakeBinance,
        "bybit": MismatchedBybit,
    }
    result = scan_cex_spot_depth(**args)

    assert result["status"] == "insufficient_sources"
    assert result["source_failures"] == {"bybit": "asset_code_mismatch"}
    assert result["opportunities"] == []


def test_invalid_venue_count_and_unknown_venues_are_rejected():
    args = base_args()
    args["venue_instruments"] = {"binance": "BTCUSDT"}
    with pytest.raises(CexScanError, match="between two and four"):
        scan_cex_spot_depth(**args)

    args = base_args()
    args["venue_instruments"] = {
        "binance": "BTCUSDT",
        "unknown": "BTCUSDT",
    }
    with pytest.raises(CexScanError, match="unsupported_venue"):
        scan_cex_spot_depth(**args)


def test_invalid_quantity_and_time_bounds_are_rejected():
    args = base_args()
    args["base_quantity"] = "NaN"
    with pytest.raises(CexScanError, match="invalid_base_quantity"):
        scan_cex_spot_depth(**args)

    args = base_args()
    args["max_quote_skew_seconds"] = -1
    with pytest.raises(CexScanError, match="invalid_max_quote_skew_seconds"):
        scan_cex_spot_depth(**args)
