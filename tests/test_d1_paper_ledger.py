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
        self.assertEqual(payload["event"]["occurred_at"], "2026-11-02T00:00:00+00:00")

    @patch("urllib.request.urlopen")
    def test_transport_failure_fails_closed_without_local_fallback(self, open_url):
        from urllib.error import URLError
        open_url.side_effect = URLError("offline")
        with self.assertRaisesRegex(D1PaperLedgerError, "transport failed"):
            self.ledger.append_event(
                candidate_id="c", event_id="e", event_type="candidate_detected",
                occurred_at="2026-11-02T00:00:00Z", payload={},
            )

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
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE paper_candidate_heads SET sequence=2 WHERE candidate_id='candidate-1'")
            db.close()


if __name__ == "__main__":
    unittest.main()
