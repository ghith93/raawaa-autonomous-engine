"""Local SQLite concurrency tests only; no remote D1 or model inference."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from engine.research_pilot_local import PilotContractError, admit_local, claim_local
from engine.task_store import TaskStore

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'config/policy.json').read_text())['live_inference_data_policy']
PAYLOAD = {'data_classification': 'public', 'topic': 'Public research topics'}
ARGS = dict(key='research-pilot:test', title='Public research', payload=PAYLOAD, policy=POLICY)


class LocalPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TaskStore(Path(self.tmp.name) / 'queue.sqlite')

    def test_concurrent_admission_replays_one_identity_and_rejects_conflict(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: admit_local(self.store, **ARGS), range(8)))
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        with self.assertRaisesRegex(PilotContractError, 'identity conflict'):
            admit_local(self.store, **dict(ARGS, title='different'))
        with self.assertRaisesRegex(PilotContractError, 'identity conflict'):
            admit_local(self.store, **dict(ARGS, payload={'data_classification': 'public', 'topic': 'different'}))
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_concurrent_claim_only_target_wins_and_other_task_unchanged(self):
        unrelated = self.store.enqueue('system_audit', 'Unrelated')
        task_id, _ = admit_local(self.store, **ARGS)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim_local(self.store, task_id=task_id, **ARGS), range(8)))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        rows = {row['id']: row for row in self.store.list_tasks()}
        self.assertEqual(rows[unrelated['id']]['status'], 'pending')
        self.assertEqual(rows[unrelated['id']]['attempts'], 0)
        self.assertEqual(rows[task_id]['execution_mode'], 'dry_run')
        self.assertEqual(rows[task_id]['attempts'], 1)
        self.assertIsNone(claim_local(self.store, task_id=unrelated['id'], **ARGS))

    def test_stale_lease_cannot_finish_and_recovery_never_requeues(self):
        task_id, _ = admit_local(self.store, **ARGS)
        lease = claim_local(self.store, task_id=task_id, **ARGS)
        with self.store._connect() as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, 'lease expired'):
            self.store.finish(task_id, 'succeeded', result={'mode': 'dry_run'}, lease_token=lease['lease_token'])
        self.assertEqual(self.store.recover_stale(), {'requeued': 0, 'failed': 1})
        self.assertIsNone(claim_local(self.store, task_id=task_id, **ARGS))

    def test_payload_and_policy_rejected_without_echo_or_write(self):
        secret = 'gsk_' + 'X' * 30
        for overrides in ({'policy': {}}, {'payload': {'data_classification': 'public', 'topic': secret}},
                          {'key': 'bad\n' + secret}):
            with self.subTest(overrides=list(overrides)):
                with self.assertRaises(PilotContractError) as caught:
                    admit_local(self.store, **dict(ARGS, **overrides))
                self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(self.store.list_tasks(), [])
        self.assertNotIn(secret, json.dumps(self.store.audit_events()))

    def test_wrong_claim_identity_and_bad_lease_cannot_mutate(self):
        task_id, _ = admit_local(self.store, **ARGS)
        with self.assertRaises(PilotContractError):
            claim_local(self.store, task_id=task_id, **dict(ARGS, title='different'))
        self.assertIsNone(claim_local(self.store, task_id=task_id, **dict(ARGS, key='research-pilot:other')))
        lease = claim_local(self.store, task_id=task_id, **ARGS)
        with self.assertRaisesRegex(ValueError, 'lease ownership lost'):
            self.store.finish(task_id, 'succeeded', lease_token='wrong')
        self.store.finish(task_id, 'blocked', lease_token=lease['lease_token'])
        self.assertIsNone(claim_local(self.store, task_id=task_id, **ARGS))


if __name__ == '__main__':
    unittest.main()
