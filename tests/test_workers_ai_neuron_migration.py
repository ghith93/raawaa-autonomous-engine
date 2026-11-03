from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class WorkersAiNeuronMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            self.db.executescript(path.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def test_policy_is_disabled_and_hard_capped_at_ten_thousand_neurons(self):
        row = self.db.execute(
            "SELECT daily_cap_neurons,allowed_model_id,enabled "
            "FROM workers_ai_neuron_policy WHERE policy_key='default'"
        ).fetchone()
        self.assertEqual(row, (10000, "@cf/google/gemma-4-26b-a4b-it", 0))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "UPDATE workers_ai_neuron_policy SET daily_cap_neurons=10001 WHERE policy_key='default'"
            )

    def test_model_task_and_public_data_constraints_reject_other_workloads(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO workers_ai_neuron_reservations(
                    reservation_id,idempotency_key,model_id,task_kind,task_id,
                    data_classification,status,estimated_neurons,day_utc,created_at
                ) VALUES('r1','i1','@cf/nvidia/other','research_brief','t1',
                    'public','reserved',100,'2026-11-03','now')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO workers_ai_neuron_reservations(
                    reservation_id,idempotency_key,model_id,task_kind,task_id,
                    data_classification,status,estimated_neurons,day_utc,created_at
                ) VALUES('r2','i2','@cf/google/gemma-4-26b-a4b-it','customer_action','t2',
                    'public','reserved',100,'2026-11-03','now')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO workers_ai_neuron_reservations(
                    reservation_id,idempotency_key,model_id,task_kind,task_id,
                    data_classification,status,estimated_neurons,day_utc,created_at
                ) VALUES('r3','i3','@cf/google/gemma-4-26b-a4b-it','research_brief','t3',
                    'confidential','reserved',100,'2026-11-03','now')"""
            )

    def test_idempotency_and_reservation_states_are_constrained(self):
        values = (
            "r1","i1","@cf/google/gemma-4-26b-a4b-it","research_brief","task-1",
            "public","reserved",100,None,"2026-11-03","now",
        )
        sql = """INSERT INTO workers_ai_neuron_reservations(
            reservation_id,idempotency_key,model_id,task_kind,task_id,
            data_classification,status,estimated_neurons,actual_neurons,day_utc,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)"""
        self.db.execute(sql, values)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ("r2","i1",*values[2:]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ("r3","i3",*values[2:6],"unknown",100,None,"2026-11-03","now"))

    def test_migration_is_idempotent_and_policy_remains_disabled(self):
        migration = (MIGRATIONS / "0008_workers_ai_neuron_ledger.sql").read_text(encoding="utf-8")
        self.db.executescript(migration)
        self.db.executescript(migration)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version='0008_workers_ai_neuron_ledger'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.execute("SELECT enabled FROM workers_ai_neuron_policy WHERE policy_key='default'").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
