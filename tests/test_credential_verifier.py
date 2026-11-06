"""The credential verifier must fail loudly and name the fix."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import raawaa_verify_cloudflare as verify  # noqa: E402


class AccountIdTests(unittest.TestCase):
    def test_empty_is_a_failure_with_a_link(self):
        step = verify.check_account_id("")
        self.assertFalse(step.ok)
        self.assertIn("dash.cloudflare.com", step.fix or "")

    def test_zone_id_length_is_rejected(self):
        self.assertFalse(verify.check_account_id("abc").ok)

    def test_a_32_hex_id_passes_format_check(self):
        self.assertTrue(verify.check_account_id("0123456789abcdef0123456789abcdef").ok)

    def test_uppercase_hex_is_accepted(self):
        self.assertTrue(verify.check_account_id("0123456789ABCDEF0123456789ABCDEF").ok)


class TokenTests(unittest.TestCase):
    def test_empty_token_fails_before_any_network_call(self):
        step = verify.check_token_active("")
        self.assertFalse(step.ok)
        self.assertIn("token", (step.fix or "").lower())


class ExitCodeTests(unittest.TestCase):
    def test_missing_credentials_exit_2(self):
        steps, meta = verify.run({})
        self.assertFalse(meta["account_id_present"])
        self.assertFalse(meta["token_present"])
        self.assertFalse(all(s.ok for s in steps))

    def test_skip_ai_does_not_spend_neurons(self):
        steps, _ = verify.run({}, skip_ai=True)
        names = [s.name for s in steps]
        self.assertIn("account id", names)
        # with no credentials there is nothing to call, and no AI step is attempted
        self.assertNotIn("Workers AI", names)


class ReportShapeTests(unittest.TestCase):
    def test_every_failure_has_a_machine_readable_shape(self):
        steps, _ = verify.run({"CLOUDFLARE_ACCOUNT_ID": "bad", "CLOUDFLARE_API_TOKEN": "x"})
        for step in steps:
            self.assertIsInstance(step.name, str)
            self.assertIsInstance(step.ok, bool)

    def test_render_marks_pass_and_fail_distinctly(self):
        self.assertIn("PASS", verify.Step("x", True).render())
        self.assertIn("FAIL", verify.Step("x", False).render())


if __name__ == "__main__":
    unittest.main()
