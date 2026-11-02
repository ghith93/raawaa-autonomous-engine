from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from arb_engine.market_sources.binance import (
    BinanceMarketDataError,
    BinancePublicMarketData,
)
from arb_engine.market_sources.bybit import (
    BybitMarketDataError,
    BybitPublicMarketData,
)
from arb_engine.market_sources.cross_venue import (
    InstrumentMappingError,
    InstrumentRegistry,
)
from arb_engine.market_sources.instruments import InstrumentMetadata, InstrumentMetadataError


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def bybit_payload(**overrides):
    item = {
        "symbol": "BTCUSDT",
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "status": "Trading",
        "lotSizeFilter": {
            "basePrecision": "0.000001",
            "quotePrecision": "0.0000001",
            "minOrderQty": "0.000011",
            "minOrderAmt": "5",
        },
        "priceFilter": {"tickSize": "0.1"},
    }
    item.update(overrides)
    return {"retCode": 0, "result": {"category": "spot", "list": [item]}}


def binance_payload(**overrides):
    item = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "isSpotTradingAllowed": True,
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
        ],
    }
    item.update(overrides)
    return {"symbols": [item]}


def test_bybit_fetch_instrument_uses_public_metadata_and_exact_precision():
    calls = []
    client = BybitPublicMarketData(
        transport=lambda url, timeout: (
            calls.append((url, timeout))
            or __import__("json").dumps(bybit_payload()).encode()
        ),
        min_interval_seconds=0,
    )
    meta = client.fetch_instrument(symbol="BTCUSDT")

    assert meta.venue_id == "bybit"
    assert meta.instrument_id == "BTCUSDT"
    assert meta.market_family == "cex_spot"
    assert meta.base_asset_code == "BTC"
    assert meta.quote_asset_code == "USDT"
    assert meta.base_step_size == Decimal("0.000001")
    assert meta.price_tick_size == Decimal("0.1")
    assert meta.min_quote_notional == Decimal("5")
    assert meta.status == "Trading"
    parsed = urlsplit(calls[0][0])
    assert parsed.path == "/v5/market/instruments-info"
    assert parse_qs(parsed.query) == {"category": ["spot"], "symbol": ["BTCUSDT"]}


def test_bybit_instrument_metadata_rejects_symbol_and_category_mismatches():
    client = BybitPublicMarketData(
        transport=lambda _url, _timeout: __import__("json").dumps(
            bybit_payload(symbol="ETHUSDT")
        ).encode(),
        min_interval_seconds=0,
    )
    with pytest.raises(BybitMarketDataError, match="symbol did not match"):
        client.fetch_instrument(symbol="BTCUSDT")

    with pytest.raises(ValueError, match="spot instruments only"):
        client.fetch_instrument(symbol="BTCUSDT", category="linear")


def test_binance_fetch_instrument_uses_lot_and_price_filters():
    calls = []
    client = BinancePublicMarketData(
        transport=lambda url, timeout: (
            calls.append((url, timeout))
            or __import__("json").dumps(binance_payload()).encode()
        ),
        min_interval_seconds=1,
    )
    meta = client.fetch_instrument(symbol="BTCUSDT")

    assert meta.venue_id == "binance"
    assert meta.instrument_id == "BTCUSDT"
    assert meta.base_asset_code == "BTC"
    assert meta.quote_asset_code == "USDT"
    assert meta.base_step_size == Decimal("0.00001")
    assert meta.price_tick_size == Decimal("0.01")
    assert meta.min_base_quantity == Decimal("0.00001")
    assert meta.min_quote_notional == Decimal("5")
    assert meta.status == "TRADING"
    parsed = urlsplit(calls[0][0])
    assert parsed.path == "/api/v3/exchangeInfo"
    assert parse_qs(parsed.query) == {"symbol": ["BTCUSDT"]}


def test_binance_instrument_metadata_rejects_missing_filters_and_wrong_symbol():
    client = BinancePublicMarketData(
        transport=lambda _url, _timeout: __import__("json").dumps(
            binance_payload(symbol="ETHUSDT")
        ).encode(),
        min_interval_seconds=1,
    )
    with pytest.raises(BinanceMarketDataError, match="symbol did not match"):
        client.fetch_instrument(symbol="BTCUSDT")

    client = BinancePublicMarketData(
        transport=lambda _url, _timeout: __import__("json").dumps(
            binance_payload(filters=[])
        ).encode(),
        min_interval_seconds=1,
    )
    with pytest.raises(BinanceMarketDataError, match="LOT_SIZE or PRICE_FILTER"):
        client.fetch_instrument(symbol="BTCUSDT")


def test_registry_requires_explicit_per_venue_asset_code_mappings():
    bybit = InstrumentMetadata(
        venue_id="bybit", instrument_id="BTCUSDT", market_family="cex_spot",
        base_asset_code="BTC", quote_asset_code="USDT",
        base_step_size="0.000001", price_tick_size="0.1",
        status="Trading", fetched_at=NOW,
    )
    binance = InstrumentMetadata(
        venue_id="binance", instrument_id="BTCUSDT", market_family="cex_spot",
        base_asset_code="BTC", quote_asset_code="USDT",
        base_step_size="0.00001", price_tick_size="0.01",
        status="TRADING", fetched_at=NOW,
    )
    aliases = {
        ("bybit", "BTC"): "asset:canonical:BTC",
        ("bybit", "USDT"): "currency:canonical:USDT",
        ("binance", "BTC"): "asset:canonical:BTC",
        ("binance", "USDT"): "currency:canonical:USDT",
    }
    registry = InstrumentRegistry.from_metadata([bybit, binance], aliases)
    assert registry.resolve("bybit", "BTCUSDT").base_step_size == Decimal("0.000001")
    assert registry.resolve("binance", "BTCUSDT").base_step_size == Decimal("0.00001")
    assert registry.resolve("binance", "BTCUSDT").base_asset_id == "asset:canonical:BTC"

    with pytest.raises(InstrumentMappingError, match="missing explicit canonical mapping"):
        InstrumentRegistry.from_metadata(
            [bybit, binance],
            {("bybit", "BTC"): "asset:canonical:BTC", ("bybit", "USDT"): "currency:canonical:USDT"},
        )


def test_registry_skips_nontrading_instruments_and_rejects_duplicate_mappings():
    item = InstrumentMetadata(
        venue_id="binance", instrument_id="BTCUSDT", market_family="cex_spot",
        base_asset_code="BTC", quote_asset_code="USDT",
        base_step_size="0.00001", price_tick_size="0.01",
        status="BREAK", fetched_at=NOW,
    )
    aliases = {
        ("binance", "BTC"): "asset:canonical:BTC",
        ("binance", "USDT"): "currency:canonical:USDT",
    }
    assert InstrumentRegistry.from_metadata([item], aliases).resolve if False else True

    trading = InstrumentMetadata(
        venue_id="binance", instrument_id="BTCUSDT", market_family="cex_spot",
        base_asset_code="BTC", quote_asset_code="USDT",
        base_step_size="0.00001", price_tick_size="0.01",
        status="TRADING", fetched_at=NOW,
    )
    with pytest.raises(InstrumentMappingError, match="duplicate instrument mapping"):
        InstrumentRegistry.from_metadata([trading, trading], aliases)


def test_instrument_metadata_rejects_float_steps_and_naive_timestamps():
    with pytest.raises(InstrumentMetadataError, match="Decimal or a decimal string"):
        InstrumentMetadata(
            venue_id="binance", instrument_id="BTCUSDT", market_family="cex_spot",
            base_asset_code="BTC", quote_asset_code="USDT",
            base_step_size=0.001, price_tick_size="0.01",
            status="TRADING", fetched_at=NOW,
        )
    with pytest.raises(InstrumentMetadataError, match="timezone-aware"):
        InstrumentMetadata(
            venue_id="binance", instrument_id="BTCUSDT", market_family="cex_spot",
            base_asset_code="BTC", quote_asset_code="USDT",
            base_step_size="0.001", price_tick_size="0.01",
            status="TRADING", fetched_at=datetime(2026, 11, 2, 12, 0),
        )
