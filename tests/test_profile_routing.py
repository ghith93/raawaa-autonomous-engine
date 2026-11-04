import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine.profile_routing import build_route_plan, render_markdown

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "provider_profiles.json"


def _template() -> dict:
    """A route that would be eligible: taken from the shipped registry, then relaxed."""
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    route = json.loads(json.dumps(data["routes"][1]))  # the qwen preview route
    route.update({
        "id": "test-route",
        "enabled": True,
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "free_verified": True,
        "commercial_use_allowed": True,
        "production_use_allowed": True,
        "input_cost_per_million_usd": 0,
        "output_cost_per_million_usd": 0,
        "max_output_tokens": 512,
        "per_task_request_limit": 3,
        "per_task_token_limit": 16000,
        "monthly_request_limit": 10000,
        "monthly_token_limit": 2000000,
        "model_terms_source_url": "https://github.com/openai/gpt-oss/blob/main/USAGE_POLICY",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    })
    return route


def _registry(routes: list[dict]) -> Path:
    directory = tempfile.mkdtemp()
    path = Path(directory) / "profiles.json"
    path.write_text(
        json.dumps({"schema_version": 1, "mode": "managed_opt_in", "routes": routes}),
        encoding="utf-8",
    )
    return path


def _route(**overrides) -> dict:
    route = _template()
    route.update(overrides)
    return route


class RoutePlanTests(unittest.TestCase):
    def test_baseline_fixture_is_eligible(self):
        plan = build_route_plan(_registry([_route()]))
        self.assertEqual(
            plan["selected"], {"provider": "groq", "model": "openai/gpt-oss-20b", "tier": "free"}
        )
        self.assertEqual(plan["excluded"], [])

    def test_disabled_profiles_produce_no_route_and_say_why(self):
        plan = build_route_plan(_registry([_route(enabled=False)]))
        self.assertIsNone(plan["selected"])
        self.assertEqual(plan["excluded"][0]["reason"], "profile_disabled")
        self.assertIn("no profile", plan["selection_reason"])

    def test_unverified_free_usage_is_rejected_with_its_own_reason(self):
        plan = build_route_plan(_registry([_route(free_verified=False)]))
        self.assertEqual(plan["excluded"][0]["reason"], "free_usage_not_verified")

    def test_nonzero_cost_is_rejected_with_its_own_reason(self):
        plan = build_route_plan(_registry([_route(input_cost_per_million_usd=0.5)]))
        self.assertEqual(plan["excluded"][0]["reason"], "would_incur_cost")

    def test_production_disallowance_is_reported_before_cost(self):
        plan = build_route_plan(
            _registry([_route(production_use_allowed=False, input_cost_per_million_usd=9.0)])
        )
        self.assertEqual(plan["excluded"][0]["reason"], "production_use_not_allowed")

    def test_stale_verification_is_rejected(self):
        plan = build_route_plan(_registry([_route(verified_at="2020-01-01T00:00:00+00:00")]))
        self.assertEqual(plan["excluded"][0]["reason"], "verification_stale")

    def test_missing_quota_is_not_ready_rather_than_eligible(self):
        plan = build_route_plan(_registry([_route(daily_token_limit=None)]))
        self.assertEqual(plan["excluded"][0]["reason"], "not_ready_for_routing")

    def test_selection_is_deterministic_across_two_eligible_profiles(self):
        entries = [
            _route(id="b", provider="zeta", model="m2"),
            _route(id="a", provider="alpha", model="m1"),
        ]
        path = _registry(entries)
        first = build_route_plan(path)["selected"]
        second = build_route_plan(path)["selected"]
        self.assertEqual(first, second)
        self.assertEqual(first["provider"], "alpha")

    def test_shipped_registry_routes_nothing_today(self):
        # While every shipped profile is disabled this must stay None. If it
        # ever fails, a provider became routable without the policy decision
        # that is supposed to accompany it.
        plan = build_route_plan()
        self.assertIsNone(plan["selected"])
        self.assertEqual(plan["counts"]["eligible"], 0)
        self.assertTrue(all(item["reason"] == "profile_disabled" for item in plan["excluded"]))

    def test_unreadable_registry_fails_closed(self):
        plan = build_route_plan(Path(tempfile.mkdtemp()) / "absent.json")
        self.assertIsNone(plan["selected"])
        self.assertIn("registry_unavailable", plan["error"])

    def test_no_side_effects_are_reported(self):
        plan = build_route_plan()
        self.assertEqual(plan["side_effects"]["network_calls"], 0)
        self.assertEqual(plan["side_effects"]["credentials_read"], 0)
        self.assertEqual(plan["side_effects"]["profiles_enabled"], 0)

    def test_markdown_names_every_exclusion(self):
        plan = build_route_plan(_registry([
            _route(id="a", enabled=False),
            _route(id="b", free_verified=False),
        ]))
        markdown = render_markdown(plan)
        self.assertIn("profile_disabled", markdown)
        self.assertIn("free_usage_not_verified", markdown)
        self.assertIn("**Selected:** none", markdown)


if __name__ == "__main__":
    unittest.main()
