"""Pinned-version, in-memory adapter experiment for Microsoft Agent Framework checkpoints.

This adapter is intentionally ephemeral. It exercises the framework's own checkpoint
codec and the local bounded envelope, but it is not a D1 adapter and must never be
used with untrusted storage because the framework codec contains pickle payloads.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from agent_framework._workflows._checkpoint import WorkflowCheckpoint
from agent_framework._workflows._checkpoint_encoding import (
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from agent_framework.exceptions import WorkflowCheckpointException

from .checkpoint_envelope import (
    CheckpointEnvelopeError,
    decode_authenticated_checkpoint_envelope,
    encode_authenticated_checkpoint_envelope,
)


class FrameworkCheckpointStorageError(ValueError):
    """Raised when the isolated framework checkpoint adapter rejects a record."""


class InMemoryEnvelopeCheckpointStorage:
    """Exercise CheckpointStorage semantics without a database or external writes.

    Uses the pinned framework's internal codec APIs deliberately and only inside
    this isolated experiment. allowed_checkpoint_types is passed to the restricted
    decoder; add application types only when their source and reconstruction behavior
    have been reviewed.
    """

    def __init__(self, integrity_key: bytes, allowed_checkpoint_types: tuple[str, ...] = ()) -> None:
        if not isinstance(integrity_key, bytes) or len(integrity_key) < 32:
            raise ValueError("integrity_key must contain at least 32 secret bytes")
        if not isinstance(allowed_checkpoint_types, tuple) or any(
            not isinstance(item, str) or not item for item in allowed_checkpoint_types
        ):
            raise ValueError("allowed_checkpoint_types must be a tuple of non-empty type keys")
        self._integrity_key = integrity_key
        self._allowed_types = frozenset(allowed_checkpoint_types)
        self._records: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _workflow_key(workflow_name: str) -> str:
        if not isinstance(workflow_name, str) or not workflow_name:
            raise FrameworkCheckpointStorageError("workflow_name is required")
        return hashlib.sha256(workflow_name.encode("utf-8")).hexdigest()

    def _decode_record(self, checkpoint_id: str, encoded: bytes) -> tuple[WorkflowCheckpoint, int]:
        try:
            envelope = decode_authenticated_checkpoint_envelope(encoded, integrity_key=self._integrity_key)
            payload = envelope["payload"]
            workflow_name = payload["workflow_name"]
            if envelope["checkpoint_id"] != checkpoint_id:
                raise FrameworkCheckpointStorageError("checkpoint identifier does not match its envelope")
            if envelope["workflow_id"] != self._workflow_key(workflow_name):
                raise FrameworkCheckpointStorageError("workflow identifier does not match its envelope")
            checkpoint_data = decode_checkpoint_value(
                payload["framework_checkpoint"], allowed_types=self._allowed_types
            )
            if not isinstance(checkpoint_data, dict):
                raise FrameworkCheckpointStorageError("framework checkpoint codec returned a non-object")
            checkpoint = WorkflowCheckpoint.from_dict(checkpoint_data)
            if checkpoint.checkpoint_id != checkpoint_id or checkpoint.workflow_name != workflow_name:
                raise FrameworkCheckpointStorageError("decoded checkpoint identity does not match its envelope")
            if checkpoint.graph_signature_hash != payload.get("graph_signature_hash"):
                raise FrameworkCheckpointStorageError("workflow graph signature does not match its envelope")
            if checkpoint.previous_checkpoint_id != envelope["parent_checkpoint_id"]:
                raise FrameworkCheckpointStorageError("checkpoint lineage does not match its envelope")
            return checkpoint, envelope["sequence"]
        except (CheckpointEnvelopeError, WorkflowCheckpointException, FrameworkCheckpointStorageError):
            raise
        except Exception as exc:
            raise FrameworkCheckpointStorageError("framework checkpoint record could not be decoded") from exc

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        if not isinstance(checkpoint, WorkflowCheckpoint):
            raise TypeError("checkpoint must be a WorkflowCheckpoint")
        try:
            # Use the framework's own encoder, then prove the result is restorable
            # under the exact restricted type allowlist before storing it.
            framework_payload = encode_checkpoint_value(checkpoint.to_dict())
            restored = decode_checkpoint_value(framework_payload, allowed_types=self._allowed_types)
            if (
                not isinstance(restored, dict)
                or restored.get("checkpoint_id") != checkpoint.checkpoint_id
                or restored.get("workflow_name") != checkpoint.workflow_name
                or restored.get("graph_signature_hash") != checkpoint.graph_signature_hash
                or restored.get("previous_checkpoint_id") != checkpoint.previous_checkpoint_id
            ):
                raise FrameworkCheckpointStorageError("framework codec round-trip changed checkpoint identity or lineage")
        except FrameworkCheckpointStorageError:
            raise
        except Exception as exc:
            raise FrameworkCheckpointStorageError("framework checkpoint cannot be safely encoded and restored") from exc

        workflow_id = self._workflow_key(checkpoint.workflow_name)
        async with self._lock:
            existing = self._records.get(checkpoint.checkpoint_id)
            if existing is not None:
                prior = decode_authenticated_checkpoint_envelope(existing, integrity_key=self._integrity_key)
                if prior["payload"].get("framework_checkpoint") != framework_payload:
                    raise FrameworkCheckpointStorageError("checkpoint ID already exists with different content")
                return checkpoint.checkpoint_id

            sequence = 1
            for record in self._records.values():
                envelope = decode_authenticated_checkpoint_envelope(record, integrity_key=self._integrity_key)
                if envelope["workflow_id"] == workflow_id:
                    sequence = max(sequence, envelope["sequence"] + 1)

            payload: dict[str, Any] = {
                "workflow_name": checkpoint.workflow_name,
                "graph_signature_hash": checkpoint.graph_signature_hash,
                "framework_checkpoint": framework_payload,
            }
            self._records[checkpoint.checkpoint_id] = encode_authenticated_checkpoint_envelope(
                integrity_key=self._integrity_key,
                workflow_id=workflow_id,
                checkpoint_id=checkpoint.checkpoint_id,
                parent_checkpoint_id=checkpoint.previous_checkpoint_id,
                sequence=sequence,
                payload=payload,
            )
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        async with self._lock:
            encoded = self._records.get(checkpoint_id)
        if encoded is None:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        checkpoint, _ = self._decode_record(checkpoint_id, encoded)
        return checkpoint

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        workflow_id = self._workflow_key(workflow_name)
        async with self._lock:
            records = list(self._records.items())
        selected = []
        for checkpoint_id, encoded in records:
            envelope = decode_authenticated_checkpoint_envelope(encoded, integrity_key=self._integrity_key)
            if envelope["workflow_id"] == workflow_id:
                checkpoint, sequence = self._decode_record(checkpoint_id, encoded)
                selected.append((sequence, checkpoint))
        return [checkpoint for _, checkpoint in sorted(selected, key=lambda item: item[0])]

    async def delete(self, checkpoint_id: str) -> bool:
        async with self._lock:
            return self._records.pop(checkpoint_id, None) is not None

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        workflow_id = self._workflow_key(workflow_name)
        async with self._lock:
            records = list(self._records.items())
        selected = []
        for checkpoint_id, encoded in records:
            envelope = decode_authenticated_checkpoint_envelope(encoded, integrity_key=self._integrity_key)
            if envelope["workflow_id"] == workflow_id:
                checkpoint, sequence = self._decode_record(checkpoint_id, encoded)
                selected.append((sequence, checkpoint))
        if not selected:
            return None
        return max(selected, key=lambda item: item[0])[1]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        workflow_id = self._workflow_key(workflow_name)
        async with self._lock:
            records = list(self._records.items())
        selected = []
        for checkpoint_id, encoded in records:
            envelope = decode_authenticated_checkpoint_envelope(encoded, integrity_key=self._integrity_key)
            if envelope["workflow_id"] == workflow_id:
                _, sequence = self._decode_record(checkpoint_id, encoded)
                selected.append((sequence, checkpoint_id))
        return [checkpoint_id for _, checkpoint_id in sorted(selected)]
