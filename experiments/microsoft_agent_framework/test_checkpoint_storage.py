"""Isolated tests for the pinned Microsoft Agent Framework checkpoint codec adapter."""
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone

from agent_framework._workflows._checkpoint import WorkflowCheckpoint

from experiments.microsoft_agent_framework.framework_checkpoint_storage import (
    FrameworkCheckpointStorageError,
    InMemoryEnvelopeCheckpointStorage,
)
from experiments.microsoft_agent_framework.checkpoint_envelope import (
    decode_authenticated_checkpoint_envelope,
    encode_authenticated_checkpoint_envelope,
)


TEST_INTEGRITY_KEY = b"test-only-checkpoint-integrity-key-32-bytes"


class ApplicationOnlyState:
    def __init__(self, value: str) -> None:
        self.value = value


class FrameworkCheckpointStorageTests(unittest.TestCase):
    def test_framework_codec_round_trip_and_checkpoint_storage_protocol(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            first = WorkflowCheckpoint(
                workflow_name="parallel-research",
                graph_signature_hash="graph-v1",
                checkpoint_id="checkpoint-1",
                timestamp="2026-11-01T10:00:00+00:00",
                iteration_count=7,
                state={"counter": 3, "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc)},
            )
            second = WorkflowCheckpoint(
                workflow_name="parallel-research",
                graph_signature_hash="graph-v1",
                checkpoint_id="checkpoint-2",
                previous_checkpoint_id="checkpoint-1",
                timestamp="2026-11-01T10:00:00+00:00",
                iteration_count=7,
                state={"counter": 4},
            )
            await storage.save(first)
            await storage.save(second)

            loaded = await storage.load("checkpoint-1")
            self.assertEqual(loaded.state["counter"], 3)
            self.assertEqual(loaded.state["created_at"], datetime(2026, 11, 1, tzinfo=timezone.utc))
            self.assertEqual(await storage.list_checkpoint_ids(workflow_name="parallel-research"),
                             ["checkpoint-1", "checkpoint-2"])
            self.assertEqual([cp.checkpoint_id for cp in await storage.list_checkpoints(
                workflow_name="parallel-research")], ["checkpoint-1", "checkpoint-2"])
            latest = await storage.get_latest(workflow_name="parallel-research")
            self.assertEqual(latest.checkpoint_id, "checkpoint-2")
            self.assertTrue(await storage.delete("checkpoint-1"))
            self.assertFalse(await storage.delete("checkpoint-1"))
            self.assertEqual(await storage.load("checkpoint-2"), second)
        asyncio.run(scenario())

    def test_checkpoint_codec_rejects_unapproved_application_types(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            checkpoint = WorkflowCheckpoint(
                workflow_name="custom-state",
                graph_signature_hash="graph-v1",
                checkpoint_id="custom-checkpoint",
                state={"custom": ApplicationOnlyState("not-allowlisted")},
            )
            with self.assertRaises(FrameworkCheckpointStorageError):
                await storage.save(checkpoint)
        asyncio.run(scenario())

    def test_duplicate_checkpoint_id_cannot_change_content(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            first = WorkflowCheckpoint(
                workflow_name="duplicate-test",
                graph_signature_hash="graph-v1",
                checkpoint_id="same-id",
                state={"value": 1},
            )
            await storage.save(first)
            changed = WorkflowCheckpoint(
                workflow_name="duplicate-test",
                graph_signature_hash="graph-v1",
                checkpoint_id="same-id",
                state={"value": 2},
            )
            with self.assertRaises(FrameworkCheckpointStorageError):
                await storage.save(changed)
        asyncio.run(scenario())

    def test_tampered_record_fails_before_framework_deserialization(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            checkpoint = WorkflowCheckpoint(
                workflow_name="tamper-test",
                graph_signature_hash="graph-v1",
                checkpoint_id="tamper-checkpoint",
                state={"value": 1},
            )
            await storage.save(checkpoint)
            envelope = json.loads(storage._records["tamper-checkpoint"])
            envelope["payload"]["workflow_name"] = "other-workflow"
            storage._records["tamper-checkpoint"] = json.dumps(
                envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            with self.assertRaisesRegex(ValueError, "authentication"):
                await storage.load("tamper-checkpoint")
        asyncio.run(scenario())


    def test_recomputed_envelope_cannot_change_graph_signature(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            checkpoint = WorkflowCheckpoint(
                workflow_name="graph-test",
                graph_signature_hash="graph-v1",
                checkpoint_id="graph-checkpoint",
                state={"value": 1},
            )
            await storage.save(checkpoint)
            envelope = decode_authenticated_checkpoint_envelope(
                storage._records["graph-checkpoint"], integrity_key=TEST_INTEGRITY_KEY
            )
            payload = dict(envelope["payload"])
            payload["graph_signature_hash"] = "forged-graph"
            storage._records["graph-checkpoint"] = encode_authenticated_checkpoint_envelope(
                integrity_key=TEST_INTEGRITY_KEY,
                workflow_id=envelope["workflow_id"],
                checkpoint_id=envelope["checkpoint_id"],
                parent_checkpoint_id=envelope["parent_checkpoint_id"],
                sequence=envelope["sequence"],
                payload=payload,
            )
            with self.assertRaisesRegex(FrameworkCheckpointStorageError, "graph signature"):
                await storage.load("graph-checkpoint")
        asyncio.run(scenario())

    def test_workflow_names_are_isolated(self):
        async def scenario():
            storage = InMemoryEnvelopeCheckpointStorage(integrity_key=TEST_INTEGRITY_KEY)
            await storage.save(WorkflowCheckpoint(
                workflow_name="workflow-a", graph_signature_hash="a", checkpoint_id="cp-a"
            ))
            self.assertEqual(await storage.list_checkpoint_ids(workflow_name="workflow-b"), [])
            self.assertIsNone(await storage.get_latest(workflow_name="workflow-b"))
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
