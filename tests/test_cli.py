import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from engine import cli
from engine.task_store import TaskStore
from engine.remote_task_store import RemoteTaskStore
from engine.task_invocation_store import TaskInvocationStore
from engine.model_gateway import UsageLedger


class CliTests(unittest.TestCase):
    def test_all_implemented_commands_are_registered(self):
        parser = cli.build_parser()
        commands = [
            ["audit"],
            ["free-models", "--provider", "gemini"],
            ["enqueue", "--type", "research_brief", "--title", "Test"],
            ["list"],
            ["run-once"],
            ["run-batch", "--max-parallel", "2"],
            ["audit-log"],
            ["memory-add", "--category", "decision", "--content", "x", "--source", "test"],
            ["memory-search", "--query", "x"],
            ["memory-list"],
            ["memory-archive", "--id", "memory-id"],
            ["model-status"],
            ["monetary-status"],
            ["provider-profile-status"],
            ["research-discover"],
            ["quota-status"],
            ["artemis-status"],
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(parser.parse_args(command).command, command[0])


    def test_artemis_status_is_offline_when_no_endpoint_is_configured(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.argv", ["engine", "artemis-status"]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["integration"], "google_artemis")
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["task_submission_enabled"])

    def test_provider_profile_status_is_read_only_and_keeps_groq_disabled(self):
        output = io.StringIO()
        with patch("sys.argv", ["engine", "provider-profile-status"]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["network_calls_performed"], 0)
        self.assertEqual(len(result["profiles"]), 3)
        profile = next(item for item in result["profiles"] if item["model"] == "openai/gpt-oss-20b")
        self.assertEqual(profile["model"], "openai/gpt-oss-20b")
        self.assertEqual(profile["api_key_env_configured_by_name_only"], "GROQ_API_KEY")
        self.assertFalse(profile["enabled"])
        self.assertFalse(profile["ready_for_routing"])
        self.assertTrue(profile["commercial_use_allowed"])
        self.assertEqual(profile["model_stage"], "production")
        self.assertTrue(profile["production_use_allowed"])
        self.assertNotIn("gsk_", output.getvalue())


    def test_audit_reports_staged_provider_registry_without_enabling_inference(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.audit(), 0)
        self.assertIn("3 provider profiles", output.getvalue())
        self.assertIn("2 deny-by-default tool definitions", output.getvalue())
        self.assertIn("safe dry-run", output.getvalue())


    def test_audit_rejects_budget_overrun_and_malformed_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            for name in ("policy.json", "agents.json", "provider_profiles.json", "tool_permissions.json", "integrations.json"):
                source = Path(cli.ROOT) / "config" / name
                (root / "config" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            policy_path = root / "config" / "policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["monthly_budget_usd"] = 13.0
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "ROOT", root), contextlib.redirect_stdout(output):
                self.assertEqual(cli.audit(), 1)
            self.assertIn("monthly_budget_usd must equal the model monthly hard cap", output.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            for name in ("policy.json", "agents.json", "provider_profiles.json", "tool_permissions.json", "integrations.json"):
                source = Path(cli.ROOT) / "config" / name
                (root / "config" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            policy_path = root / "config" / "policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["per_task_budget_usd"] = True
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "ROOT", root), contextlib.redirect_stdout(output):
                self.assertEqual(cli.audit(), 1)
            self.assertIn("per_task_budget_usd must be a finite non-negative number", output.getvalue())


    def test_audit_rejects_invalid_parallel_agent_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            for name in ("policy.json", "agents.json", "provider_profiles.json", "tool_permissions.json", "integrations.json"):
                source = Path(cli.ROOT) / "config" / name
                (root / "config" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            policy_path = root / "config" / "policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["max_parallel_agents"] = 6
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "ROOT", root), contextlib.redirect_stdout(output):
                self.assertEqual(cli.audit(), 1)
            self.assertIn("max_parallel_agents", output.getvalue())

    def test_run_batch_cannot_exceed_configured_parallel_agent_policy(self):
        with patch("sys.argv", ["engine", "run-batch", "--max-parallel", "3"]):
            with patch("engine.cli.build_live_gateway", return_value=None):
                with patch("engine.cli.store", return_value=object()):
                    with self.assertRaisesRegex(ValueError, "configured max_parallel_agents"):
                        cli.main()

    def test_remote_runner_receives_durable_invocation_store_but_local_runner_does_not(self):
        remote = RemoteTaskStore("https://engine.example", "test-token")
        with patch("engine.cli.build_live_gateway", return_value="gateway") as builder:
            self.assertEqual(cli.build_gateway_for_task_store(remote), "gateway")
        remote_kwargs = builder.call_args.kwargs
        self.assertIsInstance(remote_kwargs["invocation_store"], TaskInvocationStore)
        self.assertIs(remote_kwargs["invocation_store"].task_store, remote)

        with tempfile.TemporaryDirectory() as tmp:
            local = TaskStore(Path(tmp) / "local.db")
            with patch("engine.cli.build_live_gateway", return_value=None) as builder:
                self.assertIsNone(cli.build_gateway_for_task_store(local))
            self.assertIsNone(builder.call_args.kwargs["invocation_store"])

    def test_run_once_uses_live_runtime_factory_but_default_remains_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "engine.db")
            TaskStore(db_path).enqueue(
                "research_brief", "Test task", {"topic": "safe test"}
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"RAAWAA_ENGINE_DB": db_path}, clear=True):
                with patch("engine.cli.build_live_gateway", return_value=None) as factory:
                    with patch("sys.argv", ["engine", "run-once"]):
                        with contextlib.redirect_stdout(output):
                            self.assertEqual(cli.main(), 0)
                    factory.assert_called_once()
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "dry_run")

    def test_audit_rejects_enabled_provider_profile_in_safe_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            for name in ("policy.json", "agents.json", "provider_profiles.json", "tool_permissions.json", "integrations.json"):
                source = Path(cli.ROOT) / "config" / name
                (root / "config" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            profiles = json.loads((root / "config" / "provider_profiles.json").read_text(encoding="utf-8"))
            profiles["routes"][0]["enabled"] = True
            (root / "config" / "provider_profiles.json").write_text(json.dumps(profiles), encoding="utf-8")
            output = io.StringIO()
            with patch.object(cli, "ROOT", root), contextlib.redirect_stdout(output):
                self.assertEqual(cli.audit(), 1)
            self.assertIn("disabled in safe_dry_run", output.getvalue())


    def test_quota_status_is_local_and_reports_disabled_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch.dict(os.environ, {"RAAWAA_ENGINE_DB": os.path.join(tmp, "engine.db")}):
                with patch("sys.argv", ["engine", "quota-status"]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["network_calls_performed"], 0)
            self.assertEqual(result["route_count"], 3)
            self.assertEqual(result["eligible_routes"], 0)
            self.assertEqual(result["alerts"], [])
            self.assertTrue(all(not route["enabled"] for route in result["routes"]))
            self.assertNotIn("gsk_", output.getvalue())

    def test_quota_alerts_warn_at_eighty_percent_and_critical_at_limit(self):
        route = {
            "route_id": "groq:test:model",
            "minute_requests_used": 24,
            "minute_request_limit": 30,
            "minute_tokens_used": 8000,
            "minute_token_limit": 8000,
            "daily_requests_used": 1,
            "daily_request_limit": 1000,
        }
        alerts = cli.quota_alerts([route])
        self.assertEqual(len(alerts), 2)
        self.assertEqual({item["level"] for item in alerts}, {"warning", "critical"})
        self.assertEqual({item["window"] for item in alerts}, {"minute"})

    def test_memory_cli_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch.dict(os.environ, {"RAAWAA_ENGINE_DB": os.path.join(tmp, "engine.db")}):
                with patch("sys.argv", ["engine", "memory-add", "--category", "decision",
                                        "--content", "Use SQLite", "--source", "test",
                                        "--tags", "architecture,storage"]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
                created = json.loads(output.getvalue())
                self.assertEqual(created["category"], "decision")
                output.seek(0); output.truncate(0)
                with patch("sys.argv", ["engine", "memory-search", "--query", "SQLite"]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
                results = json.loads(output.getvalue())
                self.assertEqual(len(results), 1)
                output.seek(0); output.truncate(0)
                with patch("sys.argv", ["engine", "memory-archive", "--id", created["id"]]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
                self.assertEqual(json.loads(output.getvalue()), {"archived": True})

    def test_single_provider_discovery_missing_key_is_structured(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.argv", ["engine", "free-models", "--provider", "gemini"]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["generation_requests"], 0)
        self.assertIn("gemini", result["unavailable"])
        self.assertNotIn("models", result["unavailable"]["gemini"])

    def test_model_status_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with patch.dict(os.environ, {"RAAWAA_ENGINE_DB": os.path.join(tmp, "engine.db")}):
                with patch("sys.argv", ["engine", "model-status"]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
            status = json.loads(output.getvalue())
            self.assertFalse(status["network_calls_enabled"])
            self.assertFalse(status["paid_calls_enabled"])
            self.assertEqual(status["budgets"]["per_task_usd"], 0.2)

    def test_monetary_status_is_read_only_and_lists_ambiguous_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "engine.db")
            ledger = UsageLedger(db_path)
            ledger.reserve(
                "groq", "openai/gpt-oss-20b", "research", 0.25, 0.4, 12.0,
                task_id="task-test", per_task_budget_usd=0.30,
            )
            output = io.StringIO()
            with patch.dict(os.environ, {"RAAWAA_ENGINE_DB": db_path}, clear=True):
                with patch("sys.argv", ["engine", "monetary-status"]):
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["network_calls_performed"], 0)
            self.assertFalse(result["paid_calls_enabled"])
            self.assertEqual(result["usage"]["pending_reservations"], 1)
            self.assertEqual(result["pending_reservations"][0]["task_id"], "task-test")
            self.assertEqual(result["pending_reservations"][0]["provider"], "groq")
            self.assertEqual(result["pending_reservations"][0]["estimated_cost_usd"], 0.25)
            self.assertEqual(result["per_task_usage"][0]["task_id"], "task-test")
            self.assertTrue(result["per_task_usage"][0]["over_budget"])
            self.assertFalse(result["budget_status"]["daily_over_budget"])
            self.assertFalse(result["budget_status"]["monthly_over_budget"])
            self.assertIn("Legacy usage rows", result["per_task_attribution_note"])
            self.assertIn("Do not release", result["reconciliation_note"])
            self.assertNotIn("gsk_", output.getvalue())

    def test_provider_discovery_command_is_explicit_and_mocked(self):
        output = io.StringIO()
        expected = {"discovery_only": True, "generation_requests": 0, "models": {}, "unavailable": {}}
        with patch("sys.argv", ["engine", "free-models", "--provider", "all"]):
            with patch("engine.cli.discover_all", return_value=expected) as discover_all:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 0)
        discover_all.assert_called_once_with()
        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
