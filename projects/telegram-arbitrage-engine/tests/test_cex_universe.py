from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import sys

from arb_engine import cli
from arb_engine.cex_universe import (
    CexUniverseScanError,
    VenueRequest,
    scan_cex_universe,
)
from arb_engine.market_sources.instruments import InstrumentMetadata


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
BTC = "asset:canonical:BTC"
USDT = "currency:canonical:USDT"


def request(venue, instrument, *, base_id=BTC, quote_id=USDT, fee="10"):
    return VenueRequest(venue, instrument, base_id, quote_id, fee)


def snapshot(req, *, base_code="BTC", quote_code="USDT", ask="100", bid="99.9"):
    metadata = InstrumentMetadata(
        venue_id=req.venue_id,
        instrument_id=req.instrument_id,
        market_family="cex_spot",
        base_asset_code=base_code,
        quote_asset_code=quote_code,
        base_step_size="0.001",
        price_tick_size="0.01",
        status="TRADING",
        fetched_at=NOW,
        min_base_quantity="0.001",
        min_quote_notional="5",
    )
    book = SimpleNamespace(
        source=req.venue_id,
        symbol=req.instrument_id,
        category="spot",
        bids=((Decimal(bid), Decimal("2")),),
        asks=((Decimal(ask), Decimal("2")),),
        fetched_at=NOW,
    )
    return metadata, book


def test_three_venues_compare_all_pairs_and_both_directions():
    requests = [
        request("binance", "BTCUSDT"),
        request("bybit", "BTCUSDT"),
        request("okx", "BTC-USDT"),
    ]
    prices = {
        "binance": ("100", "99.8"),
        "bybit": ("100.1", "99.9"),
        "okx": ("99.9", "99.7"),
    }

    def fetcher(req):
        ask, bid = prices[req.venue_id]
        return snapshot(req, ask=ask, bid=bid)

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        latency_cost_bps="1",
        now=NOW,
        fetcher=fetcher,
    )

    assert result["mode"] == "read_only_cex_spot_universe_scan"
    assert result["status"] == "complete"
    assert result["compared_pair_count"] == 3
    assert len(result["opportunities"]) == 6
    assert result["fee_profiles"] == {"binance": "10", "bybit": "10", "okx": "10"}
    assert result["live_execution"] is False
    assert result["orders_submitted"] is False
    assert result["net_profit_claim"] is False
    assert all(item["execution_verified"] is False for item in result["opportunities"])


def test_partial_source_failure_preserves_healthy_venue_comparison_and_sanitizes_error():
    requests = [
        request("binance", "BTCUSDT"),
        request("bybit", "BTCUSDT"),
        request("okx", "BTC-USDT"),
    ]

    def fetcher(req):
        if req.venue_id == "okx":
            raise RuntimeError("raw provider payload and private request URL")
        return snapshot(req)

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        now=NOW,
        fetcher=fetcher,
    )

    assert result["status"] == "partial"
    assert result["compared_pair_count"] == 1
    assert result["source_failures"] == {"okx": "provider_request_failed"}
    assert "raw provider payload" not in str(result)


def test_quote_currency_code_mismatch_is_not_joined_without_conversion():
    requests = [
        request("binance", "BTCUSDT"),
        request("bybit", "BTCUSDT"),
        request("coinbase", "BTC-USD"),
    ]

    def fetcher(req):
        if req.venue_id == "coinbase":
            return snapshot(req, quote_code="USD")
        return snapshot(req)

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        now=NOW,
        fetcher=fetcher,
    )

    assert result["status"] == "partial"
    assert result["source_failures"] == {"coinbase": "quote_asset_code_mismatch"}
    assert result["compared_pair_count"] == 1


def test_operator_can_explicitly_map_kraken_xbt_to_canonical_btc():
    requests = [
        request("kraken", "XBTUSDT"),
        request("binance", "BTCUSDT"),
    ]

    def fetcher(req):
        if req.venue_id == "kraken":
            return snapshot(req, base_code="XBT")
        return snapshot(req)

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        now=NOW,
        fetcher=fetcher,
    )

    assert result["status"] == "complete"
    assert result["compared_pair_count"] == 1
    assert {item["canonical_base_asset_id"] for item in result["venues"]} == {BTC}


def test_canonical_asset_mismatch_skips_pair_even_when_symbols_look_similar():
    requests = [
        request("binance", "BTCUSDT"),
        request("bybit", "BTCUSDT", base_id="asset:canonical:wrapped-BTC"),
    ]

    def fetcher(req):
        return snapshot(req)

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        now=NOW,
        fetcher=fetcher,
    )

    assert result["status"] == "partial"
    assert result["compared_pair_count"] == 0
    assert len(result["skipped_pairs"]) == 1
    assert result["opportunities"] == []


def test_missing_fee_and_latency_inputs_never_become_zero_cost():
    requests = [
        request("binance", "BTCUSDT", fee=None),
        request("bybit", "BTCUSDT", fee=None),
    ]

    result = scan_cex_universe(
        venues=requests,
        base_quantity="0.1",
        now=NOW,
        fetcher=lambda req: snapshot(req),
    )

    assert result["opportunities"]
    for item in result["opportunities"]:
        assert item["status"] == "incomplete_costs"
        assert item["evaluation"]["net_profit_quote_estimate"] is None
        assert set(item["evaluation"]["missing_required_costs"]) == {
            "buy_fee_bps",
            "sell_fee_bps",
            "latency_cost_bps",
        }


def test_invalid_venue_count_duplicates_and_cost_values_are_rejected():
    with pytest.raises(CexUniverseScanError, match="between two and five"):
        scan_cex_universe(venues=[request("binance", "BTCUSDT")], base_quantity="0.1")

    with pytest.raises(CexUniverseScanError, match="duplicate_venue"):
        scan_cex_universe(
            venues=[request("binance", "BTCUSDT"), request("binance", "BTCUSDT")],
            base_quantity="0.1",
        )

    with pytest.raises(CexUniverseScanError, match="fee_bps_exceeds_1000"):
        request("binance", "BTCUSDT", fee="1001")

    with pytest.raises(CexUniverseScanError, match="canonical_asset_ids_must_be_distinct"):
        request("binance", "BTCUSDT", quote_id=BTC)

def test_cli_builds_explicit_venue_requests_and_fee_profiles(monkeypatch, capsys):
    captured = {}

    def fake_scan(**kwargs):
        captured.update(kwargs)
        return {"mode": "test", "status": "complete"}

    monkeypatch.setattr(cli, "scan_cex_universe", fake_scan)
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "cex-universe-scan",
        "--venue", "binance", "--instrument", "BTCUSDT",
        "--base-asset-id", BTC, "--quote-currency-id", USDT,
        "--venue", "kraken", "--instrument", "XBT/USDT",
        "--base-asset-id", BTC, "--quote-currency-id", USDT,
        "--fee-bps", "binance=10", "--fee-bps", "kraken=12",
        "--base-quantity", "0.1", "--latency-cost-bps", "2",
    ])

    cli.main()
    output = capsys.readouterr().out

    assert '"status": "complete"' in output
    assert [item.venue_id for item in captured["venues"]] == ["binance", "kraken"]
    assert [str(item.fee_bps) for item in captured["venues"]] == ["10", "12"]
    assert captured["latency_cost_bps"] == "2"


def test_cli_rejects_misaligned_repeated_venue_arguments(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "arb-engine", "cex-universe-scan",
        "--venue", "binance", "--venue", "bybit",
        "--instrument", "BTCUSDT",
        "--base-asset-id", BTC, "--quote-currency-id", USDT,
        "--base-quantity", "0.1",
    ])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
