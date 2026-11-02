from datetime import datetime, timezone

import pytest

from arb_engine.flash_swap_cli import run_flash_swap_simulation
from arb_engine.paper_ledger import PaperTradingLedger
from arb_engine.paper_observer import observe_flash_swap_outcome
from arb_engine.rpc import EvmPoolSnapshot
from arb_engine.v2 import V2PoolState


QUOTE = "0x" + "1" * 40
BASE = "0x" + "2" * 40
NATIVE = "0x" + "3" * 40
SOURCE = "0x" + "4" * 40
DESTINATION = "0x" + "5" * 40
NATIVE_QUOTE = "0x" + "6" * 40
CANDIDATE_HASH = "0x" + "a" * 64
OBSERVED_HASH = "0x" + "b" * 64
CANDIDATE_TIME = 1_798_000_000


def snapshot(pair, token0, token1, reserve0, reserve1, decimals0, decimals1, *, block, block_hash, timestamp):
    state = V2PoolState(
        chain_id=1, pair_address=pair, token0=token0, token1=token1,
        reserve0_raw=reserve0, reserve1_raw=reserve1, fee_bps=30,
        block_number=block, block_hash=block_hash,
        observed_at=datetime.fromtimestamp(timestamp, timezone.utc),
    )
    return EvmPoolSnapshot(
        state=state, token0_decimals=decimals0, token1_decimals=decimals1,
        block_timestamp=timestamp,
    )


def make_reader(*, block, block_hash, timestamp, destination_quote_reserve=2_100_000 * 10**6):
    return FakeReader({
        SOURCE: snapshot(
            SOURCE, QUOTE, BASE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18,
            block=block, block_hash=block_hash, timestamp=timestamp,
        ),
        DESTINATION: snapshot(
            DESTINATION, QUOTE, BASE, destination_quote_reserve, 1_000 * 10**18, 6, 18,
            block=block, block_hash=block_hash, timestamp=timestamp,
        ),
        NATIVE_QUOTE: snapshot(
            NATIVE_QUOTE, QUOTE, NATIVE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18,
            block=block, block_hash=block_hash, timestamp=timestamp,
        ),
    })


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


def capture_candidate(ledger_path, *, with_gas=False):
    ledger = PaperTradingLedger(ledger_path)
    args = dict(
        source_pair=SOURCE, source_venue=None, source_fee_bps=30,
        destination_pair=DESTINATION, destination_venue=None, destination_fee_bps=30,
        borrowed_token=BASE, amount_borrowed_raw=10**18,
        chain_id=1, rpc_url="https://rpc.example.invalid",
        reader=make_reader(block=100, block_hash=CANDIDATE_HASH, timestamp=CANDIDATE_TIME),
        record_paper=True, ledger_path=str(ledger_path),
    )
    if with_gas:
        args.update(
            gas_used=100_000, gas_price_wei=20_000_000_000,
            native_token=NATIVE, native_quote_pair=NATIVE_QUOTE,
            native_quote_fee_bps=30,
        )
    result = run_flash_swap_simulation(**args)
    return ledger, result["paper_ledger"]["candidate_id"]


def test_forward_observer_records_only_a_later_canonical_block(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper.sqlite3")
    reader = make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12)
    result = observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)

    assert result["mode"] == "read_only_forward_paper_observation"
    assert result["candidate_block_number"] == 100
    assert result["observed_block_number"] == 101
    assert result["block_age"] == 1
    assert result["chain_verified"] is True
    assert result["transaction_submitted"] is False
    assert result["wallet_or_signer_used"] is False
    assert result["live_execution"] is False
    assert reader.canonical_checks == [(101, OBSERVED_HASH)]
    outcomes = [event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]
    assert len(outcomes) == 1
    assert outcomes[0]["block_number"] == 101


def test_observer_rejects_same_or_earlier_block_without_writing(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper.sqlite3")
    reader = make_reader(block=100, block_hash=CANDIDATE_HASH, timestamp=CANDIDATE_TIME)
    with pytest.raises(ValueError, match="strictly later"):
        observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)
    assert not [event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]


def test_observer_rejects_later_block_with_non_later_timestamp(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper.sqlite3")
    reader = make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME)
    with pytest.raises(ValueError, match="timestamp must be later"):
        observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)
    assert not [event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]


def test_gas_scenario_is_replayed_at_the_forward_block_and_is_idempotent(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper-gas.sqlite3", with_gas=True)
    reader = make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12)
    first = observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)
    second = observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)

    assert first["gas_economics"] is not None
    assert first["recorded"] is True
    assert second["recorded"] is False
    assert second["replayed"] is True
    assert ledger.verify_chain(candidate_id)
    assert len([event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]) == 1


def test_observer_rejects_unrecorded_token_decimal_provenance(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper.sqlite3")
    reader = make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12)
    reader.states[SOURCE] = snapshot(
        SOURCE, "0x" + "7" * 40, BASE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18,
        block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12,
    )
    with pytest.raises(ValueError, match="absent from candidate decimal provenance"):
        observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)
    assert not [event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]


def test_observer_rejects_mismatched_native_quote_pool_identity(tmp_path):
    ledger, candidate_id = capture_candidate(tmp_path / "paper-gas.sqlite3", with_gas=True)
    reader = make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12)
    reader.states[NATIVE_QUOTE] = snapshot(
        "0x" + "7" * 40, QUOTE, NATIVE, 2_000_000 * 10**6, 1_000 * 10**18, 6, 18,
        block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12,
    )
    with pytest.raises(ValueError, match="different pool than the requested snapshot"):
        observe_flash_swap_outcome(candidate_id, ledger=ledger, reader=reader)
    assert not [event for event in ledger.events(candidate_id) if event["event_type"] == "outcome_observed"]


def test_observer_rejects_legacy_candidate_without_forward_parameters(tmp_path):
    ledger = PaperTradingLedger(tmp_path / "legacy.sqlite3")
    occurred_at = datetime.fromtimestamp(CANDIDATE_TIME, timezone.utc)
    ledger.append_event(
        candidate_id="legacy-candidate", event_id="legacy-detected",
        event_type="candidate_detected", occurred_at=occurred_at,
        payload={"source": "legacy", "classification": "research_only"},
    )
    ledger.append_event(
        candidate_id="legacy-candidate", event_id="legacy-snapshot",
        event_type="snapshot_captured", occurred_at=occurred_at,
        payload={
            "pool_addresses": [SOURCE, DESTINATION],
            "token_decimals": {QUOTE: 6, BASE: 18},
        },
        chain_id=1, block_number=100, block_hash=CANDIDATE_HASH,
    )
    ledger.append_event(
        candidate_id="legacy-candidate", event_id="legacy-route",
        event_type="route_simulated", occurred_at=occurred_at,
        payload={"amount_borrowed_raw": str(10**18)},
        chain_id=1, block_number=100, block_hash=CANDIDATE_HASH,
        strategy_id="v2-cross-venue-flash-swap",
        source_pool=SOURCE, destination_pool=DESTINATION,
        token_in=BASE, token_out=QUOTE, gross_profit_raw=1,
    )
    with pytest.raises(ValueError, match="predates observer metadata"):
        observe_flash_swap_outcome(
            "legacy-candidate", ledger=ledger,
            reader=make_reader(block=101, block_hash=OBSERVED_HASH, timestamp=CANDIDATE_TIME + 12),
        )
