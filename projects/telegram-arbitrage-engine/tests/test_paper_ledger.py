import sqlite3
from datetime import datetime, timezone

import pytest

from arb_engine.paper_ledger import PaperTradingLedger


TOKEN_A = "0x" + "1" * 40
TOKEN_B = "0x" + "2" * 40
POOL_A = "0x" + "3" * 40
POOL_B = "0x" + "4" * 40
BLOCK_HASH = "0x" + "a" * 64
AT = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def candidate_event(ledger, *, event_id="evt-1", payload=None):
    return ledger.append_event(
        candidate_id="candidate-001",
        event_id=event_id,
        event_type="candidate_detected",
        occurred_at=AT,
        payload=payload or {"source": "unit-test", "classification": "research_only"},
    )


def test_persists_ordered_provenance_linked_lifecycle_and_survives_restart(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = PaperTradingLedger(path)
    assert candidate_event(ledger)
    assert ledger.append_event(
        candidate_id="candidate-001", event_id="evt-2",
        event_type="snapshot_captured", occurred_at=AT,
        payload={"snapshot_source": "read_only_rpc"},
        chain_id=1, block_number=20_000_000, block_hash=BLOCK_HASH,
    )
    assert ledger.append_event(
        candidate_id="candidate-001", event_id="evt-3",
        event_type="route_simulated", occurred_at=AT,
        payload={"classification": "reserve_math_only"},
        chain_id=1, block_number=20_000_000, block_hash=BLOCK_HASH,
        strategy_id="v2-cross-venue-flash-swap",
        source_pool=POOL_A, destination_pool=POOL_B,
        token_in=TOKEN_A, token_out=TOKEN_B,
        gross_profit_raw=123_456,
    )
    assert ledger.append_event(
        candidate_id="candidate-001", event_id="evt-4",
        event_type="gas_scenario_evaluated", occurred_at=AT,
        payload={"classification": "scenario_estimate"},
        chain_id=1, block_number=20_000_000, block_hash=BLOCK_HASH,
        gas_used=250_000, gas_price_wei=20_000_000_000,
        gas_cost_profit_raw=40, net_profit_raw=123_416,
    )

    reopened = PaperTradingLedger(path)
    events = reopened.events("candidate-001")
    assert [item["sequence"] for item in events] == [1, 2, 3, 4]
    assert [item["event_type"] for item in events] == [
        "candidate_detected", "snapshot_captured", "route_simulated",
        "gas_scenario_evaluated",
    ]
    assert events[2]["block_hash"] == BLOCK_HASH
    assert events[2]["source_pool"] == POOL_A
    assert events[3]["gas_price_wei"] == str(20_000_000_000)
    assert events[3]["net_profit_raw"] == "123416"
    assert reopened.verify_chain("candidate-001")


def test_event_id_is_idempotent_but_conflicting_replay_is_rejected(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "idempotency.sqlite3")
    assert candidate_event(ledger) is True
    assert candidate_event(ledger) is False
    with pytest.raises(ValueError, match="different event content"):
        candidate_event(ledger, payload={"source": "different"})
    assert len(ledger.events("candidate-001")) == 1
    assert ledger.verify_chain("candidate-001")


def test_large_raw_token_values_are_stored_exactly_as_decimal_strings(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "large-integers.sqlite3")
    value = 2**255 - 1
    ledger.append_event(
        candidate_id="large-candidate", event_id="large-event",
        event_type="route_simulated", occurred_at=AT, payload={},
        chain_id=1, block_number=1, block_hash=BLOCK_HASH,
        strategy_id="v2-test", source_pool=POOL_A, destination_pool=POOL_B,
        token_in=TOKEN_A, token_out=TOKEN_B,
        gross_profit_raw=value, net_profit_raw=-value,
    )
    row = ledger.events("large-candidate")[0]
    assert row["gross_profit_raw"] == str(value)
    assert row["net_profit_raw"] == str(-value)
    assert ledger.verify_chain("large-candidate")


def test_rejects_invalid_event_types_provenance_and_required_route_fields(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "validation.sqlite3")
    with pytest.raises(ValueError, match="event type"):
        ledger.append_event(
            candidate_id="c", event_id="e", event_type=[], occurred_at=AT, payload={},
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.append_event(
            candidate_id="c", event_id="e2", event_type="candidate_detected",
            occurred_at=datetime(2026, 11, 2), payload={},
        )
    with pytest.raises(ValueError, match="supplied together"):
        ledger.append_event(
            candidate_id="c", event_id="e3", event_type="snapshot_captured",
            occurred_at=AT, payload={}, chain_id=1,
        )
    with pytest.raises(ValueError, match="32-byte"):
        ledger.append_event(
            candidate_id="c", event_id="e4", event_type="snapshot_captured",
            occurred_at=AT, payload={}, chain_id=1, block_number=1, block_hash="0x1234",
        )
    with pytest.raises(ValueError, match="requires canonical"):
        ledger.append_event(
            candidate_id="c", event_id="e5", event_type="route_simulated",
            occurred_at=AT, payload={},
        )
    with pytest.raises(ValueError, match="requires strategy"):
        ledger.append_event(
            candidate_id="c", event_id="e6", event_type="route_simulated",
            occurred_at=AT, payload={}, chain_id=1, block_number=1, block_hash=BLOCK_HASH,
        )
    with pytest.raises(ValueError, match="distinct"):
        ledger.append_event(
            candidate_id="c", event_id="e7", event_type="route_simulated",
            occurred_at=AT, payload={}, chain_id=1, block_number=1, block_hash=BLOCK_HASH,
            strategy_id="v2", source_pool=POOL_A, destination_pool=POOL_A,
            token_in=TOKEN_A, token_out=TOKEN_B,
        )


def test_gas_scenario_requires_explicit_cost_and_validates_nonnegative_gas(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "gas-validation.sqlite3")
    common = dict(
        candidate_id="c", event_type="gas_scenario_evaluated", occurred_at=AT,
        payload={}, chain_id=1, block_number=1, block_hash=BLOCK_HASH,
    )
    with pytest.raises(ValueError, match="requires gas"):
        ledger.append_event(event_id="e1", **common)
    with pytest.raises(ValueError, match="gas_used"):
        ledger.append_event(
            event_id="e2", gas_used=True, gas_price_wei=1,
            gas_cost_profit_raw=1, net_profit_raw=0, **common,
        )
    with pytest.raises(ValueError, match="gas_price_wei"):
        ledger.append_event(
            event_id="e3", gas_used=1, gas_price_wei=-1,
            gas_cost_profit_raw=1, net_profit_raw=0, **common,
        )


def test_sqlite_triggers_prevent_event_and_checkpoint_mutation(tmp_path):
    path = tmp_path / "append-only.sqlite3"
    ledger = PaperTradingLedger(path)
    candidate_event(ledger)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE paper_events SET payload_json = '{}' WHERE event_id = 'evt-1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM paper_events WHERE event_id = 'evt-1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE paper_event_checkpoints SET event_hash = 'bad' WHERE event_id = 'evt-1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM paper_event_checkpoints WHERE event_id = 'evt-1'")


def test_hash_chain_detects_modified_payload_and_deleted_tail(tmp_path):
    path = tmp_path / "tamper.sqlite3"
    ledger = PaperTradingLedger(path)
    candidate_event(ledger)
    ledger.append_event(
        candidate_id="candidate-001", event_id="evt-2",
        event_type="snapshot_captured", occurred_at=AT, payload={"block": 1},
        chain_id=1, block_number=1, block_hash=BLOCK_HASH,
    )
    assert ledger.verify_chain("candidate-001")

    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER paper_events_no_update")
        db.execute("UPDATE paper_events SET payload_json = '{}' WHERE event_id = 'evt-1'")
    assert ledger.verify_chain("candidate-001") is False

    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER paper_events_no_delete")
        db.execute("DELETE FROM paper_events WHERE event_id = 'evt-2'")
    assert ledger.verify_chain("candidate-001") is False


def test_payload_is_bounded_and_rejects_non_finite_json(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "payload.sqlite3")
    with pytest.raises(ValueError, match="finite JSON"):
        candidate_event(ledger, payload={"not_finite": float("nan")})
    with pytest.raises(ValueError, match="64 KiB"):
        candidate_event(ledger, payload={"large": "x" * 70_000})
