"""Bounded, integrity-checked JSON envelope for isolated checkpoint experiments.

This is a transport contract only. It does not serialize Microsoft Agent Framework
objects or make D1 persistence production-ready. Callers must first obtain a
JSON-safe payload through a framework-supported, trusted codec. Pickle and other
executable object serialization are deliberately rejected.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from typing import Any

SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 256 * 1024
MAX_PAYLOAD_DEPTH = 64
MAX_PAYLOAD_ITEMS = 20000
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_KEYS = {
    "schema_version", "workflow_id", "checkpoint_id",
    "parent_checkpoint_id", "sequence", "payload", "sha256",
}
_UNSIGNED_KEYS = _REQUIRED_KEYS - {"sha256"}


class CheckpointEnvelopeError(ValueError):
    """Raised when a checkpoint envelope is invalid or exceeds its bound."""


def _validate_id(value: Any, field: str, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise CheckpointEnvelopeError(f"{field} must be a stable identifier")


def _validate_json_value(root: Any) -> None:
    """Reject cycles, excessive nesting, oversized structures, and non-JSON values."""
    stack = [(root, 0)]
    seen_containers = set()
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > MAX_PAYLOAD_ITEMS:
            raise CheckpointEnvelopeError("checkpoint payload exceeds the item limit")
        if depth > MAX_PAYLOAD_DEPTH:
            raise CheckpointEnvelopeError("checkpoint payload exceeds the nesting limit")
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen_containers:
                raise CheckpointEnvelopeError("checkpoint payload contains a cycle or shared container")
            seen_containers.add(identity)
            for key, child in value.items():
                if not isinstance(key, str):
                    raise CheckpointEnvelopeError("checkpoint object keys must be strings")
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            identity = id(value)
            if identity in seen_containers:
                raise CheckpointEnvelopeError("checkpoint payload contains a cycle or shared container")
            seen_containers.add(identity)
            stack.extend((child, depth + 1) for child in value)
        elif value is None or type(value) in (str, int, bool):
            continue
        elif type(value) is float:
            if not math.isfinite(value):
                raise CheckpointEnvelopeError("non-finite JSON numbers are not permitted")
        else:
            raise CheckpointEnvelopeError("checkpoint payload contains a non-JSON value")


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise CheckpointEnvelopeError("checkpoint payload must contain finite JSON values") from exc


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointEnvelopeError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite(value):
    raise CheckpointEnvelopeError("non-finite JSON numbers are not permitted")


def encode_checkpoint_envelope(
    *,
    workflow_id: str,
    checkpoint_id: str,
    parent_checkpoint_id: str | None,
    sequence: int,
    payload: dict[str, Any],
) -> bytes:
    """Encode a JSON-safe checkpoint payload into a deterministic bounded envelope."""
    _validate_id(workflow_id, "workflow_id")
    _validate_id(checkpoint_id, "checkpoint_id")
    _validate_id(parent_checkpoint_id, "parent_checkpoint_id", optional=True)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise CheckpointEnvelopeError("sequence must be a non-negative integer")
    if not isinstance(payload, dict):
        raise CheckpointEnvelopeError("payload must be a JSON object")
    _validate_json_value(payload)

    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": parent_checkpoint_id,
        "sequence": sequence,
        "payload": payload,
    }
    digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    envelope = {**unsigned, "sha256": digest}
    encoded = _canonical_json(envelope)
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise CheckpointEnvelopeError("checkpoint envelope exceeds the 256 KiB limit")
    return encoded


def decode_checkpoint_envelope(encoded: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Validate size, schema, canonical form, and digest before returning checkpoint data."""
    if not isinstance(encoded, (bytes, bytearray, memoryview)):
        raise CheckpointEnvelopeError("encoded checkpoint must be bytes")
    raw = bytes(encoded)
    if not raw or len(raw) > MAX_ENVELOPE_BYTES:
        raise CheckpointEnvelopeError("checkpoint envelope is empty or exceeds the 256 KiB limit")
    try:
        envelope = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except CheckpointEnvelopeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CheckpointEnvelopeError("checkpoint envelope is not valid UTF-8 JSON") from exc

    if not isinstance(envelope, dict) or set(envelope) != _REQUIRED_KEYS:
        raise CheckpointEnvelopeError("checkpoint envelope has an unexpected shape")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != SCHEMA_VERSION:
        raise CheckpointEnvelopeError("unsupported checkpoint envelope schema")
    _validate_id(envelope["workflow_id"], "workflow_id")
    _validate_id(envelope["checkpoint_id"], "checkpoint_id")
    _validate_id(envelope["parent_checkpoint_id"], "parent_checkpoint_id", optional=True)
    sequence = envelope["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise CheckpointEnvelopeError("sequence must be a non-negative integer")
    if not isinstance(envelope["payload"], dict):
        raise CheckpointEnvelopeError("payload must be a JSON object")
    _validate_json_value(envelope["payload"])
    digest = envelope["sha256"]
    if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
        raise CheckpointEnvelopeError("sha256 must be a lowercase hexadecimal digest")

    unsigned = {key: envelope[key] for key in _UNSIGNED_KEYS}
    expected = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise CheckpointEnvelopeError("checkpoint integrity verification failed")
    if _canonical_json(envelope) != raw:
        raise CheckpointEnvelopeError("checkpoint envelope is not canonically encoded")
    return envelope
