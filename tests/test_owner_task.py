"""The generator exists because hand-written envelopes get rejected.

The first live envelope written by hand for this project failed validation on
two of its ten fields, and the rejection only surfaced after a workflow run. The
generator closes that loop by validating with the same validator the receiving
edge uses, and by posting nothing when validation fails.
"""

from __future__ import annotations

import datetime
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from scripts import owner_task
from scripts.task_envelope import envelope_hash, parse_envelope, validate

GOOD = dict(
    acceptance="The `task-runner` report shows 1 task processed and the queue at 0 pending",
    evidence="pr-470; engine/worker.py",
    base="0b69672",
)


class BuildTests(unittest.TestCase):
    def test_defaults_produce_a_valid_envelope(self):
        envelope = owner_task.build(**GOOD)
        errors, _ = validate(envelope)
        self.assertEqual(errors, [])
        self.assertEqual(envelope["cost_cap_usd"], "0")
        self.assertEqual(envelope["from"], "arena")
        self.assertEqual(envelope["to"], "chatgpt")
        self.assertEqual(envelope["forbidden"], "repo-write")

    def test_expiry_defaults_to_two_weeks_out(self):
        today = datetime.date(2026, 11, 4)
        envelope = owner_task.build(today=today, **GOOD)
        self.assertEqual(envelope["expires"], "2026-11-18")

    def test_acceptance_whitespace_is_collapsed_onto_one_line(self):
        envelope = owner_task.build(
            acceptance="The `task-runner` report shows\n1 task processed and the queue at 0 pending",
            evidence="pr-470", base="0b69672",
        )
        self.assertNotIn("\n", envelope["acceptance"])

    def test_non_integer_cost_cap_is_rendered_without_a_trailing_zero(self):
        self.assertEqual(owner_task.build(cost_cap_usd=2, **GOOD)["cost_cap_usd"], "2")
        self.assertEqual(owner_task.build(cost_cap_usd=0.5, **GOOD)["cost_cap_usd"], "0.5")

    def test_prose_acceptance_is_rejected_by_the_real_validator(self):
        errors, _ = validate(owner_task.build(
            acceptance="Review the pipeline and improve it where it looks weak",
            evidence="pr-470", base="0b69672",
        ))
        self.assertTrue(any("not falsifiable" in error for error in errors))

    def test_unopenable_evidence_is_rejected(self):
        errors, _ = validate(owner_task.build(
            acceptance=GOOD["acceptance"], evidence="the run I did earlier", base="0b69672",
        ))
        self.assertTrue(any("openable artefact" in error for error in errors))

    def test_negative_cost_cap_is_rejected(self):
        errors, _ = validate(owner_task.build(cost_cap_usd=-1, **GOOD))
        self.assertTrue(any("negative" in error for error in errors))


class IdentifierTests(unittest.TestCase):
    def test_first_id_is_one(self):
        self.assertEqual(owner_task.next_id(), "ARENA-OP-001")

    def test_next_id_skips_taken_numbers(self):
        self.assertEqual(
            owner_task.next_id(existing_ids=("ARENA-OP-001", "ARENA-OP-007", "CHATGPT-OP-040")),
            "ARENA-OP-008",
        )

    def test_other_prefixes_are_ignored(self):
        self.assertEqual(owner_task.next_id(existing_ids=("ARENA-INGEST-009",)), "ARENA-OP-001")

    def test_generated_id_matches_the_required_shape(self):
        envelope = owner_task.build(**GOOD)
        self.assertRegex(envelope["id"], r"^ARENA-OP-\d{3}$")


class RenderTests(unittest.TestCase):
    def test_render_round_trips_through_the_edge_parser(self):
        envelope = owner_task.build(**GOOD)
        parsed = parse_envelope(owner_task.render(envelope))
        self.assertEqual(parsed, envelope)
        self.assertEqual(envelope_hash(parsed), envelope_hash(envelope))

    def test_render_starts_with_the_marker(self):
        self.assertTrue(owner_task.render(owner_task.build(**GOOD)).startswith("TASK v1\n"))

    def test_comment_body_carries_the_fingerprint(self):
        envelope = owner_task.build(**GOOD)
        body = owner_task.comment_body(envelope)
        self.assertIn(f"envelope-fingerprint: {envelope_hash(envelope)}", body)
        self.assertIn(owner_task.render(envelope), body)


class HintTests(unittest.TestCase):
    def test_measurability_hint_names_a_way_out(self):
        hints = owner_task.hints(["acceptance is not falsifiable: name a command"])
        self.assertTrue(any("backticks" in hint for hint in hints))

    def test_evidence_hint_lists_openable_forms(self):
        hints = owner_task.hints(["evidence 'x' is not an openable artefact"])
        self.assertTrue(any("pr-470" in hint for hint in hints))

    def test_unrelated_error_gets_no_hint(self):
        self.assertEqual(owner_task.hints(["cost_cap_usd may not be negative"]), [])


class MainTests(unittest.TestCase):
    def _run(self, argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = owner_task.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_valid_envelope_prints_and_does_not_post(self):
        code, out, err = self._run([
            "--acceptance", GOOD["acceptance"], "--evidence", "pr-470",
            "--base", "0b69672", "--id", "ARENA-OP-501",
        ])
        self.assertEqual(code, 0)
        self.assertIn("TASK v1", out)
        self.assertIn(f"fingerprint: {envelope_hash(parse_envelope(out))}", err)

    def test_invalid_envelope_exits_two_and_prints_nothing_usable(self):
        code, out, err = self._run([
            "--acceptance", "make it better somehow", "--evidence", "pr-470",
            "--base", "0b69672", "--id", "ARENA-OP-502",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nothing was posted", err)
        self.assertIn("hint:", err)

    def test_bad_expiry_format_is_rejected_before_building(self):
        with self.assertRaises(SystemExit):
            self._run(["--acceptance", GOOD["acceptance"], "--evidence", "pr-470",
                       "--expires", "18/11/2026"])


if __name__ == "__main__":
    unittest.main()
