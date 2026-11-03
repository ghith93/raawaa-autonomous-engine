import unittest
from pathlib import Path


class ProductionWorkerDeploymentContractTests(unittest.TestCase):
    def test_deploy_preserves_dashboard_managed_worker_variables(self):
        workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
        self.assertIn("wrangler@4 deploy --config wrangler.toml --keep-vars", workflow)

    def test_deploy_fails_when_watchdog_is_not_ready(self):
        workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
        self.assertIn('if not watchdog["ready"]:', workflow)
        self.assertIn("refusing to report a healthy production release", workflow.lower())


if __name__ == "__main__":
    unittest.main()
