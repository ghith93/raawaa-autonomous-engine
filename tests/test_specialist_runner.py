import os
import unittest
from unittest.mock import patch

from scripts.run_specialist_lanes import _safe_batch_outcome, main


STAGING_URL = "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev"
RESEARCHER_TOKEN = "researcher-lane-token-0123456789abcdef0123456789"


def valid_environment(**overrides):
    values = {
        "RAAWAA_ENGINE_API_URL": STAGING_URL,
        "RAAWAA_ENGINE_API_TOKEN": "ephemeral-staging-api-token",
        "RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN": "ephemeral-staging-admin-token",
        "GROQ_API_KEY": "free-tier-key-present",
        "RAAWAA_ENGINE_LIVE_INFERENCE": "enabled",
        "RAAWAA_ENGINE_RESEARCHER_LANE_TOKEN": RESEARCHER_TOKEN,
    }
    values.update(overrides)
    return values


class SpecialistLaneRunnerTests(unittest.TestCase):
    def test_missing_required_staging_configuration_fails_closed(self):
        with patch.dict(os.environ, {"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}, clear=True), \
             patch("scripts.run_specialist_lanes.RemoteTaskStore") as remote_store:
            self.assertEqual(main(), 1)
        remote_store.assert_not_called()

    def test_disabled_live_inference_does_not_construct_store_or_gateway(self):
        environment = valid_environment(RAAWAA_ENGINE_LIVE_INFERENCE="disabled")
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.RemoteTaskStore") as remote_store, \
             patch("scripts.run_specialist_lanes.build_live_gateway") as gateway:
            self.assertEqual(main(), 0)
        remote_store.assert_not_called()
        gateway.assert_not_called()

    def test_live_execution_is_restricted_to_exact_staging_worker(self):
        environment = valid_environment(
            RAAWAA_ENGINE_API_URL="https://raawaa-autonomous-engine-control.example",
        )
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.RemoteTaskStore") as remote_store:
            self.assertEqual(main(), 1)
        remote_store.assert_not_called()

    def test_only_policy_eligible_lane_uses_dedicated_gateway_and_d1_invocations(self):
        environment = valid_environment(
            RAAWAA_ENGINE_REVIEWER_LANE_TOKEN="reviewer-lane-token-0123456789abcdef",
        )
        task_store = object()
        invocation_store = object()
        gateway_value = object()
        batch = {
            "status": "completed",
            "tasks_processed": 0,
            "stop_reason": "queue_idle",
            "results": [{"status": "idle"}],
        }
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.RemoteTaskStore", return_value=task_store) as remote_store, \
             patch("scripts.run_specialist_lanes.TaskInvocationStore", return_value=invocation_store) as invocation, \
             patch("scripts.run_specialist_lanes.build_live_gateway", return_value=gateway_value) as gateway, \
             patch("scripts.run_specialist_lanes.db_path", return_value="engine.db"), \
             patch("scripts.run_specialist_lanes.run_batch", return_value=batch) as run_batch, \
             patch("scripts.run_specialist_lanes.run_once") as run_once:
            self.assertEqual(main(), 0)
            remote_store.assert_called_once()
            self.assertEqual(remote_store.call_args.kwargs["lane_token"], RESEARCHER_TOKEN)
            invocation.assert_called_once_with(task_store, worker_lane="specialist:researcher")
            self.assertEqual(gateway.call_args.kwargs["invocation_store"], invocation_store)
            self.assertTrue(str(gateway.call_args.args[1]).endswith("policy.groq-smoke.json"))
            self.assertTrue(str(gateway.call_args.args[2]).endswith("provider_profiles.groq-smoke.json"))
            self.assertEqual(run_batch.call_args.kwargs["max_tasks"], 1)
            self.assertEqual(run_batch.call_args.kwargs["max_parallel"], 1)
            runner = run_batch.call_args.kwargs["runner"]
            runner(task_store)
            run_once.assert_called_once()
            self.assertIs(run_once.call_args.args[0], task_store)
            self.assertIs(run_once.call_args.kwargs["live_gateway"], gateway_value)
            self.assertEqual(
                run_once.call_args.kwargs["environ"]["RAAWAA_ENGINE_WORKER_LANE"],
                "specialist:researcher",
            )
            self.assertEqual(
                run_once.call_args.kwargs["environ"]["RAAWAA_ENGINE_LANE_TOKEN"],
                RESEARCHER_TOKEN,
            )

    def test_gateway_failure_prevents_any_task_batch_or_claim(self):
        environment = valid_environment()
        task_store = object()
        with patch.dict(os.environ, environment, clear=True), \
             patch("scripts.run_specialist_lanes.RemoteTaskStore", return_value=task_store), \
             patch("scripts.run_specialist_lanes.TaskInvocationStore"), \
             patch("scripts.run_specialist_lanes.build_live_gateway", return_value=None), \
             patch("scripts.run_specialist_lanes.db_path", return_value="engine.db"), \
             patch("scripts.run_specialist_lanes.run_batch") as run_batch:
            self.assertEqual(main(), 1)
        run_batch.assert_not_called()

    def test_failed_or_dry_run_task_is_not_reported_as_specialist_success(self):
        self.assertEqual(_safe_batch_outcome({
            "status": "completed", "tasks_processed": 1, "stop_reason": "task_limit",
            "results": [{"status": "blocked", "mode": "live_inference"}],
        }), (False, 1, "task_not_successfully_executed"))
        self.assertEqual(_safe_batch_outcome({
            "status": "completed", "tasks_processed": 1, "stop_reason": "task_limit",
            "results": [{"status": "succeeded", "mode": "dry_run"}],
        }), (False, 1, "task_not_successfully_executed"))
        self.assertEqual(_safe_batch_outcome({
            "status": "completed", "tasks_processed": 1, "stop_reason": "task_limit",
            "results": [{"status": "succeeded", "mode": "live_inference"}],
        }), (True, 1, "task_limit"))


if __name__ == "__main__":
    unittest.main()
