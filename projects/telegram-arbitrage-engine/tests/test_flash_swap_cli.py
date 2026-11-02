from datetime import datetime, timezone
import sys

import pytest

from arb_engine.cli import main
import arb_engine.flash_swap_cli as flash_swap_module
from arb_engine.flash_swap_cli import run_flash_swap_simulation
from arb_engine.rpc import EvmPoolSnapshot
from arb_engine.v2 import V2PoolState


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
NATIVE = "0x" + "3" * 40
SOURCE = "0x" + "4" * 40
DESTINATION = "0x" + "5" * 40
NATIVE_QUOTE = "0x" + "6" * 40
HASH = "0x" + "a" * 64


def snapshot(pair, token0, token1, reserve0, reserve1, decimals0, decimals1, *, block=100, block_hash=HASH):
    state = V2PoolState(
        chain_id=1, pair_address=pair, token0=token0, token1=token1,
        reserve0_raw=reserve0, reserve1_raw=reserve1, fee_bps=30,
        block_number=block, block_hash=block_hash,
        observed_at=datetime.now(timezone.utc),
    )
    return EvmPoolSnapshot(
        state=state, token0_decimals=decimals0, token1_decimals=decimals1,
        block_timestamp=1_798_000_000,
    )


class FakeReader:
    def __init__(self, states):
        self.states = states
        self.requested = None
        self.canonical_checks = []

    def read_pools(self, pools):
        self.requested = pools
        return [self.states[address] for address, _fee in pools]

    def verify_canonical_block(self, block_number, block_hash):
        self.canonical_checks.append((block_number, block_hash))

    def verify_v2_pool_factory(self, *args):
        raise AssertionError("Manual fee mode must not invent factory verification")

    def verify_v2_router_factory(self, *args):
        raise AssertionError("Manual fee mode must not invent router verification")


def reader():
    return FakeReader({
        SOURCE: snapshot(SOURCE, QUOTE, BASE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18),
        DESTINATION: snapshot(DESTINATION, QUOTE, BASE, 2_100_000 * 10**6, 1_000 * 10**18, 6, 18),
        NATIVE_QUOTE: snapshot(NATIVE_QUOTE, QUOTE, NATIVE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18),
    })


def base_args():
    return dict(
        source_pair=SOURCE, source_venue=None, source_fee_bps=30,
        destination_pair=DESTINATION, destination_venue=None, destination_fee_bps=30,
        borrowed_token=BASE, amount_borrowed_raw=10**18,
        chain_id=1, rpc_url="https://rpc.example.invalid",
    )


def test_flash_swap_cli_is_read_only_and_does_not_write_without_opt_in(tmp_path):
    fake = reader()
    result = run_flash_swap_simulation(**base_args(), reader=fake, ledger_path=str(tmp_path / "paper.db"))
    assert result["mode"] == "read_only_v2_flash_swap_simulation"
    assert int(result["gross_profit_raw"]) > 0
    assert result["gas_economics"] is None
    assert result["paper_ledger"] is None
    assert result["transaction_submitted"] is False
    assert result["wallet_or_signer_used"] is False
    assert result["live_execution"] is False
    assert fake.requested == [(SOURCE, 30), (DESTINATION, 30)]
    assert fake.canonical_checks == [(100, HASH)]
    assert not (tmp_path / "paper.db").exists()


def test_gas_scenario_uses_same_block_weth_usdc_and_reports_net(tmp_path):
    fake = reader()
    result = run_flash_swap_simulation(
        **base_args(), reader=fake,
        gas_used=100_000, gas_price_wei=20_000_000_000,
        native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
        native_quote_fee_bps=30,
        ledger_path=str(tmp_path / "paper.db"),
    )
    gas = result["gas_economics"]
    assert fake.requested == [(SOURCE, 30), (DESTINATION, 30), (NATIVE_QUOTE, 30)]
    assert result["borrowed_token_decimals"] == 18
    assert result["repayment_token_decimals"] == 6
    assert gas["native_token_decimals"] == 18
    assert gas["gas_cost_profit_token_raw"] == "4000000"
    assert int(gas["net_profit_raw"]) > 0
    assert gas["net_positive_under_scenario"] is True
    assert gas["native_quote_pool"]["block_hash"] == HASH


def test_opt_in_paper_capture_records_events_and_verifies_hash_chain(tmp_path):
    result = run_flash_swap_simulation(
        **base_args(), reader=reader(),
        gas_used=100_000, gas_price_wei=20_000_000_000,
        native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
        native_quote_fee_bps=30,
        record_paper=True, ledger_path=str(tmp_path / "paper.db"),
    )
    capture = result["paper_ledger"]
    assert capture["recorded"] is True
    assert capture["event_count"] == 4
    assert capture["chain_verified"] is True
    assert capture["classification"] == "local_research_events_only_no_order_created_or_submitted"


def test_rejects_partial_gas_inputs_and_missing_native_quote_pool():
    with pytest.raises(ValueError, match="both"):
        run_flash_swap_simulation(
            **base_args(), reader=reader(), gas_used=100_000,
        )
    with pytest.raises(ValueError, match="native-quote-pair"):
        run_flash_swap_simulation(
            **base_args(), reader=reader(),
            gas_used=100_000, gas_price_wei=20_000_000_000,
            native_token=NATIVE,
        )


def test_rejects_mismatched_block_across_quote_pool():
    fake = reader()
    fake.states[NATIVE_QUOTE] = snapshot(
        NATIVE_QUOTE, QUOTE, NATIVE, 2_000_000 * 10**6, 1_000 * 10**18,
        6, 18, block=101,
    )
    with pytest.raises(ValueError, match="one canonical chain and block"):
        run_flash_swap_simulation(
            **base_args(), reader=fake,
            gas_used=100_000, gas_price_wei=20_000_000_000,
            native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
            native_quote_fee_bps=30,
        )


def test_rejects_wrong_native_quote_assets_and_inconsistent_decimals():
    fake = reader()
    fake.states[NATIVE_QUOTE] = snapshot(
        NATIVE_QUOTE, QUOTE, BASE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18,
    )
    with pytest.raises(ValueError, match="exactly the native"):
        run_flash_swap_simulation(
            **base_args(), reader=fake,
            gas_used=100_000, gas_price_wei=20_000_000_000,
            native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
            native_quote_fee_bps=30,
        )

def test_rejects_decimal_disagreement_in_native_quote_snapshot():
    fake = reader()
    fake.states[NATIVE_QUOTE] = snapshot(
        NATIVE_QUOTE, QUOTE, NATIVE, 2_000_000 * 10**6, 1_000 * 10**18, 18, 18,
    )
    with pytest.raises(ValueError, match="decimals disagree"):
        run_flash_swap_simulation(
            **base_args(), reader=fake,
            gas_used=100_000, gas_price_wei=20_000_000_000,
            native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
            native_quote_fee_bps=30,
        )


def test_negative_gross_surplus_is_recorded_as_rejected_not_as_profit(tmp_path):
    fake = reader()
    fake.states[DESTINATION] = snapshot(
        DESTINATION, QUOTE, BASE, 1_900_000 * 10**6, 1_000 * 10**18, 6, 18,
    )
    result = run_flash_swap_simulation(
        **base_args(), reader=fake, record_paper=True,
        ledger_path=str(tmp_path / "rejected.sqlite3"),
    )
    assert int(result["gross_profit_raw"]) < 0
    assert result["paper_ledger"]["event_count"] == 4
    assert result["paper_ledger"]["chain_verified"] is True



def test_flash_swap_cli_help_is_registered(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["arb-engine", "flash-swap-sim", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert "--record-paper" in capsys.readouterr().out



def test_paper_observe_cli_help_is_registered(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["arb-engine", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "paper-observe" in output
    assert "later canonical block" in output


def test_explicit_d1_backend_records_deterministic_idempotent_events(monkeypatch, tmp_path):
    class FakeD1Ledger:
        records = {}

        def __init__(self):
            self.verify_calls = []

        def append_event(self, **event):
            event_id = event["event_id"]
            if event_id in self.records:
                assert self.records[event_id] == event
            else:
                self.records[event_id] = event
            return {"event_id": event_id, "replay": event_id in self.records}

        def verify_chain(self, candidate_id):
            self.verify_calls.append(candidate_id)
            return True

    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_BACKEND", "d1")
    monkeypatch.setattr(flash_swap_module, "D1PaperTradingLedger", FakeD1Ledger)
    monkeypatch.setattr(
        flash_swap_module, "PaperTradingLedger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("D1 must not fall back to SQLite")),
    )
    first = run_flash_swap_simulation(
        **base_args(), reader=reader(), record_paper=True,
        ledger_path=str(tmp_path / "must-not-exist.sqlite3"),
    )
    second = run_flash_swap_simulation(
        **base_args(), reader=reader(), record_paper=True,
        ledger_path=str(tmp_path / "must-not-exist.sqlite3"),
    )
    assert first["paper_ledger"]["backend"] == "d1"
    assert second["paper_ledger"]["candidate_id"] == first["paper_ledger"]["candidate_id"]
    assert first["paper_ledger"]["event_count"] == 3
    assert len(FakeD1Ledger.records) == 3
    assert not (tmp_path / "must-not-exist.sqlite3").exists()


def test_d1_backend_initialization_failure_does_not_fallback_to_sqlite(monkeypatch, tmp_path):
    from arb_engine.d1_paper_ledger import D1PaperLedgerError

    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_BACKEND", "d1")

    def unavailable():
        raise D1PaperLedgerError("D1 endpoint is not configured")

    monkeypatch.setattr(flash_swap_module, "D1PaperTradingLedger", unavailable)
    monkeypatch.setattr(
        flash_swap_module, "PaperTradingLedger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must fail closed")),
    )
    with pytest.raises(D1PaperLedgerError, match="not configured"):
        run_flash_swap_simulation(
            **base_args(), reader=reader(), record_paper=True,
            ledger_path=str(tmp_path / "must-not-exist.sqlite3"),
        )
    assert not (tmp_path / "must-not-exist.sqlite3").exists()


def test_unknown_paper_backend_is_rejected_and_default_read_only_ignores_backend(monkeypatch):
    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_BACKEND", "remote-magic")
    with pytest.raises(ValueError, match="either 'sqlite' or 'd1'"):
        run_flash_swap_simulation(**base_args(), reader=reader(), record_paper=True)
    monkeypatch.setattr(
        flash_swap_module, "D1PaperTradingLedger",
        lambda: (_ for _ in ()).throw(AssertionError("read-only mode must not instantiate D1")),
    )
    result = run_flash_swap_simulation(**base_args(), reader=reader(), record_paper=False)
    assert result["paper_ledger"] is None
