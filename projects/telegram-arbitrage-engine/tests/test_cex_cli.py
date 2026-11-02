import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from arb_engine import cli
from arb_engine.market_sources.instruments import InstrumentMetadata


BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"


def metadata(venue: str, instrument_id: str = "BTCUSDT", base_code: str = "BTC", quote_code: str = "USDT") -> InstrumentMetadata:
    return InstrumentMetadata(
        venue_id=venue,
        instrument_id=instrument_id,
        market_family="cex_spot",
        base_asset_code=base_code,
        quote_asset_code=quote_code,
        base_step_size="0.001",
        price_tick_size="0.1",
        status="TRADING",
        fetched_at=datetime.now(timezone.utc),
        min_base_quantity="0.001",
        min_quote_notional="5",
    )


def book(venue: str, *, bids: tuple, asks: tuple, symbol: str = "BTCUSDT"):
    return SimpleNamespace(
        source=venue,
        category="spot",
        symbol=symbol,
        bids=bids,
        asks=asks,
        fetched_at=datetime.now(timezone.utc),
    )


def install_fake_clients(monkeypatch):
    class FakeBybit:
        def fetch_instrument(self, *, symbol, category):
            assert (symbol, category) == ("BTCUSDT", "spot")
            return metadata("bybit")

        def fetch_orderbook(self, *, category, symbol, limit):
            assert (category, symbol, limit) == ("spot", "BTCUSDT", 50)
            return book(
                "bybit",
                bids=((Decimal("99.9"), Decimal("1")),),
                asks=((Decimal("100"), Decimal("1")),),
            )

    class FakeBinance:
        def fetch_instrument(self, *, symbol):
            assert symbol == "BTCUSDT"
            return metadata("binance")

        def fetch_orderbook(self, *, symbol, limit):
            assert (symbol, limit) == ("BTCUSDT", 100)
            return book(
                "binance",
                bids=((Decimal("101.2"), Decimal("1")),),
                asks=((Decimal("101.3"), Decimal("1")),),
            )

    monkeypatch.setattr(cli, "BybitPublicMarketData", FakeBybit)
    monkeypatch.setattr(cli, "BinancePublicMarketData", FakeBinance)


def command_args(*extra, b_base_id=BTC):
    return [
        "arb-engine", "cex-scan",
        "--venue-a", "bybit",
        "--instrument-a", "BTCUSDT",
        "--a-base-asset-id", BTC,
        "--a-quote-currency-id", USDT,
        "--venue-b", "binance",
        "--instrument-b", "BTCUSDT",
        "--b-base-asset-id", b_base_id,
        "--b-quote-currency-id", USDT,
        "--base-quantity", "0.5",
        *extra,
    ]


def test_cex_cli_sweeps_both_public_books_and_reports_operator_cost_estimate(monkeypatch, capsys):
    install_fake_clients(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        command_args("--fee-a-bps", "10", "--fee-b-bps", "10", "--latency-cost-bps", "1"),
    )

    cli.main()
    output = json.loads(capsys.readouterr().out)

    assert output["mode"] == "read_only_cex_spot_depth_comparison"
    assert output["live_execution"] is False
    assert output["orders_submitted"] is False
    assert output["canonical_mapping"]["bybit"]["base_asset_id"] == BTC
    assert len(output["opportunities"]) == 2

    forward, reverse = output["opportunities"]
    assert (forward["buy_venue"], forward["sell_venue"]) == ("bybit", "binance")
    assert forward["status"] == "net_positive_estimate"
    assert Decimal(forward["gross_profit_quote"]) == Decimal("0.6")
    assert Decimal(forward["evaluation"]["net_profit_quote_estimate"]) > 0
    assert forward["evaluation"]["execution_verified"] is False
    assert reverse["status"] == "net_nonpositive"
    assert output["net_profit_claim"] is False


def test_cex_cli_never_assumes_missing_fees_or_latency_are_zero(monkeypatch, capsys):
    install_fake_clients(monkeypatch)
    monkeypatch.setattr("sys.argv", command_args())

    cli.main()
    output = json.loads(capsys.readouterr().out)
    forward = output["opportunities"][0]

    assert forward["status"] == "incomplete_costs"
    assert forward["evaluation"]["net_profit_quote_estimate"] is None
    assert set(forward["evaluation"]["missing_required_costs"]) == {
        "buy_fee_bps",
        "sell_fee_bps",
        "latency_cost_bps",
    }


def test_cex_cli_blocks_symbol_match_when_canonical_asset_ids_differ(monkeypatch, capsys):
    install_fake_clients(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        command_args(
            "--fee-a-bps", "10",
            "--fee-b-bps", "10",
            "--latency-cost-bps", "1",
            b_base_id="asset:canonical:wrapped-BTC",
        ),
    )

    cli.main()
    output = json.loads(capsys.readouterr().out)

    assert all(item["status"] == "asset_or_quote_identity_mismatch" for item in output["opportunities"])
    assert all(item["evaluation"] is None for item in output["opportunities"])
    assert all(item["gross_profit_quote"] is None for item in output["opportunities"])


def test_cex_cli_command_is_visible_in_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["arb-engine", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert "cex-scan" in capsys.readouterr().out


def test_cex_snapshot_factory_uses_kraken_coinbase_and_okx_public_adapters(monkeypatch):
    class FakeKraken:
        def fetch_instrument(self, *, pair):
            assert pair == "XBT/USDT"
            return metadata("kraken", "XBTUSDT", "XBT", "USDT")

        def fetch_orderbook(self, *, pair, count):
            assert (pair, count) == ("XBTUSDT", 100)
            return book("kraken", symbol="XBTUSDT", bids=((Decimal("99"), Decimal("1")),), asks=((Decimal("100"), Decimal("1")),))

    class FakeCoinbase:
        def fetch_instrument(self, *, product_id):
            assert product_id == "BTC-USD"
            return metadata("coinbase", "BTC-USD", "BTC", "USD")

        def fetch_orderbook(self, *, product_id, level):
            assert (product_id, level) == ("BTC-USD", 2)
            return book("coinbase", symbol="BTC-USD", bids=((Decimal("99"), Decimal("1")),), asks=((Decimal("100"), Decimal("1")),))

    class FakeOKX:
        def fetch_instrument(self, *, inst_id):
            assert inst_id == "BTC-USDT"
            return metadata("okx", "BTC-USDT", "BTC", "USDT")

        def fetch_orderbook(self, *, inst_id, depth):
            assert (inst_id, depth) == ("BTC-USDT", 20)
            return book("okx", symbol="BTC-USDT", bids=((Decimal("99"), Decimal("1")),), asks=((Decimal("100"), Decimal("1")),))

    monkeypatch.setattr(cli, "KrakenPublicMarketData", FakeKraken)
    monkeypatch.setattr(cli, "CoinbaseExchangePublicMarketData", FakeCoinbase)
    monkeypatch.setattr(cli, "OKXPublicSpotMarketData", FakeOKX)

    for venue, instrument in (
        ("kraken", "XBT/USDT"),
        ("coinbase", "BTC-USD"),
        ("okx", "BTC-USDT"),
    ):
        instrument_metadata, snapshot = cli._fetch_cex_snapshot(venue, instrument)
        assert instrument_metadata.venue_id == venue
        assert snapshot.source == venue
        assert snapshot.symbol == instrument_metadata.instrument_id
