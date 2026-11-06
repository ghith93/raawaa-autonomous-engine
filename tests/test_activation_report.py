"""The activation report must never claim more than it measured."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import raawaa_activate as activate  # noqa: E402


class CredentialReportingTests(unittest.TestCase):
    def test_values_are_never_copied_into_the_report(self):
        secret = "super-secret-value-123"
        rows = activate.credential_report({"CLOUDFLARE_API_TOKEN": secret})
        dumped = repr(rows)
        self.assertNotIn(secret, dumped)

    def test_missing_credential_carries_an_action(self):
        rows = {r["name"]: r for r in activate.credential_report({})}
        self.assertFalse(rows["CLOUDFLARE_ACCOUNT_ID"]["present"])
        self.assertTrue(rows["CLOUDFLARE_ACCOUNT_ID"]["action"])

    def test_present_credential_needs_no_action(self):
        rows = {r["name"]: r for r in activate.credential_report({"ENGINE_API_TOKEN": "x"})}
        self.assertTrue(rows["ENGINE_API_TOKEN"]["present"])
        self.assertIsNone(rows["ENGINE_API_TOKEN"]["action"])


class VerdictTests(unittest.TestCase):
    def routes(self, reachable):
        return [{"name": "storefront", "reachable": reachable}]

    def test_without_a_probe_the_verdict_admits_it(self):
        verdict = activate._verdict(self.routes(True), [], False, probed=False)
        self.assertIn("not fetched", verdict)

    def test_unreachable_is_reported_before_missing_credentials(self):
        verdict = activate._verdict(self.routes(False), ["X"], False, probed=True)
        self.assertIn("not reachable", verdict)

    def test_live_with_missing_credentials_is_called_unactivated(self):
        verdict = activate._verdict(self.routes(True), ["X"], True, probed=True)
        self.assertIn("not activated", verdict)

    def test_fully_configured_is_the_only_full_pass(self):
        verdict = activate._verdict(self.routes(True), [], True, probed=True)
        self.assertIn("live and activated", verdict)

    def test_image_route_gap_is_its_own_failure_state(self):
        verdict = activate._verdict(self.routes(True), [], False, probed=True)
        self.assertIn("free image route", verdict)


class ProbeTests(unittest.TestCase):
    def test_unreachable_host_returns_false_not_an_exception(self):
        result = activate.probe("https://127.0.0.1:9/nonexistent", timeout=2)
        self.assertFalse(result["reachable"])
        self.assertIn("error", result)


class ReportShapeTests(unittest.TestCase):
    def test_report_separates_deployed_from_activated(self):
        report = activate.build_report({}, do_probe=False)
        self.assertIn("routes", report)
        self.assertIn("credentials", report)
        self.assertTrue(report["missing_credentials"])
        for route in report["routes"]:
            self.assertFalse(route.get("probed", True))

    def test_d1_binding_is_reported_from_the_repo(self):
        report = activate.build_report({}, do_probe=False)
        self.assertEqual(report["d1"]["database_name"], "raawaa-autonomous-e")
        self.assertTrue(report["d1"]["database_id_bound"])


if __name__ == "__main__":
    unittest.main()
