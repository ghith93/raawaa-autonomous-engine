import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.d1_paper_ledger import D1PaperLedgerError, D1PaperTradingLedger


ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self, payload, status=200):
        self.payload = json.dumps(payload).encode()
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.payload


class D1PaperLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = D1PaperTradingLedger("https://engine.example", "test-token")

    @patch("urllib.request.urlopen")
    def test_append_posts_authenticated_event_and_returns_sequence(self, open_url):
        open_url.return_value = Response({
            "event_id": "event-1", "sequence": 1, "event_hash": "a" * 64, "replay": False,
        }, 201)
        result = self.ledger.append_event(
            candidate_id="candidate-1", event_id="event-1",
            event_type="candidate_detected",
            occurred_at=__import__("datetime").datetime(2026, 11, 2, tzinfo=__import__("datetime").timezone.utc),
            payload={"source": "test"},
        )
        self.assertEqual(result["sequence"], 1)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://engine.example/v1/paper-ledger")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("User-agent"), "RaawaaAutonomousEngine/1.0")
        payload = json.loads(request.data)
        self.assertEqual(payload["action"], "append")
        self.assertEqual(payload["event"]["occurred_at"], "2026-11-02T00:00:00.000+00:00")

    @patch("urllib.request.urlopen")
    def test_append_normalizes_raw_integer_fields_to_decimal_strings(self, open_url):
        open_url.return_value = Response({
            "event_id": "gas-1", "sequence": 1, "event_hash": "a" * 64, "replay": False,
        }, 201)
        self.ledger.append_event(
            candidate_id="candidate-1", event_id="gas-1",
            event_type="gas_scenario_evaluated",
            occurred_at="2026-11-02T00:00:00Z",
            chain_id=1, block_number=100, block_hash="0x" + "a" * 64,
            gas_used=100_000, gas_price_wei=20_000_000_000,
            gas_cost_profit_raw=4_000_000, net_profit_raw=8_000_000,
            gross_profit_raw=12_000_000, payload={},
        )
        payload = json.loads(open_url.call_args.args[0].data)
        event = payload["event"]
        self.assertEqual(event["gas_used"], "100000")
        self.assertEqual(event["gas_price_wei"], "20000000000")
        self.assertEqual(event["gross_profit_raw"], "12000000")
        self.assertEqual(event["net_profit_raw"], "8000000")

    @patch("urllib.request.urlopen")
    def test_payload_rejects_floats_and_unsafe_integers_before_network(self, open_url):
        for payload in ({"fraction": 1.5}, {"unsafe": 2**53}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.ledger.append_event(
                    candidate_id="c", event_id="e", event_type="candidate_detected",
                    occurred_at="2026-11-02T00:00:00Z", payload=payload,
                )
        open_url.assert_not_called()

    def test_naive_iso_timestamp_string_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit timezone"):
            self.ledger.append_event(
                candidate_id="c", event_id="e", event_type="candidate_detected",
                occurred_at="2026-11-02T00:00:00", payload={},
            )

    @patch("urllib.request.urlopen")
    def test_verify_chain_accepts_exactly_the_requested_limit(self, open_url):
        record = {
            "candidate_id": "candidate-1", "event_id": "event-1",
            "event_type": "candidate_detected", "occurred_at": "2026-11-02T00:00:00.000+00:00",
            "chain_id": None, "block_number": None, "block_hash": None,
            "strategy_id": None, "source_pool": None, "destination_pool": None,
            "token_in": None, "token_out": None, "gross_profit_raw": None,
            "gas_used": None, "gas_price_wei": None, "gas_cost_profit_raw": None,
            "net_profit_raw": None, "payload_json": "{}",
        }
        envelope = {"previous_hash": "0" * 64, "record": {"sequence": 1, **record}}
        digest = hashlib.sha256(json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode()).hexdigest()
        event = {**record, "sequence": 1, "previous_hash": "0" * 64, "event_hash": digest}
        checkpoint = {"sequence": 1, "event_id": "event-1", "event_hash": digest}
        open_url.return_value = Response({
            "events": [event], "checkpoints": [checkpoint], "head_sequence": 1,
            "head_hash": digest, "event_count": 1, "checkpoint_count": 1,
        })
        self.assertTrue(self.ledger.verify_chain("candidate-1", limit=1))

    @patch("urllib.request.urlopen")
    def test_transport_failure_fails_closed_without_local_fallback(self, open_url):
        from urllib.error import URLError
        open_url.side_effect = URLError("offline")
        with self.assertRaisesRegex(D1PaperLedgerError, "transport failed"):
            self.ledger.append_event(
                candidate_id="c", event_id="e", event_type="candidate_detected",
                occurred_at="2026-11-02T00:00:00Z", payload={},
            )

    @patch("urllib.request.urlopen")
    def test_verify_chain_checks_event_digest_and_checkpoint(self, open_url):
        record = {
            "candidate_id": "candidate-1", "event_id": "event-1",
            "event_type": "candidate_detected", "occurred_at": "2026-11-02T00:00:00+00:00",
            "chain_id": None, "block_number": None, "block_hash": None,
            "strategy_id": None, "source_pool": None, "destination_pool": None,
            "token_in": None, "token_out": None, "gross_profit_raw": None,
            "gas_used": None, "gas_price_wei": None, "gas_cost_profit_raw": None,
            "net_profit_raw": None, "payload_json": "{}",
        }
        envelope = {"previous_hash": "0" * 64, "record": {"sequence": 1, **record}}
        digest = hashlib.sha256(json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode()).hexdigest()
        event = {**record, "sequence": 1, "previous_hash": "0" * 64, "event_hash": digest}
        checkpoint = {"sequence": 1, "event_id": "event-1", "event_hash": digest}
        open_url.return_value = Response({"events": [event], "checkpoints": [checkpoint], "head_sequence": 1, "head_hash": digest, "event_count": 1, "checkpoint_count": 1})
        self.assertTrue(self.ledger.verify_chain("candidate-1"))
        event["event_hash"] = "f" * 64
        open_url.return_value = Response({"events": [event], "checkpoints": [checkpoint]})
        self.assertFalse(self.ledger.verify_chain("candidate-1"))

    def test_configuration_requires_https_and_token(self):
        for url, token in (("http://engine.example", "token"), ("https://engine.example", "")):
            with self.subTest(url=url, token=bool(token)), self.assertRaises(D1PaperLedgerError):
                D1PaperTradingLedger(url, token)

    def test_naive_datetime_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            self.ledger.append_event(
                candidate_id="c", event_id="e", event_type="candidate_detected",
                occurred_at=__import__("datetime").datetime(2026, 11, 2), payload={},
            )

    def test_production_deploy_and_migration_are_manual_only(self):
        deploy = (ROOT / ".github" / "workflows" / "deploy-worker.yml").read_text(encoding="utf-8")
        manual_migrate = (ROOT / ".github" / "workflows" / "d1-migrate.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", deploy)
        self.assertIn("DEPLOY_CONTROL_WORKER", deploy)
        self.assertNotIn("  push:", deploy)
        self.assertIn("workflow_dispatch:", manual_migrate)
        self.assertIn("raawaa-autonomous-e", manual_migrate)
        self.assertNotIn("  push:", manual_migrate)
        self.assertFalse((ROOT / ".github" / "workflows" / "d1-auto-migrate.yml").exists())

    def test_migration_creates_append_only_tables_and_cas_head(self):
        migration = ROOT / "migrations" / "0004_paper_event_ledger.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "paper.db")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.executescript(migration.read_text(encoding="utf-8"))
            db.execute(
                "INSERT INTO paper_candidate_heads(candidate_id,sequence,event_hash,updated_at) VALUES(?,?,?,?)",
                ("candidate-1", 0, "0" * 64, "2026-11-02T00:00:00Z"),
            )
            changed = db.execute(
                "UPDATE paper_candidate_heads SET sequence=1,event_hash=? WHERE candidate_id=? AND sequence=0 AND event_hash=?",
                ("a" * 64, "candidate-1", "0" * 64),
            ).rowcount
            self.assertEqual(changed, 1)
            lost_race = db.execute(
                "UPDATE paper_candidate_heads SET sequence=1,event_hash=? WHERE candidate_id=? AND sequence=0 AND event_hash=?",
                ("b" * 64, "candidate-1", "0" * 64),
            ).rowcount
            self.assertEqual(lost_race, 0)
            db.execute("""
                INSERT INTO paper_events(
                    event_id,candidate_id,sequence,event_type,occurred_at,payload_json,
                    previous_hash,event_hash
                ) VALUES(?,?,?,?,?,?,?,?)
            """, ("event-1", "candidate-1", 1, "candidate_detected",
                  "2026-11-02T00:00:00+00:00", "{}", "0" * 64, "a" * 64))
            db.execute("""
                INSERT INTO paper_event_checkpoints(candidate_id,sequence,event_id,event_hash,created_at)
                VALUES(?,?,?,?,?)
            """, ("candidate-1", 1, "event-1", "a" * 64, "2026-11-02T00:00:00+00:00"))
            for statement in (
                "UPDATE paper_events SET event_type='expired' WHERE event_id='event-1'",
                "DELETE FROM paper_events WHERE event_id='event-1'",
                "UPDATE paper_event_checkpoints SET event_hash='b' WHERE event_id='event-1'",
                "DELETE FROM paper_event_checkpoints WHERE event_id='event-1'",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            db.close()


if __name__ == "__main__":
    unittest.main()
