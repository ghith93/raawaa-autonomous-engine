"""Token diagnosis must name the fix, and must never echo the token."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import raawaa_token_doctor as doctor  # noqa: E402


class ShapeTests(unittest.TestCase):
    def test_a_quoted_paste_is_detected(self):
        self.assertTrue(any("double quote" in p for p in doctor.shape_report('"abcdef"')))

    def test_a_trailing_newline_is_detected(self):
        self.assertTrue(any("newline" in p for p in doctor.shape_report("abc\n")))

    def test_a_masked_value_is_detected(self):
        self.assertTrue(any("asterisk" in p for p in doctor.shape_report("v1.0-****")))

    def test_a_truncated_value_is_flagged_by_length(self):
        self.assertTrue(any("truncated" in p for p in doctor.shape_report("short")))

    def test_a_longer_value_is_flagged_as_probably_foreign(self):
        # 53 characters with clean shape is exactly what the repository contained and
        # Cloudflare rejected, so the shape check must say something about it.
        problems = doctor.shape_report("a" * 53)
        self.assertTrue(any("40 characters" in p for p in problems), problems)

    def test_a_canonical_40_character_token_is_clean(self):
        self.assertEqual(doctor.shape_report("a" * 40), [])

    def test_bearer_prefix_is_flagged(self):
        problems = doctor.shape_report("Bearer abcdef")
        self.assertTrue(any("Bearer" in p for p in problems))

    def test_a_plausible_token_has_no_shape_problems(self):
        self.assertEqual(doctor.shape_report("a" * 40), [])


class InterpretTests(unittest.TestCase):
    def test_invalid_token_points_at_new_token_page(self):
        verdict, action = doctor.interpret(
            400, {"errors": [{"code": 1000, "message": "Invalid API Token"}]})
        self.assertEqual(verdict, "invalid")
        self.assertIn("api-tokens", action)

    def test_wrong_account_is_distinguished_from_bad_token(self):
        verdict, _ = doctor.interpret(
            404, {"errors": [{"code": 7003, "message": "Could not route"}]})
        self.assertEqual(verdict, "account")

    def test_missing_permission_is_distinguished_from_invalid(self):
        verdict, _ = doctor.interpret(
            403, {"errors": [{"code": 9109, "message": "Unauthorized to access requested resource"}]})
        self.assertEqual(verdict, "permission")

    def test_network_failure_does_not_blame_the_token(self):
        verdict, action = doctor.interpret(0, {})
        self.assertEqual(verdict, "network")
        self.assertIn("may be fine", action)


class SafetyTests(unittest.TestCase):
    def test_diagnosis_never_contains_the_token_value(self):
        secret = "v1.0-" + "z" * 34
        report = doctor.diagnose({"CLOUDFLARE_API_TOKEN": secret})
        self.assertNotIn(secret, repr(report))

    def test_missing_token_is_reported_as_missing(self):
        report = doctor.diagnose({})
        self.assertEqual(report["verdict"], "missing")

    def test_env_file_parsing_ignores_comments_and_blanks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('A=1\n# comment\n\nB = two \n', encoding="utf-8")
            values = doctor.load_env_file(path)
            self.assertEqual(values["A"], "1")
            self.assertEqual(values["B"], "two")
            self.assertNotIn("# comment", values)


if __name__ == "__main__":
    unittest.main()


class TokenAuditTests(unittest.TestCase):
    """Cleanup planning: classify, flag staleness, never delete."""

    def setUp(self):
        import raawaa_token_audit as audit
        self.audit = audit

    def test_names_map_to_a_purpose(self):
        self.assertEqual(self.audit.classify("Raawaa Engine Access Audit"), "read-only-inventory")
        self.assertEqual(self.audit.classify("Workers AI"), "workers-ai")
        self.assertEqual(self.audit.classify("Edit Cloudflare Workers"), "workers-edit")
        self.assertEqual(self.audit.classify("raawaa-pages-deploy"), "pages-deploy")
        self.assertEqual(self.audit.classify("something-else"), "other")

    def test_the_designated_token_is_never_a_deletion_candidate(self):
        tokens = [
            {"name": "Edit Cloudflare Workers", "last_used_on": "2025-01-01T00:00:00Z"},
            {"name": "Old Experiment", "last_used_on": "2025-01-01T00:00:00Z"},
        ]
        result = self.audit.plan(tokens, keep="Edit Cloudflare Workers")
        self.assertNotIn("Edit Cloudflare Workers", result["delete_candidates"])
        self.assertIn("Old Experiment", result["delete_candidates"])

    def test_a_powerful_but_idle_token_is_flagged_as_risk(self):
        tokens = [{"name": "Edit Cloudflare Workers", "last_used_on": "2024-01-01T00:00:00Z"}]
        result = self.audit.plan(tokens)
        self.assertIn("Edit Cloudflare Workers", result["risk"])

    def test_a_recently_used_token_is_not_stale(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        tokens = [{"name": "Workers AI", "last_used_on": now.isoformat()}]
        result = self.audit.plan(tokens, stale_days=90)
        self.assertEqual(result["delete_candidates"], [])

    def test_missing_timestamp_is_treated_as_unknown_not_as_fresh(self):
        self.assertIsNone(self.audit.age_days(None))


class TokenMatrixTests(unittest.TestCase):
    def setUp(self):
        import raawaa_token_matrix as matrix
        self.matrix = matrix

    def test_empty_token_has_no_capabilities(self):
        result = self.matrix.probe("", "abc")
        self.assertFalse(result["usable"])
        self.assertEqual(result["missing"], ["no value"])

    def test_probe_never_returns_the_token(self):
        secret = "z" * 40
        result = self.matrix.probe(secret, "abc")
        self.assertNotIn(secret, repr(result))

    def test_capability_list_covers_what_the_project_needs(self):
        names = [name for name, *_ in self.matrix.CAPABILITIES]
        self.assertEqual(names, ["token_active", "account", "d1", "ai_models"])
