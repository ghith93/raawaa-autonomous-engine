import json
import os
import unittest
from unittest.mock import patch

from scripts.run_specialist_lanes import main


class SpecialistLaneRunnerTests(unittest.TestCase):
    def test_missing_lane_tokens_leave_all_specialists_dormant(self):
        environment = {
            "RAAWAA_ENGINE_API_URL": "https://engine.example",
            "RAAWAA_ENGINE_API_TOKEN": "api-token",
            "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
        }
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.subprocess.run") as run:
            self.assertEqual(main(), 0)
        run.assert_not_called()

    def test_disabled_live_inference_refuses_to_claim_or_complete_tasks(self):
        token = "researcher-lane-token-0123456789abcdef0123456789"
        environment = {
            "RAAWAA_ENGINE_API_URL": "https://engine.example",
            "RAAWAA_ENGINE_API_TOKEN": "api-token",
            "RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN": token,
            "RAAWAA_ENGINE_LIVE_INFERENCE": "disabled",
        }
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.subprocess.run") as run:
            self.assertEqual(main(), 0)
        run.assert_not_called()

    def test_enabled_lane_runs_one_bounded_policy_gated_batch(self):
        token = "researcher-lane-token-0123456789abcdef0123456789"
        environment = {
            "RAAWAA_ENGINE_API_URL": "https://engine.example",
            "RAAWAA_ENGINE_API_TOKEN": "api-token",
            "RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN": token,
            "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
        }
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": json.dumps({"tasks_processed": 0, "stop_reason": "queue_idle"}),
            "stderr": "",
        })()
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.subprocess.run", return_value=completed) as run:
            self.assertEqual(main(), 0)
        run.assert_called_once()
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["RAAWAA_ENGINE_WORKER_LANE"], "specialist:researcher")
        self.assertEqual(env["RAAWAA_ENGINE_LANE_TOKEN"], token)
        self.assertEqual(env["RAAWAA_ENGINE_LIVE_INFERENCE"], "enabled")
        self.assertEqual(run.call_args.args[0][-4:], ["--max-tasks", "1", "--max-seconds", "35"])


if __name__ == "__main__":
    unittest.main()
