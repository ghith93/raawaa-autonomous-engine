import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from engine import cli


class CliTests(unittest.TestCase):
    def test_all_implemented_commands_are_registered(self):
        parser = cli.build_parser()
        commands = [
            ["audit"],
            ["free-models", "--provider", "gemini"],
            ["enqueue", "--type", "research_brief", "--title", "Test"],
            ["list"],
            ["run-once"],
            ["audit-log"],
            ["memory-add", "--category", "decision", "--content", "x", "--source", "test"],
            ["memory-search", "--query", "x"],
            ["memory-list"],
            ["memory-archive", "--id", "memory-id"],
            ["model-status"],
            ["provider-profile-status"],
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(parser.parse_args(command).command, command[0])


    def test_provider_profile_status_is_read_only_and_keeps_groq_disabled(self):
        output = io.StringIO()
        with patch("sys.argv", ["engine", "provider-profile-status"]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["network_calls_enabled"])
        self.assertEqual(len(result["profiles"]), 1)
        profile = result["profiles"][0]
        self.assertEqual(profile["model"], "qwen/qwen3.8-27b")
        self.assertEqual(profile["api_key_env_configured_by_name_only"], "GROQ_API_KEY")
        self.assertFalse(profile["enabled"])
        self.assertFalse(profile["ready_for_routing"])
        self.assertFalse(profile["commercial_use_allowed"])
        self.assertNotIn("gsk_", output.getvalue())

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
