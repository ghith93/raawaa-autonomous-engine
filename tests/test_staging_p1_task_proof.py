from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts import run_staging_p1_task_proof as proof


def _env(**overrides):
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "42079999999",
        "GITHUB_RUN_ATTEMPT": "1",
        "RAAWAA_ENGINE_API_URL": proof.STAGING_API_URL,
        "RAAWAA_ENGINE_PROCESS_SUPERVISOR": "enabled",
        "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
        "RAAWAA_ENGINE_STAGING_TOKEN_INSTALLED": "true",
        "RAAWAA_ENGINE_API_TOKEN": "t" * 40,
    }
    env.update(overrides)
    return env


class FakeStore:
    """Minimal exact-task store: enqueue -> claim -> succeed -> read back."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.status = "pending"
        self.claims = 0
        self.enqueued = None

    def enqueue(self, task_type, title, payload, **kwargs):
        self.enqueued = (task_type, title, payload, kwargs)
        return {
            "id": "p1-task-1",
            "task_type": task_type,
            "title": title,
            "idempotency_key": kwargs["idempotency_key"],
            "payload_json": json.dumps({"operation": proof.OPERATION}),
            "status": self.status,
        }

    def claim_supervised_probe(self, task_id):
        self.claims += 1
        if self.status != "pending":
            return None
        self.status = "running"
        return {
            "id": task_id,
            "task_type": proof.TASK_TYPE,
            "status": "running",
            "execution_mode": "dry_run",
            "payload_json": json.dumps({"operation": proof.OPERATION}),
            "lease_token": "lease-1",
        }

    def list_tasks(self, limit=50):
        return [{
            "id": "p1-task-1",
            "task_type": proof.TASK_TYPE,
            "status": self.status,
            "payload_json": json.dumps({"operation": proof.OPERATION}),
        }]


def _processor(store, claimed, **kwargs):
    store.status = "succeeded"
    return {
        "id": claimed["id"],
        "status": "succeeded",
        "classification": "succeeded",
        "operation": proof.OPERATION,
        "dispatch_id": "dispatch-1",
        "replayed": False,
    }


class EnvironmentValidationTests(unittest.TestCase):
    def test_happy_environment_yields_run_scoped_key(self):
        token, key = proof.validate_environment(_env())
        self.assertEqual(token, "t" * 40)
        self.assertEqual(key, "staging-p1-proof:42079999999:1")

    def test_schedule_event_is_refused(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(GITHUB_EVENT_NAME="schedule"))

    def test_non_main_ref_is_refused(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(GITHUB_REF="refs/heads/topic"))

    def test_production_url_is_refused(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(
                RAAWAA_ENGINE_API_URL="https://raawaa-autonomous-engine-control.example.workers.dev"))

    def test_missing_token_install_is_refused(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(RAAWAA_ENGINE_STAGING_TOKEN_INSTALLED="false"))

    def test_provider_credential_is_refused(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(GROQ_API_KEY="x"))

    def test_live_inference_must_be_disabled(self):
        with self.assertRaises(proof.P1ProofError):
            proof.validate_environment(_env(RAAWAA_ENGINE_LIVE_INFERENCE="enabled"))


class RunProofTests(unittest.TestCase):
    def _run(self, store, processor=_processor, env=None):
        return proof.run_p1_proof(
            env or _env(),
            store_factory=lambda *a, **k: store,
            processor=processor,
        )

    def test_full_lifecycle_with_readback_and_duplicate_claim_blocked(self):
        store = FakeStore()
        result = self._run(store)
        self.assertEqual(result["task_id"], "p1-task-1")
        self.assertEqual(result["dispatch_id"], "dispatch-1")
        self.assertEqual(result["readback_status"], "succeeded")
        self.assertTrue(result["duplicate_claim_blocked"])
        self.assertFalse(result["counts_toward_schedule_acceptance"])
        self.assertEqual(result["provider_calls"], 0)
        # One claim to execute plus one post-settlement claim that must refuse.
        self.assertEqual(store.claims, 2)
        task_type, title, payload, kwargs = store.enqueued
        self.assertEqual(task_type, proof.TASK_TYPE)
        self.assertEqual(payload, {"operation": proof.OPERATION})
        self.assertEqual(kwargs["max_attempts"], 1)

    def test_non_pending_enqueue_is_not_a_fresh_proof(self):
        store = FakeStore()
        store.status = "succeeded"
        with self.assertRaises(proof.P1ProofError):
            self._run(store)

    def test_replayed_processor_result_is_refused(self):
        def replaying_processor(store, claimed, **kwargs):
            result = _processor(store, claimed, **kwargs)
            result["replayed"] = True
            return result

        with self.assertRaises(proof.P1ProofError):
            self._run(FakeStore(), processor=replaying_processor)

    def test_unreadable_terminal_row_is_refused(self):
        class VanishingStore(FakeStore):
            def list_tasks(self, limit=50):
                return []

        with self.assertRaises(proof.P1ProofError):
            self._run(VanishingStore())

    def test_duplicate_claim_after_settlement_is_refused(self):
        class GreedyStore(FakeStore):
            def claim_supervised_probe(self, task_id):
                claimed = super().claim_supervised_probe(task_id)
                if claimed is None and self.status == "succeeded":
                    return {"id": task_id}  # a second lease must never appear
                return claimed

        with self.assertRaises(proof.P1ProofError):
            self._run(GreedyStore())


class StepSummaryTests(unittest.TestCase):
    def test_failure_is_mirrored_to_step_summary(self):
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}, clear=True):
                self.assertEqual(proof.main(), 1)
            self.assertIn("Staging P1 task proof FAILED", summary.read_text(encoding="utf-8"))


class WorkflowShapeTests(unittest.TestCase):
    def test_workflow_is_manual_only_and_revokes_token(self):
        text = Path(".github/workflows/staging-p1-task-proof.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("cron:", text)
        self.assertIn("scripts/run_staging_p1_task_proof.py", text)
        self.assertIn("secret put ENGINE_API_TOKEN --config wrangler.staging.toml", text)
        self.assertIn("secret delete ENGINE_API_TOKEN --config wrangler.staging.toml", text)
        self.assertIn("NEVER counts toward the Runtime-004", text)


if __name__ == "__main__":
    unittest.main()
