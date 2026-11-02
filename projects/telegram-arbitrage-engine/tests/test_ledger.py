from decimal import Decimal

from arb_engine.ledger import OpportunityLedger
from arb_engine.scanner import PriceSpread


def test_ledger_records_and_reads_observations(tmp_path):
    ledger = OpportunityLedger(tmp_path / "test.sqlite3")
    item = PriceSpread(
        chain="ethereum", base_symbol="AAA", quote_symbol="USDC",
        base_address="0x" + "1" * 40, quote_address="0x" + "2" * 40,
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


def test_engine_state_is_persisted_and_validated(tmp_path):
    ledger = OpportunityLedger(tmp_path / "state.sqlite3")
    assert ledger.get_state("telegram_update_offset") is None
    ledger.set_state("telegram_update_offset", "42")
    assert ledger.get_state("telegram_update_offset") == "42"
    ledger.set_state("telegram_update_offset", "43")
    assert ledger.get_state("telegram_update_offset") == "43"


def test_summary_separates_different_contracts_with_same_symbols(tmp_path):
    ledger = OpportunityLedger(tmp_path / "symbol-collision.sqlite3")
    common = dict(
        chain="ethereum", base_symbol="AAA", quote_symbol="USDC",
        quote_address="0x" + "2" * 40,
        buy_venue="dex-a", sell_venue="dex-b",
        buy_price_usd=Decimal("1"), sell_price_usd=Decimal("1.01"),
        gross_spread_bps=Decimal("100"), min_pool_liquidity_usd=Decimal("20000"),
        total_24h_volume_usd=Decimal("50000"), retrieval_age_seconds=Decimal("0.5"),
        buy_pair_url="https://a", sell_pair_url="https://b",
    )
    first = PriceSpread(base_address="0x" + "1" * 40, **common)
    second = PriceSpread(base_address="0x" + "3" * 40, **common)
    assert ledger.record([first, second]) == 2
    assert len(ledger.summarize()) == 2


def test_ledger_migrates_legacy_unique_key_and_preserves_rows(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL, chain TEXT NOT NULL,
            base_symbol TEXT NOT NULL, quote_symbol TEXT NOT NULL,
            buy_venue TEXT NOT NULL, sell_venue TEXT NOT NULL,
            buy_price TEXT NOT NULL, sell_price TEXT NOT NULL,
            gross_spread_bps TEXT NOT NULL, min_liquidity_usd TEXT NOT NULL,
            total_volume_24h_usd TEXT NOT NULL, classification TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(observed_at, chain, base_symbol, quote_symbol, buy_venue, sell_venue)
        )""")
        db.execute("""INSERT INTO observations
            (observed_at, chain, base_symbol, quote_symbol, buy_venue, sell_venue,
             buy_price, sell_price, gross_spread_bps, min_liquidity_usd,
             total_volume_24h_usd, classification, payload_json)
            VALUES ('2026-01-01T00:00:00+00:00', 'ethereum', 'AAA', 'USDC',
                    'dex-a', 'dex-b', '1', '1.01', '100', '10000', '20000',
                    'indicative_only_not_executable', '{}')""")
    ledger = OpportunityLedger(path)
    assert len(ledger.recent()) == 1
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(observations)")}
    assert "base_address" in columns and "quote_address" in columns
    unique_columns = []
    with sqlite3.connect(path) as db:
        for index in db.execute("PRAGMA index_list(observations)").fetchall():
            if index[2]:
                unique_columns.append(tuple(row[2] for row in db.execute(f"PRAGMA index_info('{index[1]}')")))
    assert ("observed_at", "chain", "base_symbol", "quote_symbol",
            "base_address", "quote_address", "buy_venue", "sell_venue") in unique_columns
