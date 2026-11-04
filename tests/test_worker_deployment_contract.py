import unittest
from pathlib import Path


class ProductionWorkerDeploymentContractTests(unittest.TestCase):
    def test_deploy_preserves_dashboard_managed_worker_variables(self):
        workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
        self.assertIn("wrangler@4 deploy --config wrangler.toml --keep-vars", workflow)

    def test_migration_gate_precedes_production_secret_mutation(self):
        workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
        migration_gate = workflow.index("name: Refuse to deploy with an unapplied migration")
        secret_write = workflow.index("name: Install application bearer token as Worker secret")
        deploy = workflow.index("name: Deploy authenticated D1 control Worker")
        self.assertLess(migration_gate, secret_write)
        self.assertLess(secret_write, deploy)
        gate = workflow[migration_gate:secret_write]
        self.assertIn("SELECT name FROM d1_migrations", gate)
        self.assertNotIn("migrations apply", gate)

    def test_deploy_fails_when_watchdog_is_not_ready(self):
        workflow = Path(".github/workflows/deploy-worker.yml").read_text(encoding="utf-8")
        self.assertIn('if not watchdog["ready"]:', workflow)
        self.assertIn("refusing to report a healthy production release", workflow.lower())


if __name__ == "__main__":
    unittest.main()
