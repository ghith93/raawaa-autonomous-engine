import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

from scripts.billing_audit_annotation import build_annotation

ROOT = Path(__file__).resolve().parents[1]


class BillingAuditAnnotationTests(unittest.TestCase):
    def test_unavailable_endpoint_is_unknown_not_free(self):
        text = build_annotation({"spend_policy_http_status": 404, "subscriptions_http_status": 200,
                                 "enabled_workers_usage_block": True,
                                 "account_id": "private", "token": "secret"})
        self.assertIn("enabled_workers_usage_block=unknown", text)
        self.assertIn("enabled_durable_objects_usage_block=unknown", text)
        self.assertIn("zero cost are not established", text)
        self.assertNotIn("private", text)
        self.assertNotIn("secret", text)

    def test_observed_booleans_are_reported_without_inferring_hard_stop(self):
        text = build_annotation({"spend_policy_http_status": 200, "subscriptions_http_status": 200,
                                 "enabled_workers_usage_block": True,
                                 "enabled_durable_objects_usage_block": False})
        self.assertIn("enabled_workers_usage_block=true", text)
        self.assertIn("enabled_durable_objects_usage_block=false", text)
        self.assertIn("hard stop and zero cost are not established", text)

    def test_workflow_heredoc_publishes_only_observed_safe_fields(self):
        workflow = (ROOT / ".github/workflows/cloudflare-billing-inventory.yml").read_text()
        source = workflow.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        code = textwrap.dedent(source)
        with tempfile.TemporaryDirectory() as temp:
            policies, subscriptions = Path(temp) / "policies.json", Path(temp) / "subscriptions.json"
            policies.write_text("{}")  # unavailable endpoint must not be parsed
            subscriptions.write_text(json.dumps({"success": True, "result": []}))
            result = subprocess.run(["python3", "-", str(policies), "404", str(subscriptions), "200"],
                                    input=code, text=True, capture_output=True, cwd=ROOT, check=True)
        self.assertIn("::notice title=Billing inventory (read-only)::", result.stdout)
        self.assertIn("enabled_workers_usage_block=unknown", result.stdout)
        self.assertNotIn("secret", result.stdout)

    def test_untrusted_values_are_rejected(self):
        base = {"spend_policy_http_status": 200, "subscriptions_http_status": 200}
        for change in ({"spend_policy_http_status": "200"},
                       {"subscriptions_http_status": True},
                       {"enabled_workers_usage_block": "true\n::notice::injected"},
                       {"enabled_durable_objects_usage_block": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_annotation({**base, **change})


if __name__ == "__main__":
    unittest.main()
