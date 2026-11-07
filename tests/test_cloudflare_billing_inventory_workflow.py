"""Static reliability contract for the read-only Cloudflare billing inventory."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CloudflareBillingInventoryWorkflowTests(unittest.TestCase):
    def test_inventory_workflow_runs_the_same_fail_closed_subscription_parser(self):
        workflow = (ROOT / ".github" / "workflows" / "cloudflare-billing-inventory.yml").read_text(encoding="utf-8")
        self.assertIn('"scripts/verify_cloudflare_zero_cost_boundary.py"', workflow)
        self.assertIn("Evaluate the zero-cost gate and report which endpoint answered what", workflow)
        self.assertIn("python scripts/verify_cloudflare_zero_cost_boundary.py", workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", workflow)

    def test_gate_outcome_is_diagnosable_instead_of_a_bare_exit_code(self):
        """This workflow is the diagnosis surface; a merged gate hid the cause.

        When the gate step failed the whole job, the run list showed only
        "failure" and the sanitized reason was unreadable. The parser's exit
        code is now recorded, and one read-only status probe per endpoint is
        published, so "endpoint missing" and "paid plan present" are different
        observable outcomes.
        """
        workflow = (ROOT / ".github/workflows/cloudflare-billing-inventory.yml").read_text(encoding="utf-8")
        gate_step = workflow.split("Evaluate the zero-cost gate and report which endpoint answered what", 1)[1]
        gate_step = gate_step.split("- name:", 1)[0]
        self.assertIn("continue-on-error: true", gate_step)
        self.assertIn("gate_exit=$?", gate_step)
        self.assertIn("zero_cost_gate_exit=", gate_step)
        for endpoint in ("account_subscriptions", "zones", "spend_policies"):
            self.assertIn(endpoint, gate_step)
        # Status codes only - never a URL, body, account id or credential.
        self.assertNotIn("print(exc.read", gate_step)
        self.assertNotIn("print(url)", gate_step)
        self.assertNotIn("CLOUDFLARE_API_TOKEN=", gate_step)

    def test_billing_reads_retry_transient_http_and_transport_failures_with_bounds(self):
        source = (ROOT / ".github/workflows/cloudflare-billing-inventory.yml").read_text(encoding="utf-8")
        self.assertEqual(source.count("--retry 3 --retry-delay 2 --retry-max-time 15 --connect-timeout 10 --max-time 20"), 2)
        self.assertIn("if [ \"$subscriptions_status\" != \"200\" ]; then", source)
        self.assertIn("if [ \"$policies_status\" = \"404\" ]; then", source)
        self.assertIn("No external usage_block policy is verified.", source)
        self.assertNotIn("--retry-all-errors", source)


if __name__ == "__main__":
    unittest.main()
