import hashlib
import unittest
from unittest.mock import patch

from engine.process_supervisor import DispatchLedgerError, SUPERVISOR_VERSION
from engine.remote_process_dispatch_ledger import RemoteProcessDispatchLedger, STEP_KEY
from engine.remote_task_store import RemoteTaskStore, RemoteTaskStoreError


TASK_ID = "task-remote-1"
LEASE = "lease-secret-value"
DISPATCH_ID = "a" * 32
ARGV_DIGEST = "b" * 64
NOW = "2026-11-05T00:00:00.000Z"


def fingerprint(operation="python_platform_probe", argv_digest=ARGV_DIGEST):
    material = {
        "operation": operation,
        "operation_version": SUPERVISOR_VERSION,
        "argv_digest": argv_digest,
    }
    encoded = __import__("json").dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def dispatch(status="prepared", *, operation="python_platform_probe", outcome=None,
             dispatch_id=DISPATCH_ID, op_fingerprint=None):
    return {
        "dispatch_id": dispatch_id,
        "task_id": TASK_ID,
        "step_key": STEP_KEY,
        "operation": operation,
        "operation_version": SUPERVISOR_VERSION,
        "operation_fingerprint": op_fingerprint or fingerprint(operation),
        "status": status,
        "outcome_version": 1 if outcome is not None else None,
        "outcome": outcome,
        "detail": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def settled_outcome():
    return {
        "classification": "succeeded", "exit_code": 0, "signal_number": None,
        "timed_out": False, "cancelled": False, "output_truncated": False,
        "stdout": "Linux", "stderr": "", "duration_seconds": 0.03,
        "descendants_killed": 0,
    }


class RemoteProcessDispatchLedgerTests(unittest.TestCase):
    def setUp(self):
        self.remote = RemoteTaskStore("https://engine.example", "test-token")
        self.ledger = RemoteProcessDispatchLedger(self.remote)

    def test_begin_durably_prepares_then_starts_before_returning_permission_to_spawn(self):
        responses = [
            {"dispatch": dispatch("prepared")},
            {"ok": True, "status": "in_flight"},
        ]
        with patch.object(self.remote, "_request", side_effect=responses) as call:
            result = self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token=LEASE,
            )
        self.assertIsNone(result)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].args[0], f"/v1/tasks/{TASK_ID}/process-dispatches")
        self.assertEqual(call.call_args_list[0].args[2]["action"], "prepare")
        self.assertEqual(call.call_args_list[0].args[2]["step_key"], STEP_KEY)
        self.assertEqual(call.call_args_list[0].args[2]["lease_token"], LEASE)
        self.assertEqual(call.call_args_list[1].args[2]["action"], "start")
        self.assertEqual(self.ledger.get(DISPATCH_ID).status, "in_flight")

    def test_recovered_in_flight_is_fenced_as_ambiguous_without_starting_again(self):
        with patch.object(self.remote, "_request", side_effect=[
            {"dispatch": dispatch("in_flight")}, {"ok": True, "status": "ambiguous"},
        ]) as call:
            record = self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token=LEASE,
            )
        self.assertEqual(record.status, "ambiguous")
        self.assertEqual(record.detail, "recovered_in_flight_dispatch")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].args[2]["action"], "prepare")
        self.assertEqual(call.call_args_list[1].args[2]["action"], "ambiguous")

    def test_settle_persists_versioned_bounded_result_with_active_lease(self):
        with patch.object(self.remote, "_request", side_effect=[
            {"dispatch": dispatch("prepared")}, {"ok": True, "status": "in_flight"},
            {"ok": True, "status": "settled", "replay": False},
        ]) as call:
            self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token=LEASE,
            )
            outcome = settled_outcome()
            self.ledger.settle(
                DISPATCH_ID, classification="succeeded", exit_code=0,
                output_truncated=False, result=outcome,
            )
        payload = call.call_args.args[2]
        self.assertEqual(payload["action"], "settle")
        self.assertEqual(payload["lease_token"], LEASE)
        self.assertEqual(payload["outcome"]["classification"], "succeeded")
        self.assertEqual(payload["outcome"]["exit_code"], 0)
        self.assertFalse(payload["outcome"]["timed_out"])
        self.assertFalse(payload["outcome"]["cancelled"])
        self.assertEqual(self.ledger.get(DISPATCH_ID).result["stdout"], "Linux")

    def test_output_is_byte_bounded_and_truncation_is_recorded(self):
        with patch.object(self.remote, "_request", side_effect=[
            {"dispatch": dispatch("prepared")}, {"ok": True, "status": "in_flight"},
            {"ok": True, "status": "settled", "replay": False},
        ]) as call:
            self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token=LEASE,
            )
            result = settled_outcome()
            result["stdout"] = "é" * 3000
            self.ledger.settle(
                DISPATCH_ID, classification="succeeded", exit_code=0,
                output_truncated=False, result=result,
            )
        stored = call.call_args.args[2]["outcome"]
        self.assertLessEqual(len(stored["stdout"].encode("utf-8")), 3500)
        self.assertTrue(stored["output_truncated"])

    def test_settled_replay_returns_saved_outcome_without_start(self):
        outcome = settled_outcome()
        with patch.object(self.remote, "_request", return_value={
            "dispatch": dispatch("settled", outcome=outcome),
        }) as call:
            record = self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token="new-lease",
            )
        self.assertEqual(record.status, "settled")
        self.assertEqual(record.classification, "succeeded")
        self.assertEqual(record.result["stdout"], "Linux")
        self.assertEqual(call.call_count, 1)

    def test_changed_operation_fingerprint_is_rejected(self):
        with patch.object(self.remote, "_request", return_value={
            "dispatch": dispatch("prepared", operation="git_head_revision"),
        }):
            with self.assertRaises(DispatchLedgerError):
                self.ledger.begin(
                    DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                    argv_digest=ARGV_DIGEST, lease_token=LEASE,
                )

    def test_lost_start_response_cannot_authorize_a_child_or_a_replay(self):
        with patch.object(self.remote, "_request", side_effect=[
            {"dispatch": dispatch("prepared")}, RemoteTaskStoreError("response lost"),
            {"dispatch": dispatch("in_flight")}, {"ok": True, "status": "ambiguous"},
        ]) as call:
            with self.assertRaises(RemoteTaskStoreError):
                self.ledger.begin(
                    DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                    argv_digest=ARGV_DIGEST, lease_token=LEASE,
                )
            replay = RemoteProcessDispatchLedger(self.remote).begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST, lease_token="next-lease",
            )
        self.assertEqual(replay.status, "ambiguous")
        self.assertEqual(call.call_count, 4)

    def test_remote_reconciliation_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(DispatchLedgerError, "reconciliation is unsupported"):
            self.ledger.reconcile()

    def test_settled_record_rejects_malformed_signal_metadata(self):
        outcome = settled_outcome()
        outcome["signal_number"] = 999
        with patch.object(self.remote, "_request", return_value={
            "dispatch": dispatch("settled", outcome=outcome),
        }):
            with self.assertRaisesRegex(RemoteTaskStoreError, "exit status"):
                self.ledger.begin(
                    DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                    argv_digest=ARGV_DIGEST, lease_token=LEASE,
                )

    def test_task_id_is_validated_before_constructing_remote_path(self):
        with patch.object(self.remote, "_request") as call:
            with self.assertRaisesRegex(ValueError, "task_id is invalid"):
                self.ledger._post("../other", {"action": "prepare"})
        call.assert_not_called()

    def test_remote_ledger_rejects_non_remote_store_and_missing_lease(self):
        with self.assertRaises(TypeError):
            RemoteProcessDispatchLedger(object())
        with self.assertRaises(DispatchLedgerError):
            self.ledger.begin(
                DISPATCH_ID, task_id=TASK_ID, operation="python_platform_probe",
                argv_digest=ARGV_DIGEST,
            )


if __name__ == "__main__":
    unittest.main()
