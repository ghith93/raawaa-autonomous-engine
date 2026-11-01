import tempfile
import unittest
from pathlib import Path

from engine.operation_store import OperationStore


class OperationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OperationStore(Path(self.tmp.name) / "operations.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_is_idempotent_for_identical_intent(self):
        first = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        second = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "pending")

    def test_idempotency_key_cannot_be_reused_for_different_input(self):
        self.store.prepare("send_message", "msg-123", {"to": "customer"})
        with self.assertRaisesRegex(ValueError, "different operation input"):
            self.store.prepare("send_message", "msg-123", {"to": "other"})

    def test_only_one_claim_can_start_execution(self):
        operation = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        claim = self.store.claim(operation["id"])
        self.assertIsNotNone(claim)
        self.assertIsNone(self.store.claim(operation["id"]))
        self.assertEqual(self.store.get(operation["id"])["status"], "executing")

    def test_claim_token_fences_stale_completion(self):
        operation = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        claim = self.store.claim(operation["id"])
        with self.assertRaisesRegex(ValueError, "claim lost"):
            self.store.mark_succeeded(operation["id"], "stale-token", {"sent": True})
        self.store.mark_succeeded(operation["id"], claim["claim_token"],
                                  {"sent": True}, "provider-456")
        saved = self.store.get(operation["id"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["external_ref"], "provider-456")

    def test_ambiguous_outcome_blocks_replay_until_reconciled(self):
        operation = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        claim = self.store.claim(operation["id"])
        self.store.mark_unknown(operation["id"], claim["claim_token"])
        self.assertIsNone(self.store.claim(operation["id"]))
        with self.assertRaisesRegex(ValueError, "outcome must be"):
            self.store.reconcile(operation["id"], "invalid")
        self.store.reconcile(operation["id"], "not_performed")
        retry = self.store.claim(operation["id"])
        self.assertIsNotNone(retry)
        self.assertEqual(retry["status"], "executing")

    def test_reconcile_confirmed_success_never_replays(self):
        operation = self.store.prepare("send_message", "msg-123", {"to": "customer"})
        claim = self.store.claim(operation["id"])
        self.store.mark_unknown(operation["id"], claim["claim_token"])
        self.store.reconcile(operation["id"], "succeeded",
                             {"provider_status": "delivered"}, "provider-456")
        self.assertIsNone(self.store.claim(operation["id"]))
        self.assertEqual(self.store.get(operation["id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
