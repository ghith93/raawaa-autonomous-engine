"""Tests for Staging-only operational telemetry publication; no Cloudflare calls."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_staging_ops_telemetry import (
    TelemetryPublishError,
    build_telemetry_sql,
    publish_staging_telemetry,
)


def write_fixture(root: Path, *, staging_id="231dd948-04a0-49e3-ad73-444f77d69477"):
    (root / "config").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "config" / "policy.json").write_text(json.dumps({
        "mode": "safe_dry_run",
        "allow_paid_model_calls": False,
        "allow_external_writes": False,
        "model_policy": {"paid_fallback_enabled": False},
    }), encoding="utf-8")
    (root / ".github" / "workflows" / "task-runner.yml").write_text(
        """      - name: Process bounded D1 task batch
          RAAWAA_ENGINE_LIVE_INFERENCE: disabled
      - name: Publish bounded Task Runner status
""",
        encoding="utf-8",
    )
    (root / "wrangler.staging.toml").write_text(
        f"""name = "raawaa-autonomous-engine-staging"
[[d1_databases]]
database_name = "raawaa-engine-staging"
database_id = "{staging_id}"
""",
        encoding="utf-8",
    )
    (root / "wrangler.toml").write_text(
        """name = "raawaa-autonomous-engine-control"
[[d1_databases]]
database_name = "raawaa-autonomous-e"
database_id = "f0ffbed3-940f-488d-a7d1-31cbc5fbf742"
""",
        encoding="utf-8",
    )


def valid_env(**changes):
    env = {
        "CLOUDFLARE_API_TOKEN": "not-a-real-token",
        "CLOUDFLARE_ACCOUNT_ID": "account-placeholder",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_NUMBER": "78",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REF_NAME": "main",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW": "Engine CI",
        "ENGINE_CI_TEST_RESULT": "success",
        "ENGINE_CI_STAGING_MIGRATE_RESULT": "success",
        "ENGINE_CI_STAGING_WORKER_RESULT": "success",
        "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
    }
    env.update(changes)
    return env


class StagingOpsTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_fixture(self.root)
        self.now = datetime(2026, 11, 3, 12, 40, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_two_sanitized_staging_events_and_default_kill_switch(self):
        sql, summary = build_telemetry_sql(valid_env(), self.root, self.now)
        self.assertIn("'ci_health_snapshot'", sql)
        self.assertIn("'kill_switch_snapshot'", sql)
        self.assertIn("raawaa-engine-staging", sql)
        self.assertEqual(summary["conclusion"], "success")
        self.assertEqual(summary["kill_switch"], "engaged")
        self.assertNotIn("not-a-real-token", sql)
        self.assertNotIn("account-placeholder", sql)

    def test_ci_failure_is_persisted_as_failure_not_success(self):
        sql, summary = build_telemetry_sql(
            valid_env(ENGINE_CI_STAGING_WORKER_RESULT="failure"), self.root, self.now
        )
        self.assertEqual(summary["conclusion"], "failure")
        self.assertIn('"conclusion":"failure"', sql)

    def test_skipped_staging_jobs_produce_incomplete_result(self):
        _, summary = build_telemetry_sql(
            valid_env(
                ENGINE_CI_STAGING_MIGRATE_RESULT="skipped",
                ENGINE_CI_STAGING_WORKER_RESULT="skipped",
            ),
            self.root,
            self.now,
        )
        self.assertEqual(summary["conclusion"], "incomplete")

    def test_enabled_live_inference_disengages_gate(self):
        _, summary = build_telemetry_sql(
            valid_env(RAAWAA_ENGINE_LIVE_INFERENCE="enabled"), self.root, self.now
        )
        self.assertEqual(summary["kill_switch"], "not_engaged")

    def test_wrong_database_id_fails_before_runner(self):
        write_fixture(self.root, staging_id="f0ffbed3-940f-488d-a7d1-31cbc5fbf742")
        called = []
        with self.assertRaisesRegex(TelemetryPublishError, "identity mismatch"):
            publish_staging_telemetry(
                valid_env(), root=self.root, runner=lambda *a, **k: called.append(a)
            )
        self.assertEqual(called, [])

    def test_only_main_push_engine_ci_is_accepted(self):
        for changes in (
            {"GITHUB_REF_NAME": "feature"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_WORKFLOW": "Other workflow"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(TelemetryPublishError):
                    build_telemetry_sql(valid_env(**changes), self.root, self.now)

    def test_runner_targets_only_staging_d1_and_uses_pinned_wrangler(self):
        seen = {}

        def fake_runner(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            return None

        result = publish_staging_telemetry(
            valid_env(), root=self.root, runner=fake_runner, now=self.now
        )
        command = seen["command"]
        self.assertEqual(result["published_events"], 2)
        self.assertEqual(command[:4], ["npx", "--yes", "wrangler@4.156.1", "d1"])
        self.assertIn("raawaa-engine-staging", command)
        self.assertIn("--remote", command)
        self.assertIn("wrangler.staging.toml", command)
        self.assertNotIn("wrangler.toml", command)
        self.assertEqual(seen["kwargs"]["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
