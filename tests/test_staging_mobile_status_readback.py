import json
import unittest
from unittest.mock import patch

from scripts.verify_staging_mobile_status import verify


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class StagingMobileStatusReadbackTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "RAAWAA_ENGINE_API_URL": "https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev",
            "RAAWAA_ENGINE_STAGING_API_TOKEN": "test-token",
            "GITHUB_RUN_ID": "41639680521",
            "GITHUB_RUN_NUMBER": "221",
            "GITHUB_SHA": "a" * 40,
        }
        self.payload = {
            "ci_health": {
                "available": True, "status": "success", "conclusion": "success",
                "freshness": "fresh", "workflow": "Engine CI",
                "run_id": 41639680521, "run_number": 221, "head_sha": "a" * 40,
                "jobs": {"test": "success", "staging_migrate": "success", "staging_worker_verify": "success"},
                "recorded_at": "2026-11-03T12:57:25+00:00",
            },
            "kill_switch": {
                "available": True, "status": "engaged", "freshness": "fresh",
                "scope": "scheduled_task_runner_default", "policy_mode": "safe_dry_run",
                "live_inference_default": "disabled", "allow_paid_model_calls": False,
                "paid_fallback_enabled": False, "allow_external_writes": False,
                "source_sha": "a" * 40, "recorded_at": "2026-11-03T12:57:25+00:00",
            },
            "unavailable_metrics": {},
        }

    def test_verifies_current_ci_and_default_kill_switch(self):
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = Response(self.payload)
            result = verify(self.env, opener=build.return_value.open)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["ci_health"], "success")
        self.assertEqual(result["kill_switch"], "engaged")
        self.assertFalse(result["paid_model_calls"])

    def test_rejects_snapshot_for_different_commit(self):
        self.payload["ci_health"]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            verify(self.env, opener=lambda *args, **kwargs: Response(self.payload))

    def test_rejects_non_engaged_kill_switch(self):
        self.payload["kill_switch"]["status"] = "not_engaged"
        with self.assertRaisesRegex(RuntimeError, "safe default policy"):
            verify(self.env, opener=lambda *args, **kwargs: Response(self.payload))

    def test_refuses_non_staging_origin(self):
        self.env["RAAWAA_ENGINE_API_URL"] = "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev"
        with self.assertRaisesRegex(ValueError, "exact Staging Worker"):
            verify(self.env, opener=lambda *args, **kwargs: Response(self.payload))


if __name__ == "__main__":
    unittest.main()
