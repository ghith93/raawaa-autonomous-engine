"""Authenticated local-file checkpoint adapter for isolated restart tests only."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import uuid
from pathlib import Path

from agent_framework._workflows._checkpoint import WorkflowCheckpoint

from .checkpoint_envelope import (
    MAX_ENVELOPE_BYTES,
    decode_authenticated_checkpoint_envelope,
)
from .framework_checkpoint_storage import (
    FrameworkCheckpointStorageError,
    InMemoryEnvelopeCheckpointStorage,
)


class FileEnvelopeCheckpointStorage(InMemoryEnvelopeCheckpointStorage):
    """Persist authenticated checkpoint records in a private local test directory.

    This adapter is a restart/recovery fixture, not a production storage backend.
    The directory and HMAC key must be trusted. Framework checkpoint values still
    use the pinned framework's pickle-bearing codec.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        integrity_key: bytes,
        allowed_checkpoint_types: tuple[str, ...] = (),
    ) -> None:
        super().__init__(integrity_key=integrity_key, allowed_checkpoint_types=allowed_checkpoint_types)
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix" and stat.S_IMODE(self.directory.stat().st_mode) & 0o077:
            raise FrameworkCheckpointStorageError("checkpoint directory permissions must be private (0700)")
        self._file_lock = asyncio.Lock()
        self._load_existing_records()

    @staticmethod
    def _filename(checkpoint_id: str) -> str:
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise FrameworkCheckpointStorageError("checkpoint_id is required")
        return hashlib.sha256(checkpoint_id.encode("utf-8")).hexdigest() + ".checkpoint"

    def _load_existing_records(self) -> None:
        for path in sorted(self.directory.glob("*.checkpoint")):
            if path.is_symlink():
                raise FrameworkCheckpointStorageError("checkpoint records must not be symbolic links")
            file_stat = path.stat()
            if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise FrameworkCheckpointStorageError("checkpoint file permissions must be private (0600)")
            size = file_stat.st_size
            if size <= 0 or size > MAX_ENVELOPE_BYTES:
                raise FrameworkCheckpointStorageError("checkpoint file is empty or exceeds the size limit")
            encoded = path.read_bytes()
            envelope = decode_authenticated_checkpoint_envelope(
                encoded, integrity_key=self._integrity_key
            )
            checkpoint_id = envelope["checkpoint_id"]
            if path.name != self._filename(checkpoint_id):
                raise FrameworkCheckpointStorageError("checkpoint filename does not match its authenticated ID")
            if checkpoint_id in self._records:
                raise FrameworkCheckpointStorageError("duplicate checkpoint ID in local storage")
            self._records[checkpoint_id] = encoded

    @staticmethod
    def _atomic_write(path: Path, encoded: bytes) -> None:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        if not isinstance(checkpoint, WorkflowCheckpoint):
            raise TypeError("checkpoint must be a WorkflowCheckpoint")
        async with self._file_lock:
            previous = self._records.get(checkpoint.checkpoint_id)
            checkpoint_id = await super().save(checkpoint)
            encoded = self._records[checkpoint_id]
            path = self.directory / self._filename(checkpoint_id)
            try:
                await asyncio.to_thread(self._atomic_write, path, encoded)
            except Exception as exc:
                if previous is None:
                    await super().delete(checkpoint_id)
                else:
                    self._records[checkpoint_id] = previous
                raise FrameworkCheckpointStorageError("checkpoint file could not be committed atomically") from exc
            return checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        async with self._file_lock:
            return await super().load(checkpoint_id)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        async with self._file_lock:
            return await super().list_checkpoints(workflow_name=workflow_name)

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        async with self._file_lock:
            return await super().list_checkpoint_ids(workflow_name=workflow_name)

    async def delete(self, checkpoint_id: str) -> bool:
        async with self._file_lock:
            path = self.directory / self._filename(checkpoint_id)
            try:
                await asyncio.to_thread(path.unlink)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FrameworkCheckpointStorageError("checkpoint file could not be deleted") from exc
            return await super().delete(checkpoint_id)
