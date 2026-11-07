"""Static reliability contract for the read-only Cloudflare billing inventory."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CloudflareBillingInventoryWorkflowTests(unittest.TestCase):
    def test_inventory_workflow_runs_the_same_fail_closed_subscription_parser(self):
        workflow = (ROOT / ".github" / "workflows" / "cloudflare-billing-inventory.yml").read_text(encoding="utf-8")
        self.assertIn('"scripts/verify_cloudflare_zero_cost_boundary.py"', workflow)
        self.assertIn("Evaluate complete subscription inventory with fail-closed zero-cost parser", workflow)
        self.assertIn("python scripts/verify_cloudflare_zero_cost_boundary.py", workflow)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", workflow)

    def test_billing_reads_retry_transient_http_and_transport_failures_with_bounds(self):
        source = (ROOT / ".github/workflows/cloudflare-billing-inventory.yml").read_text(encoding="utf-8")
        self.assertEqual(source.count("--retry 3 --retry-delay 2 --retry-max-time 15 --connect-timeout 10 --max-time 20"), 2)
        self.assertIn("if [ \"$subscriptions_status\" != \"200\" ]; then", source)
        self.assertIn("if [ \"$policies_status\" = \"404\" ]; then", source)
        self.assertIn("No external usage_block policy is verified.", source)
        self.assertNotIn("--retry-all-errors", source)


if __name__ == "__main__":
    unittest.main()
