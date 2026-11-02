import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_engine import cli
from arb_engine.market_sources.jupiter import (
    JupiterPriceBatch,
    JupiterPriceObservation,
    JupiterQuote,
)


SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)


def test_solana_price_cli_serializes_indicative_price_and_missing_mints(monkeypatch, capsys):
    observation = JupiterPriceObservation(
        source="jupiter",
        network="solana",
        mint=SOL,
        usd_price=Decimal("150.123456789"),
        decimals=9,
        block_id=348004023,
        price_change_24h=Decimal("-1.25"),
        liquidity_usd=Decimal("500000"),
        created_at=NOW,
        fetched_at=NOW,
    )
    batch = JupiterPriceBatch(
        requested_mints=(SOL, USDC),
        observations=(observation,),
        missing_mints=(USDC,),
        fetched_at=NOW,
    )

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout_seconds": 8.0}

        def fetch_prices(self, mints):
            assert mints == [SOL, USDC]
            return batch

    monkeypatch.setattr(cli, "JupiterPriceV3Client", FakeClient)
    monkeypatch.setattr(
        "sys.argv",
        ["arb-engine", "solana-price", "--mint", SOL, "--mint", USDC],
    )
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "read_only_solana_indicative_price"
    assert output["missing_mints"] == [USDC]
    assert output["observations"][0]["usd_price"] == "150.123456789"
    assert output["observations"][0]["price_change_24h_pct"] == "-1.25"
    assert output["observations"][0]["source_block_id"] == 348004023
    assert output["live_execution"] is False
    assert "not an executable price" in output["warning"]


def test_solana_quote_cli_serializes_quote_only_response(monkeypatch, capsys):
    quote = JupiterQuote(
        source="jupiter",
        network="solana",
        input_mint=SOL,
        output_mint=USDC,
        input_amount_raw=100_000_000,
        output_amount_raw=11680143,
        other_amount_threshold_raw=11621743,
        fee_bps=2,
        fee_mint=USDC,
        price_impact_pct=Decimal("0.0001"),
        router="metis",
        route_plan=({"swapInfo": {"label": "Orca"}},),
        request_id="quote-1",
        context_slot=345678900,
        fetched_at=NOW,
        transaction_present=False,
    )

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout_seconds": 8.0}

        def quote(self, **kwargs):
            assert kwargs == {
                "input_mint": SOL,
                "output_mint": USDC,
                "amount_raw": "100000000",
            }
            return quote

    monkeypatch.setattr(cli, "JupiterQuoteOnlyClient", FakeClient)
    monkeypatch.setattr(
        "sys.argv",
        [
            "arb-engine", "solana-quote",
            "--input-mint", SOL,
            "--output-mint", USDC,
            "--amount-raw", "100000000",
        ],
    )
    cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "read_only_solana_router_quote"
    assert output["output_amount_raw"] == "11680143"
    assert output["transaction_present"] is False
    assert output["live_execution"] is False
    assert "No transaction is built" in output["warning"]


def test_solana_cli_commands_are_visible_in_top_level_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["arb-engine", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "solana-price" in output
    assert "solana-quote" in output
