"""Tests for the secret-bearing workflow_dispatch main-ref guard."""
import unittest
from pathlib import Path

from scripts.check_workflow_dispatch_guards import (
    PRODUCTION_CLASS,
    job_blocks,
    production_dispatch_violations,
    workflow_violations,
)

ROOT = Path(__file__).resolve().parents[1]


class WorkflowDispatchGuardTests(unittest.TestCase):
    def test_secret_dispatch_job_requires_main_ref(self):
        content = """on:
  workflow_dispatch:
jobs:
  deploy:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
"""
        self.assertEqual(workflow_violations("deploy.yml", content), [])

    def test_secret_dispatch_job_without_guard_is_rejected(self):
        content = """on:
  workflow_dispatch:
jobs:
  deploy:
    steps:
      - env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
"""
        self.assertEqual(len(workflow_violations("deploy.yml", content)), 1)

    def test_staging_replay_jobs_are_explicitly_main_guarded(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for job in ("staging-migrate", "staging-worker-verify",
                    "publish-staging-ops-telemetry", "staging-mobile-status-verify"):
            block = next((content for name, content in job_blocks(workflow) if name == job), "")
            self.assertIn("github.ref == 'refs/heads/main'", block)
            self.assertIn("github.event_name == 'workflow_dispatch'", block)
            self.assertIn("github.event_name == 'push'", block)

    def test_non_secret_or_non_dispatch_workflows_are_not_restricted(self):
        no_secret = """on:
  workflow_dispatch:
jobs:
  inspect:
    steps:
      - run: echo safe
"""
        no_dispatch = """on:
  push:
jobs:
  inspect:
    steps:
      - env:
          TOKEN: ${{ secrets.READ_TOKEN }}
"""
        self.assertEqual(workflow_violations("safe.yml", no_secret), [])
        self.assertEqual(workflow_violations("push.yml", no_dispatch), [])

    def test_production_class_workflow_must_not_be_dispatchable(self):
        content = """on:
  workflow_dispatch:
  workflow_run:
    workflows: ["Engine CI"]
    types: [completed]
jobs:
  deploy:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
"""
        violations = production_dispatch_violations(".github/workflows/deploy-worker.yml", content)
        self.assertEqual(len(violations), 1)
        self.assertIn("must not expose workflow_dispatch", violations[0])

    def test_production_class_workflow_passes_without_dispatch(self):
        content = """on:
  workflow_run:
    workflows: ["Engine CI"]
    types: [completed]
jobs:
  deploy:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
"""
        self.assertEqual(production_dispatch_violations(".github/workflows/deploy-worker.yml", content), [])

    def test_non_production_workflow_may_keep_dispatch(self):
        content = """on:
  workflow_dispatch:
jobs:
  inspect:
    steps:
      - run: echo safe
"""
        self.assertEqual(production_dispatch_violations(".github/workflows/d1-migrate.yml", content), [])
        self.assertEqual(production_dispatch_violations(".github/workflows/maintenance.yml", content), [])

    def test_repository_production_workflows_have_no_dispatch(self):
        for name in sorted(PRODUCTION_CLASS):
            content = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertEqual(production_dispatch_violations(f".github/workflows/{name}", content), [],
                             f"{name} must not expose workflow_dispatch")

    def test_each_secret_bearing_job_is_checked_independently(self):
        content = """on:
  workflow_dispatch:
jobs:
  safe:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.ONE }}
  unsafe:
    steps:
      - env:
          TOKEN: ${{ secrets.TWO }}
"""
        violations = workflow_violations("multi.yml", content)
        self.assertEqual(len(violations), 1)
        self.assertIn("unsafe", violations[0])


if __name__ == "__main__":
    unittest.main()
