"""Tests for Staging-only operational telemetry publication; no Cloudflare calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_staging_ops_telemetry import (
    TelemetryPublishError,
    build_telemetry_statement,
    publish_staging_telemetry,
)


def write_fixture(root: Path, *, staging_id="231dd948-04a0-49e3-ad73-444f77d69477"):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
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


class FakeD1Client:
    def __init__(self, *, account_id, database_id, api_token, changes=2, rows=None):
        self.configuration = (account_id, database_id, api_token)
        self.changes = changes
        self.rows = rows if rows is not None else [
            {"event": "ci_health_snapshot", "created_at": "2026-11-03T12:40:00+00:00"},
            {"event": "kill_switch_snapshot", "created_at": "2026-11-03T12:40:00+00:00"},
        ]
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("INSERT"):
            return {"meta": {"changes": self.changes}}
        return {"results": self.rows}


class StagingOpsTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_fixture(self.root)
        self.now = datetime(2026, 11, 3, 12, 40, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_script_bootstraps_repo_root_when_invoked_by_path(self):
        env = {
            key: value for key, value in os.environ.items()
            if key not in {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
        }
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "publish_staging_ops_telemetry.py")],
            cwd=tempfile.gettempdir(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("Missing required telemetry environment settings", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_builds_parameterized_staging_events_and_default_kill_switch(self):
        sql, params, summary = build_telemetry_statement(valid_env(), self.root, self.now)
        self.assertIn("VALUES(NULL,?,?,?),(NULL,?,?,?)", sql)
        self.assertEqual(params[0], "ci_health_snapshot")
        self.assertEqual(params[3], "kill_switch_snapshot")
        ci = json.loads(params[1])
        gate = json.loads(params[4])
        self.assertEqual(ci["conclusion"], "success")
        self.assertEqual(gate["status"], "engaged")
        self.assertEqual(summary["kill_switch"], "engaged")
        self.assertNotIn("not-a-real-token", json.dumps(params))
        self.assertNotIn("account-placeholder", json.dumps(params))

    def test_ci_failure_is_persisted_as_failure_not_success(self):
        _, params, summary = build_telemetry_statement(
            valid_env(ENGINE_CI_STAGING_WORKER_RESULT="failure"), self.root, self.now
        )
        self.assertEqual(summary["conclusion"], "failure")
        self.assertEqual(json.loads(params[1])["conclusion"], "failure")

    def test_skipped_staging_jobs_produce_incomplete_result(self):
        _, params, summary = build_telemetry_statement(
            valid_env(
                ENGINE_CI_STAGING_MIGRATE_RESULT="skipped",
                ENGINE_CI_STAGING_WORKER_RESULT="skipped",
            ),
            self.root,
            self.now,
        )
        self.assertEqual(summary["conclusion"], "incomplete")
        self.assertEqual(json.loads(params[1])["conclusion"], "incomplete")

    def test_enabled_live_inference_disengages_gate(self):
        _, params, summary = build_telemetry_statement(
            valid_env(RAAWAA_ENGINE_LIVE_INFERENCE="enabled"), self.root, self.now
        )
        self.assertEqual(summary["kill_switch"], "not_engaged")
        self.assertEqual(json.loads(params[4])["live_inference_default"], "enabled")

    def test_wrong_database_id_fails_before_client_creation(self):
        write_fixture(self.root, staging_id="f0ffbed3-940f-488d-a7d1-31cbc5fbf742")
        called = []
        with self.assertRaisesRegex(TelemetryPublishError, "identity mismatch"):
            publish_staging_telemetry(
                valid_env(), root=self.root,
                client_factory=lambda **kwargs: called.append(kwargs),
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
                    build_telemetry_statement(valid_env(**changes), self.root, self.now)

    def test_writes_and_reads_back_only_staging_d1_events(self):
        clients = []

        def factory(**kwargs):
            client = FakeD1Client(**kwargs)
            clients.append(client)
            return client

        result = publish_staging_telemetry(
            valid_env(), root=self.root, client_factory=factory, now=self.now
        )
        self.assertEqual(result["published_events"], 2)
        self.assertEqual(result["verified_events"], 2)
        self.assertEqual(result["database"], "raawaa-engine-staging")
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].configuration[1], "231dd948-04a0-49e3-ad73-444f77d69477")
        self.assertEqual(len(clients[0].calls), 2)

    def test_fails_if_d1_does_not_confirm_both_inserts(self):
        with self.assertRaisesRegex(TelemetryPublishError, "did not confirm both telemetry inserts"):
            publish_staging_telemetry(
                valid_env(), root=self.root,
                client_factory=lambda **kwargs: FakeD1Client(**kwargs, changes=1),
                now=self.now,
            )

    def test_fails_if_d1_readback_is_incomplete(self):
        with self.assertRaisesRegex(TelemetryPublishError, "readback did not confirm"):
            publish_staging_telemetry(
                valid_env(), root=self.root,
                client_factory=lambda **kwargs: FakeD1Client(**kwargs, rows=[]),
                now=self.now,
            )


if __name__ == "__main__":
    unittest.main()
