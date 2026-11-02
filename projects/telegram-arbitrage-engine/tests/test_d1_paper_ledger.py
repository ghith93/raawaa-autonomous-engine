import json
from datetime import datetime, timezone

import pytest

from arb_engine.d1_paper_ledger import D1PaperTradingLedger, create_paper_ledger
from arb_engine.paper_ledger import PaperTradingLedger


def response(data, status=200):
    return status, json.dumps(data, separators=(",", ":")).encode()


def event_record():
    return {
        "candidate_id": "candidate-1",
        "event_id": "candidate-1:detected",
        "event_type": "candidate_detected",
        "occurred_at": datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
        "payload": {"source": "test", "classification": "research_only"},
    }


def test_d1_append_uses_https_bearer_and_decimal_strings():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, json.loads(body)))
        return response({"event_id": "candidate-1:detected", "event_hash": "a" * 64, "replay": False}, 201)

    ledger = D1PaperTradingLedger(
        "https://engine.example.com", "test-token", transport=transport,
    )
    assert ledger.append_event(**event_record()) is True
    method, url, headers, payload = calls[0]
    assert method == "POST"
    assert url == "https://engine.example.com/v1/paper-ledger"
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["User-Agent"].startswith("RaawaaAutonomousEngine/")
    assert payload["action"] == "append"
    assert payload["event"]["event_id"] == "candidate-1:detected"
    assert payload["event"]["occurred_at"] == "2026-11-02T12:00:00.000+00:00"


def test_d1_identical_retry_is_reported_as_idempotent_replay():
    def transport(_method, _url, _headers, _body):
        return response({"event_id": "candidate-1:detected", "event_hash": "b" * 64, "replay": True})

    ledger = D1PaperTradingLedger("https://engine.example.com", "test-token", transport=transport)
    assert ledger.append_event(**event_record()) is False


def test_d1_rejects_non_https_or_credential_bearing_endpoint():
    for endpoint in (
        "http://engine.example.com",
        "https://user:pass@engine.example.com",
        "https://engine.example.com/path?token=secret",
        "https://engine.example.com#fragment",
    ):
        with pytest.raises(ValueError, match="HTTPS"):
            D1PaperTradingLedger(endpoint, "test-token")


def test_d1_fails_closed_on_http_errors_and_malformed_responses():
    def unavailable(_method, _url, _headers, _body):
        return response({"error": "storage_unavailable"}, 503)

    ledger = D1PaperTradingLedger("https://engine.example.com", "test-token", transport=unavailable)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        ledger.append_event(**event_record())

    def malformed(_method, _url, _headers, _body):
        return 200, b"not-json"

    ledger = D1PaperTradingLedger("https://engine.example.com", "test-token", transport=malformed)
    with pytest.raises(RuntimeError, match="malformed JSON"):
        ledger.append_event(**event_record())


def test_d1_chain_verification_checks_events_checkpoints_and_head():
    payload_json = '{"classification":"research_only","source":"test"}'
    record = {
        "candidate_id": "candidate-1",
        "event_id": "candidate-1:detected",
        "event_type": "candidate_detected",
        "occurred_at": "2026-11-02T12:00:00.000+00:00",
        "chain_id": None,
        "block_number": None,
        "block_hash": None,
        "strategy_id": None,
        "source_pool": None,
        "destination_pool": None,
        "token_in": None,
        "token_out": None,
        "gross_profit_raw": None,
        "gas_used": None,
        "gas_price_wei": None,
        "gas_cost_profit_raw": None,
        "net_profit_raw": None,
        "payload_json": payload_json,
    }
    previous_hash = "0" * 64
    event_hash = PaperTradingLedger._hash_record({"sequence": 1, **record}, previous_hash)
    event = {**record, "sequence": 1, "previous_hash": previous_hash, "event_hash": event_hash}
    data = {
        "events": [event],
        "checkpoints": [{"sequence": 1, "event_id": record["event_id"], "event_hash": event_hash}],
        "head_sequence": 1,
        "head_hash": event_hash,
        "event_count": 1,
        "checkpoint_count": 1,
    }

    def transport(_method, _url, _headers, _body):
        return response(data)

    ledger = D1PaperTradingLedger("https://engine.example.com", "test-token", transport=transport)
    assert ledger.verify_chain("candidate-1") is True
    data["head_hash"] = "f" * 64
    assert ledger.verify_chain("candidate-1") is False


def test_backend_selection_keeps_sqlite_default_and_requires_explicit_d1_config(monkeypatch, tmp_path):
    monkeypatch.delenv("RAAWAA_PAPER_LEDGER_BACKEND", raising=False)
    monkeypatch.delenv("RAAWAA_PAPER_LEDGER_URL", raising=False)
    monkeypatch.delenv("RAAWAA_PAPER_LEDGER_TOKEN", raising=False)
    local = create_paper_ledger(str(tmp_path / "local.sqlite3"))
    assert isinstance(local, PaperTradingLedger)

    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_BACKEND", "d1")
    with pytest.raises(ValueError, match="HTTPS"):
        create_paper_ledger(str(tmp_path / "must-not-be-used.sqlite3"))
    assert not (tmp_path / "must-not-be-used.sqlite3").exists()

    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_URL", "https://engine.example.com")
    monkeypatch.setenv("RAAWAA_PAPER_LEDGER_TOKEN", "test-token")
    remote = create_paper_ledger(str(tmp_path / "unused.sqlite3"))
    assert isinstance(remote, D1PaperTradingLedger)
    assert not (tmp_path / "unused.sqlite3").exists()
