"""Tests for the secret-bearing workflow_dispatch main-ref guard."""
import unittest

from scripts.check_workflow_dispatch_guards import workflow_violations


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
