import json
import unittest

from experiments.microsoft_agent_framework.checkpoint_envelope import (
    MAX_ENVELOPE_BYTES,
    CheckpointEnvelopeError,
    decode_checkpoint_envelope,
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

    def test_binary_pickle_payload_is_not_accepted_as_a_payload(self):
        with self.assertRaises(CheckpointEnvelopeError):
            self.make_blob(payload=b"pickle-like-bytes")


if __name__ == "__main__":
    unittest.main()
