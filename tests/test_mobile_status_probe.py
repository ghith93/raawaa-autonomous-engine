import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from engine import mobile_status_probe as probe


API_URL = "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev"
TOKEN = "synthetic-engine-token"


def status_payload():
    return {
        "unavailable_metrics": {
            "paper_ledger": "Available",
            "provider_usage": "Available",
            "last_scan": "Scan-run heartbeat is not recorded; latest snapshot is separate",
            "source_freshness": "Not yet aggregated from market-data adapters",
            "ci_health": "Not yet ingested from GitHub Actions",
            "kill_switch": "Engine policy state is not yet mirrored to the Worker",
        },
        "tasks": [
            {"status": "pending", "count": 2},
            {"status": "running", "count": 1},
            {"status": "succeeded", "count": 7},
        ],
        "paper_events": [
            {"event_type": "candidate_detected", "count": 3},
            {"event_type": "snapshot_captured", "count": 4},
        ],
        "model_costs_usd": {"total": 99.99},
    }


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class MobileStatusProbeTests(unittest.TestCase):
    def test_summary_reports_migrations_gaps_and_aggregate_counts_only(self):
        result = probe.summarize_mobile_status(status_payload())

        self.assertEqual(result, {
            "http": "200",
            "paper_ledger": "available",
            "provider_usage": "available",
            "last_scan": "not_recorded",
            "source_freshness": "not_aggregated",
            "ci_health": "not_ingested",
            "kill_switch": "not_mirrored",
            "tasks_pending": "2",
            "tasks_running": "1",
            "paper_events": "7",
        })
        self.assertNotIn("model_costs", result)
        self.assertNotIn("99.99", json.dumps(result))

    def test_missing_status_fields_remain_unknown_not_available(self):
        result = probe.summarize_mobile_status({})
        self.assertEqual(result["paper_ledger"], "unknown")
        self.assertEqual(result["provider_usage"], "unknown")
        self.assertEqual(result["last_scan"], "unknown")
        self.assertEqual(result["source_freshness"], "unknown")
        self.assertEqual(result["ci_health"], "unknown")
        self.assertEqual(result["kill_switch"], "unknown")

    def test_authenticated_probe_uses_only_approved_https_worker_and_get(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["timeout"] = timeout
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            return FakeResponse(status_payload())

        result = probe.probe_mobile_status(
            API_URL,
            TOKEN,
            "synthetic-access-id",
            "synthetic-access-secret",
            opener=opener,
        )

        self.assertEqual(result["http"], "200")
        self.assertEqual(seen["url"], API_URL + "/v1/mobile/status")
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["timeout"], 15)
        self.assertEqual(seen["headers"]["authorization"], "Bearer " + TOKEN)
        self.assertEqual(seen["headers"]["cf-access-client-id"], "synthetic-access-id")
        self.assertEqual(seen["headers"]["cf-access-client-secret"], "synthetic-access-secret")

    def test_404_reports_route_not_deployed_without_treating_it_as_a_probe_crash(self):
        from urllib.error import HTTPError

        def missing_route(_request, timeout):
            raise HTTPError(API_URL + "/v1/mobile/status", 404, "Not Found", {}, None)

        result = probe.probe_mobile_status(API_URL, TOKEN, opener=missing_route)
        self.assertEqual(result["http"], "404")
        self.assertEqual(result["paper_ledger"], "route_not_deployed")
        self.assertEqual(result["last_scan"], "route_not_deployed")
        self.assertEqual(result["tasks_pending"], "unknown")

    def test_non_404_http_errors_still_fail_closed(self):
        from urllib.error import HTTPError

        def forbidden(_request, timeout):
            raise HTTPError(API_URL + "/v1/mobile/status", 403, "Forbidden", {}, None)

        with self.assertRaises(HTTPError):
            probe.probe_mobile_status(API_URL, TOKEN, opener=forbidden)

    def test_rejects_non_https_or_unapproved_worker_origins(self):
        for url in (
            "http://" + probe._ALLOWED_HOST,
            "https://example.com",
            API_URL + "/unexpected-path",
            API_URL + "?redirect=elsewhere",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.probe_mobile_status(url, TOKEN, opener=lambda *_args, **_kwargs: self.fail("network must not run"))

    def test_rejects_partial_access_credentials_and_invalid_counts(self):
        with self.assertRaises(ValueError):
            probe.build_headers(TOKEN, "client-id", "")
        with self.assertRaises(ValueError):
            probe.summarize_mobile_status({"tasks": [{"status": "pending", "count": -1}]})
        with self.assertRaises(ValueError):
            probe.summarize_mobile_status({"tasks": [{"status": "pending", "count": True}]})

    def test_main_writes_only_sanitized_github_outputs(self):
        values = probe.summarize_mobile_status(status_payload())
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "ENGINE_API_URL": API_URL,
                "ENGINE_API_TOKEN": TOKEN,
                "CF_ACCESS_CLIENT_ID": "",
                "CF_ACCESS_CLIENT_SECRET": "",
                "GITHUB_OUTPUT": output.name,
            }
            stdout = StringIO()
            with patch.dict(os.environ, env, clear=True), patch.object(
                probe, "probe_mobile_status", return_value=values
            ), redirect_stdout(stdout):
                self.assertEqual(probe.main(), 0)
            output.seek(0)
            parsed = dict(line.rstrip("\n").split("=", 1) for line in output if "=" in line)
            self.assertEqual(parsed["http"], "200")
            self.assertEqual(parsed["paper_ledger"], "available")
            self.assertEqual(parsed["tasks_pending"], "2")
            self.assertEqual(parsed["paper_events"], "7")
            self.assertNotIn(TOKEN, stdout.getvalue())
            self.assertNotIn("99.99", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
