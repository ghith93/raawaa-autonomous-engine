import json
import unittest

from experiments.microsoft_agent_framework.checkpoint_envelope import (
    MAX_ENVELOPE_BYTES,
    CheckpointEnvelopeError,
    decode_authenticated_checkpoint_envelope,
    decode_checkpoint_envelope,
    encode_authenticated_checkpoint_envelope,
    encode_checkpoint_envelope,
)


class CheckpointEnvelopeTests(unittest.TestCase):
    def make_blob(self, **overrides):
        values = {
            "workflow_id": "research-workflow",
            "checkpoint_id": "checkpoint-2",
            "parent_checkpoint_id": "checkpoint-1",
            "sequence": 2,
            "payload": {"completed_steps": ["source-a"], "state": {"answer": "safe"}},
        }
        values.update(overrides)
        return encode_checkpoint_envelope(**values)

    def test_round_trip_is_deterministic_and_preserves_lineage(self):
        first = self.make_blob()
        second = self.make_blob()
        self.assertEqual(first, second)
        decoded = decode_checkpoint_envelope(first)
        self.assertEqual(decoded["workflow_id"], "research-workflow")
        self.assertEqual(decoded["parent_checkpoint_id"], "checkpoint-1")
        self.assertEqual(decoded["sequence"], 2)
        self.assertEqual(decoded["payload"]["completed_steps"], ["source-a"])

    def test_tampered_payload_is_rejected(self):
        envelope = json.loads(self.make_blob())
        envelope["payload"]["state"]["answer"] = "tampered"
        tampered = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode()
        with self.assertRaisesRegex(CheckpointEnvelopeError, "integrity"):
            decode_checkpoint_envelope(tampered)


    def test_authenticated_round_trip_and_wrong_key_rejection(self):
        key = b"test-only-integrity-key-material-32-bytes"
        blob = encode_authenticated_checkpoint_envelope(
            integrity_key=key,
            workflow_id="research-workflow",
            checkpoint_id="checkpoint-auth",
            parent_checkpoint_id=None,
            sequence=1,
            payload={"state": {"value": "verified"}},
        )
        decoded = decode_authenticated_checkpoint_envelope(blob, integrity_key=key)
        self.assertEqual(decoded["payload"]["state"]["value"], "verified")
        self.assertNotIn("hmac_sha256", decoded)
        with self.assertRaisesRegex(CheckpointEnvelopeError, "authentication"):
            decode_authenticated_checkpoint_envelope(blob, integrity_key=b"wrong-key-material-32-bytes-long")

    def test_hmac_rejects_writer_who_recomputes_unkeyed_checksum(self):
        key = b"test-only-integrity-key-material-32-bytes"
        original = encode_authenticated_checkpoint_envelope(
            integrity_key=key,
            workflow_id="research-workflow",
            checkpoint_id="checkpoint-auth",
            parent_checkpoint_id=None,
            sequence=1,
            payload={"state": {"value": "original"}},
        )
        original_wrapper = json.loads(original)
        forged_base = self.make_blob(payload={"state": {"value": "writer-forged"}})
        forged_wrapper = json.loads(forged_base)
        forged_wrapper["hmac_sha256"] = original_wrapper["hmac_sha256"]
        forged = json.dumps(forged_wrapper, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode()
        with self.assertRaisesRegex(CheckpointEnvelopeError, "authentication"):
            decode_authenticated_checkpoint_envelope(forged, integrity_key=key)

    def test_authenticated_envelope_rejects_short_keys_and_missing_tags(self):
        with self.assertRaisesRegex(CheckpointEnvelopeError, "32 secret bytes"):
            encode_authenticated_checkpoint_envelope(
                integrity_key=b"too-short",
                workflow_id="research-workflow",
                checkpoint_id="checkpoint-auth",
                parent_checkpoint_id=None,
                sequence=1,
                payload={},
            )
        key = b"test-only-integrity-key-material-32-bytes"
        blob = encode_authenticated_checkpoint_envelope(
            integrity_key=key,
            workflow_id="research-workflow",
            checkpoint_id="checkpoint-auth",
            parent_checkpoint_id=None,
            sequence=1,
            payload={},
        )
        wrapper = json.loads(blob)
        del wrapper["hmac_sha256"]
        malformed = json.dumps(wrapper, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaisesRegex(CheckpointEnvelopeError, "shape"):
            decode_authenticated_checkpoint_envelope(malformed, integrity_key=key)

    def test_duplicate_keys_and_noncanonical_json_are_rejected(self):
        with self.assertRaisesRegex(CheckpointEnvelopeError, "duplicate"):
            decode_checkpoint_envelope(b'{"a":1,"a":2}')
        blob = self.make_blob()
        with self.assertRaisesRegex(CheckpointEnvelopeError, "canonically"):
            decode_checkpoint_envelope(blob + b" ")

    def test_unknown_schema_and_unexpected_fields_are_rejected(self):
        envelope = json.loads(self.make_blob())
        envelope["schema_version"] = 2
        with self.assertRaisesRegex(CheckpointEnvelopeError, "schema"):
            decode_checkpoint_envelope(json.dumps(envelope, sort_keys=True,
                separators=(",", ":")).encode())
        envelope = json.loads(self.make_blob())
        envelope["unexpected"] = True
        with self.assertRaisesRegex(CheckpointEnvelopeError, "shape"):
            decode_checkpoint_envelope(json.dumps(envelope, sort_keys=True,
                separators=(",", ":")).encode())

    def test_oversized_envelopes_are_rejected_before_parsing(self):
        with self.assertRaisesRegex(CheckpointEnvelopeError, "256 KiB"):
            decode_checkpoint_envelope(b"x" * (MAX_ENVELOPE_BYTES + 1))
        with self.assertRaisesRegex(CheckpointEnvelopeError, "256 KiB"):
            self.make_blob(payload={"large": "x" * MAX_ENVELOPE_BYTES})

    def test_invalid_identifiers_sequence_and_payload_fail_closed(self):
        invalid = [
            {"workflow_id": "../workflow"},
            {"checkpoint_id": ""},
            {"sequence": True},
            {"sequence": -1},
            {"payload": ["not", "an", "object"]},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(CheckpointEnvelopeError):
                self.make_blob(**change)

    def test_non_finite_numbers_and_non_json_objects_are_rejected(self):
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload={"value": float("nan")})
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload={"value": object()})
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload={1: "non-string-key"})
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload=cyclic)

    def test_binary_pickle_payload_is_not_accepted_as_a_payload(self):
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload=b"pickle-like-bytes")

    def test_excessive_json_nesting_fails_closed(self):
        nested = {}
        cursor = nested
        for _ in range(100):
            cursor["child"] = {}
            cursor = cursor["child"]
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload=nested)


if __name__ == "__main__":
    unittest.main()
