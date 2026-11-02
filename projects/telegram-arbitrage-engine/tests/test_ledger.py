from decimal import Decimal

from arb_engine.ledger import OpportunityLedger
from arb_engine.scanner import PriceSpread


def test_ledger_records_and_reads_observations(tmp_path):
    ledger = OpportunityLedger(tmp_path / "test.sqlite3")
    item = PriceSpread(
        chain="ethereum", base_symbol="AAA", quote_symbol="USDC",
        buy_venue="dex-a", sell_venue="dex-b", buy_price_usd=Decimal("1"),
        sell_price_usd=Decimal("1.01"), gross_spread_bps=Decimal("100"),
        min_pool_liquidity_usd=Decimal("20000"), total_24h_volume_usd=Decimal("50000"),
        retrieval_age_seconds=Decimal("0.5"), buy_pair_url="https://a", sell_pair_url="https://b",
    )
    assert ledger.record([item]) == 1
    rows = ledger.recent()
    assert len(rows) == 1
    assert rows[0]["gross_spread_bps"] == "100"
    assert rows[0]["classification"] == "indicative_only_not_executable"
    assert '"quote_timestamp_status":"not_provided_by_source"' in rows[0]["payload_json"]


def test_summary_groups_repeated_observations_without_calling_them_profit(tmp_path):
    ledger = OpportunityLedger(tmp_path / "summary.sqlite3")
    item = PriceSpread(
        chain="ethereum", base_symbol="AAA", quote_symbol="USDC",
        buy_venue="dex-a", sell_venue="dex-b", buy_price_usd=Decimal("1"),
        sell_price_usd=Decimal("1.01"), gross_spread_bps=Decimal("100"),
        min_pool_liquidity_usd=Decimal("20000"), total_24h_volume_usd=Decimal("50000"),
        retrieval_age_seconds=Decimal("0.5"), buy_pair_url="https://a", sell_pair_url="https://b",
    )
    ledger.record([item])
    ledger.record([item])
    summary = ledger.summarize()
    assert len(summary) == 1
    assert summary[0]["observation_count"] == 2
    assert summary[0]["mean_gross_spread_bps"] == "100"
    assert summary[0]["interpretation"] == "repeated_indicative_observations_not_backtested_profit"
