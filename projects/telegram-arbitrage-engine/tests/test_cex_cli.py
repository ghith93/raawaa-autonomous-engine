import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from arb_engine import cli
from arb_engine.market_sources.instruments import InstrumentMetadata


BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"


def metadata(venue: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        venue_id=venue,
        instrument_id="BTCUSDT",
        market_family="cex_spot",
        base_asset_code="BTC",
        quote_asset_code="USDT",
        base_step_size="0.001",
        price_tick_size="0.1",
        status="TRADING",
        fetched_at=datetime.now(timezone.utc),
        min_base_quantity="0.001",
        min_quote_notional="5",
    )


def book(venue: str, *, bids: tuple, asks: tuple):
    return SimpleNamespace(
        source=venue,
        category="spot",
        symbol="BTCUSDT",
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


def command_args(*extra):
    return [
        "arb-engine", "cex-scan",
        "--venue-a", "bybit",
        "--instrument-a", "BTCUSDT",
        "--a-base-asset-id", BTC,
        "--a-quote-currency-id", USDT,
        "--venue-b", "binance",
        "--instrument-b", "BTCUSDT",
        "--b-base-asset-id", BTC,
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
    output = __import__("json").loads(capsys.readouterr().out)
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
            "--b-base-asset-id", "asset:canonical:wrapped-BTC",
            "--fee-a-bps", "10",
            "--fee-b-bps", "10",
            "--latency-cost-bps", "1",
        ),
    )

    cli.main()
    output = __import__("json").loads(capsys.readouterr().out)

    assert all(item["status"] == "asset_or_quote_identity_mismatch" for item in output["opportunities"])
    assert all(item["evaluation"] is None for item in output["opportunities"])
    assert all(item["gross_profit_quote"] is None for item in output["opportunities"])


def test_cex_cli_command_is_visible_in_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["arb-engine", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert "cex-scan" in capsys.readouterr().out
