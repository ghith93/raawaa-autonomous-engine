"""Tests for the secret-bearing workflow_dispatch main-ref guard."""
import unittest
from pathlib import Path

from scripts import check_workflow_dispatch_guards as check
from scripts.check_workflow_dispatch_guards import (
    PRODUCTION_CLASS,
    job_blocks,
    production_dispatch_violations,
    workflow_violations,
)

ROOT = Path(__file__).resolve().parents[1]


class WorkflowDispatchGuardTests(unittest.TestCase):
    def test_cli_describes_engine_ci_staging_scope(self):
        import contextlib
        import io
        from unittest.mock import patch
        out = io.StringIO()
        with patch("sys.argv", ["check_workflow_dispatch_guards.py"]), contextlib.redirect_stdout(out):
            self.assertEqual(check.main(), 0)
        self.assertIn("push and scheduled Engine CI runs skip them", out.getvalue())
        self.assertNotIn("push and scheduled runs are read-only", out.getvalue())

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

    def test_staging_jobs_are_opt_in_only_and_never_automatic(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for job in ("staging-migrate", "staging-worker-verify",
                    "publish-staging-ops-telemetry", "staging-mobile-status-verify"):
            block = next((content for name, content in job_blocks(workflow) if name == job), "")
            self.assertIn("github.ref == 'refs/heads/main'", block)
            self.assertNotIn("github.event_name == 'push'", block)
            self.assertNotIn("github.event_name == 'schedule'", block)
            self.assertIn("github.event_name == 'workflow_dispatch' && inputs.enable_staging_operations == true", block)
        self.assertEqual(check.staging_gate_violations(".github/workflows/ci.yml", workflow), [])
        self.assertIn("default: false", workflow)



    def test_engine_ci_has_daily_unattended_regression_schedule(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        triggers = check.parse_triggers(workflow)
        self.assertIn("schedule", triggers)
        self.assertIn('cron: "17 4 * * *"', workflow)
        self.assertIn("workflow_dispatch", triggers)
        for job in check.STAGING_EFFECT_JOBS:
            block = next((content for name, content in job_blocks(workflow) if name == job), "")
            self.assertNotIn("github.event_name == 'schedule'", block)
            self.assertNotIn("github.event_name == 'push'", block)

    def test_staging_jobs_fail_closed_without_explicit_opt_in(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        unsafe = workflow.replace("inputs.enable_staging_operations == true", "false")
        violations = check.staging_gate_violations(".github/workflows/ci.yml", unsafe)
        self.assertEqual(len(violations), len(check.STAGING_EFFECT_JOBS))
        unsafe_default = workflow.replace("default: false", "default: true")
        self.assertTrue(check.staging_gate_violations(".github/workflows/ci.yml", unsafe_default))

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

class TriggerParsingTests(unittest.TestCase):
    """Regression tests for the bypass the reviewer found in the merged guard.

    The previous detector was the regex ``^  workflow_dispatch:``, which only
    matched one spelling. Each case below is valid YAML that re-enables the
    manual trigger, so each must be detected.
    """

    VARIANTS = {
        "block mapping, two spaces": "on:\n  workflow_dispatch:\n",
        "block mapping, four spaces": "on:\n    workflow_dispatch:\n",
        "quoted key": "on:\n  'workflow_dispatch':\n",
        "double-quoted key": 'on:\n  "workflow_dispatch":\n',
        "flow mapping": "on: {workflow_dispatch: {}}\n",
        "flow sequence": "on: [push, workflow_dispatch]\n",
        "block sequence": "on:\n  - push\n  - workflow_dispatch\n",
        "inline scalar": "on: workflow_dispatch\n",
    }

    def test_every_valid_form_is_detected(self):
        for name, content in self.VARIANTS.items():
            with self.subTest(form=name):
                self.assertTrue(
                    check.has_manual_dispatch(content),
                    f"{name} should be detected as a manual dispatch trigger",
                )

    def test_production_class_rejects_every_form(self):
        for name, content in self.VARIANTS.items():
            with self.subTest(form=name):
                violations = check.production_dispatch_violations("deploy-worker.yml", content)
                self.assertTrue(violations, f"{name} must violate the production rule")

    def test_nested_keys_do_not_count_as_triggers(self):
        content = (
            "on:\n"
            "  push:\n"
            "    branches:\n"
            "      - main\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
        )
        self.assertEqual(check.parse_triggers(content), {"push"})

    def test_comment_is_not_a_trigger(self):
        content = "on:\n  push:\n  # workflow_dispatch: removed\n"
        self.assertFalse(check.has_manual_dispatch(content))

    def test_trigger_inside_job_body_is_not_a_trigger(self):
        content = (
            "on:\n"
            "  push:\n"
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            "      - run: echo workflow_dispatch\n"
        )
        self.assertFalse(check.has_manual_dispatch(content))

    def test_missing_on_key_yields_no_triggers(self):
        self.assertEqual(check.parse_triggers("jobs:\n  build: {}\n"), set())

    def test_every_repository_workflow_declares_a_trigger(self):
        workflow_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for path in check.iter_workflow_files(workflow_dir):
            with self.subTest(workflow=path.name):
                triggers = check.parse_triggers(path.read_text(encoding="utf-8"))
                self.assertTrue(triggers, f"{path.name} declares no trigger the parser can see")

    def test_production_deploy_workflow_has_no_dispatch(self):
        path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-worker.yml"
        self.assertFalse(check.has_manual_dispatch(path.read_text(encoding="utf-8")))


class PushAndPullRequestSecretGuardTests(unittest.TestCase):
    def test_arena_branch_push_with_secrets_is_rejected_even_with_job_if(self):
        content = """on:
  push:
    branches: [main, "arena/**"]
jobs:
  verify:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
"""
        violations = check.push_branch_secret_violations(
            ".github/workflows/raawaa-image-route-check.yml", content
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("arena/**", violations[0])

    def test_main_only_push_branches_passes(self):
        content = """on:
  push:
    branches:
      - main
jobs:
  verify:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
"""
        self.assertEqual(check.push_branch_secret_violations("safe.yml", content), [])

    def test_unscoped_push_requires_main_ref_guard_on_secret_jobs(self):
        bad = """on:
  push:
jobs:
  test:
    steps:
      - run: echo ok
  secret_job:
    steps:
      - env:
          TOKEN: ${{ secrets.API_TOKEN }}
"""
        good = """on:
  push:
jobs:
  test:
    steps:
      - run: echo ok
  secret_job:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.API_TOKEN }}
"""
        self.assertEqual(len(check.push_branch_secret_violations("bad.yml", bad)), 1)
        self.assertEqual(check.push_branch_secret_violations("good.yml", good), [])

    def test_pull_request_target_with_secrets_is_rejected(self):
        content = """on:
  pull_request_target:
jobs:
  unsafe:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          TOKEN: ${{ secrets.API_TOKEN }}
"""
        violations = check.push_branch_secret_violations("prt.yml", content)
        self.assertTrue(any("pull_request_target" in v for v in violations))

    def test_manual_dispatch_without_main_guard_on_image_workflow_is_rejected(self):
        content = """on:
  workflow_dispatch:
  push:
    branches: [main]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - env:
          TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
"""
        violations = check.workflow_violations(
            ".github/workflows/raawaa-image-route-check.yml", content
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("lacks an explicit main-ref guard", violations[0])

    def test_all_repository_workflows_pass_push_and_pr_secret_guards(self):
        workflow_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for path in check.iter_workflow_files(workflow_dir):
            with self.subTest(workflow=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertEqual(
                    check.push_branch_secret_violations(f".github/workflows/{path.name}", content),
                    [],
                )

    def test_iter_workflow_files_scans_both_yml_and_yaml(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "safe.yml").write_text("on:\n  push:\njobs:\n  ok:\n    steps: []\n", encoding="utf-8")
            (wf_dir / "secret-leak.yaml").write_text(
                "on:\n  workflow_dispatch:\njobs:\n  bad:\n    steps:\n"
                "      - env:\n          KEY: ${{ secrets.SECRET_KEY }}\n",
                encoding="utf-8",
            )
            (wf_dir / "README.md").write_text("ignore\n", encoding="utf-8")
            found = check.iter_workflow_files(wf_dir)
            self.assertEqual([p.name for p in found], ["safe.yml", "secret-leak.yaml"])
            yaml_content = (wf_dir / "secret-leak.yaml").read_text(encoding="utf-8")
            self.assertEqual(len(check.workflow_violations("secret-leak.yaml", yaml_content)), 1)


