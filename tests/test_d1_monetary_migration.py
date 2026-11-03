from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))


class DurableMonetaryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        for migration in MIGRATIONS:
            self.db.executescript(migration.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def test_default_budget_policy_matches_approved_usd_caps(self):
        row = self.db.execute(
            "SELECT daily_cap_nanos,monthly_cap_nanos,per_task_cap_nanos,enabled FROM monetary_budget_policies WHERE policy_key='default'"
        ).fetchone()
        self.assertEqual(row, (400000000, 12000000000, 200000000, 1))

    def test_reservation_state_machine_and_provider_io_release_guard(self):
        self.db.execute(
            "INSERT INTO monetary_reservations(reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,estimated_cost_nanos,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'reserved',?,?)",
            ("reserve-1", "default", "provider", "model", "research", "task-1", "2026-11-03", "2026-11", 50000000, "now", "now"),
        )
        self.db.execute("UPDATE monetary_reservations SET status='in_flight',provider_io_started_at='now',updated_at='now' WHERE reservation_id='reserve-1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE monetary_reservations SET status='released',released_at='now',updated_at='now' WHERE reservation_id='reserve-1'")
        self.db.execute("UPDATE monetary_reservations SET status='ambiguous',ambiguous_at='now',ambiguous_reason='provider_timeout',updated_at='now' WHERE reservation_id='reserve-1'")
        self.db.execute("UPDATE monetary_reservations SET status='settled',actual_cost_nanos=25000000,input_tokens=10,output_tokens=5,cost_basis='provider_reported',settled_at='now',updated_at='now' WHERE reservation_id='reserve-1'")
        row = self.db.execute("SELECT status,actual_cost_nanos,input_tokens,output_tokens FROM monetary_reservations WHERE reservation_id='reserve-1'").fetchone()
        self.assertEqual(row, ("settled", 25000000, 10, 5))

    def test_negative_cost_invalid_status_and_unknown_policy_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO monetary_reservations(reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,estimated_cost_nanos,status,created_at,updated_at) VALUES('bad','default','p','m','k','t','2026-11-03','2026-11',-1,'reserved','now','now')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO monetary_reservations(reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,estimated_cost_nanos,status,created_at,updated_at) VALUES('bad-status','default','p','m','k','t','2026-11-03','2026-11',1,'unknown','now','now')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO monetary_reservations(reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,estimated_cost_nanos,status,created_at,updated_at) VALUES('bad-policy','missing','p','m','k','t','2026-11-03','2026-11',1,'reserved','now','now')"
            )

    def test_ambiguous_and_pending_reservations_remain_budgeted(self):
        self.db.execute(
            "INSERT INTO monetary_reservations(reservation_id,policy_key,provider,model,task_kind,task_id,utc_day,utc_month,estimated_cost_nanos,status,created_at,updated_at) VALUES('ambiguous-1','default','p','m','k','task-2','2026-11-03','2026-11',75000000,'reserved','now','now')"
        )
        self.db.execute("UPDATE monetary_reservations SET status='in_flight',provider_io_started_at='now' WHERE reservation_id='ambiguous-1'")
        self.db.execute("UPDATE monetary_reservations SET status='ambiguous',ambiguous_at='now',ambiguous_reason='provider_timeout' WHERE reservation_id='ambiguous-1'")
        pending = self.db.execute("SELECT SUM(estimated_cost_nanos) FROM monetary_reservations WHERE policy_key='default' AND status IN ('reserved','in_flight','ambiguous')").fetchone()[0]
        self.assertEqual(pending, 75000000)


if __name__ == "__main__":
    unittest.main()
