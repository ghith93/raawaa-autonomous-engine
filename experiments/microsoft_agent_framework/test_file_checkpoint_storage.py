"""Restart and atomic-write tests for the isolated authenticated file adapter."""
from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_framework._workflows._checkpoint import WorkflowCheckpoint
from agent_framework.exceptions import WorkflowCheckpointException

from experiments.microsoft_agent_framework.checkpoint_envelope import CheckpointEnvelopeError
from experiments.microsoft_agent_framework.file_checkpoint_storage import FileEnvelopeCheckpointStorage
from experiments.microsoft_agent_framework.framework_checkpoint_storage import FrameworkCheckpointStorageError


TEST_INTEGRITY_KEY = b"test-only-checkpoint-integrity-key-32-bytes"


class FileCheckpointStorageTests(unittest.TestCase):
    def test_fresh_instance_can_load_checkpoint_after_restart(self):
        async def scenario(directory: str):
            storage = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            checkpoint = WorkflowCheckpoint(
                workflow_name="restart-workflow",
                graph_signature_hash="graph-v1",
                checkpoint_id="restart-checkpoint",
                state={"count": 9, "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc)},
            )
            await storage.save(checkpoint)

            restarted = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            restored = await restarted.load("restart-checkpoint")
            self.assertEqual(restored.state["count"], 9)
            self.assertEqual(restored.state["created_at"], datetime(2026, 11, 1, tzinfo=timezone.utc))
            self.assertEqual(await restarted.list_checkpoint_ids(workflow_name="restart-workflow"),
                             ["restart-checkpoint"])
            self.assertEqual((await restarted.get_latest(workflow_name="restart-workflow")).checkpoint_id,
                             "restart-checkpoint")
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_atomic_write_uses_private_permissions_and_ignores_orphan_temp_files(self):
        async def scenario(directory: str):
            storage = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            checkpoint = WorkflowCheckpoint(
                workflow_name="permissions",
                graph_signature_hash="graph-v1",
                checkpoint_id="private-checkpoint",
            )
            await storage.save(checkpoint)
            files = list(Path(directory).glob("*.checkpoint"))
            self.assertEqual(len(files), 1)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            (Path(directory) / ".interrupted-write.tmp").write_bytes(b"partial")
            restarted = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            self.assertEqual((await restarted.load("private-checkpoint")).checkpoint_id,
                             "private-checkpoint")
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_wrong_key_and_corrupt_record_fail_closed_on_restart(self):
        async def scenario(directory: str):
            storage = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            await storage.save(WorkflowCheckpoint(
                workflow_name="tamper", graph_signature_hash="graph-v1", checkpoint_id="tamper-checkpoint"
            ))
            with self.assertRaises(CheckpointEnvelopeError):
                FileEnvelopeCheckpointStorage(directory, integrity_key=b"wrong-key-material-32-bytes-long")
            record = next(Path(directory).glob("*.checkpoint"))
            record.write_bytes(record.read_bytes() + b" ")
            with self.assertRaises(CheckpointEnvelopeError):
                FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


    def test_shared_directory_and_symlink_records_are_rejected(self):
        if os.name != "posix":
            self.skipTest("POSIX permission and symlink checks are platform-specific")
        with tempfile.TemporaryDirectory() as root:
            shared = Path(root) / "shared"
            shared.mkdir(mode=0o755)
            shared.chmod(0o755)
            with self.assertRaisesRegex(FrameworkCheckpointStorageError, "permissions"):
                FileEnvelopeCheckpointStorage(shared, integrity_key=TEST_INTEGRITY_KEY)

        with tempfile.TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            outside = Path(root) / "outside"
            outside.write_bytes(b"not a checkpoint")
            link = private / FileEnvelopeCheckpointStorage._filename("linked-checkpoint")
            link.symlink_to(outside)
            with self.assertRaisesRegex(FrameworkCheckpointStorageError, "symbolic links"):
                FileEnvelopeCheckpointStorage(private, integrity_key=TEST_INTEGRITY_KEY)

    def test_delete_is_persisted_across_restart(self):
        async def scenario(directory: str):
            storage = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            await storage.save(WorkflowCheckpoint(
                workflow_name="delete-workflow", graph_signature_hash="graph-v1", checkpoint_id="delete-checkpoint"
            ))
            self.assertTrue(await storage.delete("delete-checkpoint"))
            restarted = FileEnvelopeCheckpointStorage(directory, integrity_key=TEST_INTEGRITY_KEY)
            with self.assertRaises(WorkflowCheckpointException):
                await restarted.load("delete-checkpoint")
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))


if __name__ == "__main__":
    unittest.main()
