"""Tests for Operational Resource & Capability Intelligence (Resource Brain v1)."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.resource_intelligence import (
    EXECUTION_PLANES,
    LIFECYCLE_STATES,
    VERIFICATION_FACET_KEYS,
    build_resource_inventory,
    render_resource_markdown,
    validate_resource_inventory,
)


class ResourceIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ref_time = datetime(2026, 11, 6, 20, 10, 0, tzinfo=timezone.utc)
        self.inventory = build_resource_inventory(ROOT, reference_time=self.ref_time)

    def test_shipped_inventory_passes_strict_validation_with_zero_errors(self) -> None:
        self.assertEqual(self.inventory["validation_errors"], [])
        self.assertEqual(self.inventory["policy_posture"]["mode"], "safe_dry_run")
        self.assertEqual(self.inventory["policy_posture"]["monthly_budget_usd"], 0)
        self.assertEqual(self.inventory["policy_posture"]["monthly_total_hard_cap_usd"], 0)
        self.assertFalse(self.inventory["policy_posture"]["allow_paid_model_calls"])
        self.assertFalse(self.inventory["policy_posture"]["allow_external_writes"])

    def test_every_resource_uses_canonical_lifecycle_planes_facets_and_secret_names(self) -> None:
        resources = self.inventory["resources"]
        self.assertGreaterEqual(len(resources), 30)
        for item in resources:
            with self.subTest(resource_id=item["id"]):
                self.assertIn(item["lifecycle_state"], LIFECYCLE_STATES)
                self.assertIn(item["execution_plane"], EXECUTION_PLANES)
                self.assertEqual(
                    set(item["verification_facets"].keys()),
                    set(VERIFICATION_FACET_KEYS),
                )
                for sec_name in item["secret_names"]:
                    self.assertRegex(sec_name, r"^[A-Z][A-Z0-9_]{2,79}$")
                    self.assertNotIn("telegram", sec_name.lower())

    def test_groq_and_xai_grok_are_strictly_separated_and_enforced(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        groq = by_id["provider:groq"]
        xai = by_id["provider:xai"]
        ap_grok = by_id["external_catalog_model:activepieces:x-ai/grok-4.20"]
        route_20b = by_id["route:groq-free-gpt-oss-20b-primary"]
        route_120b = by_id["route:groq-free-gpt-oss-120b-fallback"]

        self.assertEqual(groq["lifecycle_state"], "permission_verified")
        self.assertEqual(groq["secret_names"], ["GROQ_API_KEY"])
        self.assertIs(groq["verification_facets"]["authenticated"], True)
        self.assertIs(groq["verification_facets"]["catalog_visible"], True)
        self.assertIs(groq["verification_facets"]["inference_verified"], False)

        self.assertEqual(xai["lifecycle_state"], "blocked")
        self.assertEqual(xai["execution_plane"], "catalog_only")
        self.assertEqual(xai["secret_names"], [])
        self.assertEqual(ap_grok["lifecycle_state"], "blocked")
        self.assertIs(ap_grok["verification_facets"]["catalog_visible"], True)
        self.assertIs(ap_grok["verification_facets"]["authenticated"], False)

        self.assertEqual(route_20b["lifecycle_state"], "tested")
        self.assertIs(route_20b["verification_facets"]["inference_verified"], True)
        self.assertIs(route_20b["quotas_and_cost"]["enabled_default"], False)

        self.assertEqual(route_120b["lifecycle_state"], "blocked")
        self.assertIs(route_120b["quotas_and_cost"]["free_verified"], False)
        self.assertIs(route_120b["quotas_and_cost"]["production_use_allowed"], False)
        self.assertIs(route_120b["verification_facets"]["inference_verified"], False)

    def test_external_operator_surfaces_cannot_be_called_directly_by_engine(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        for surf_id in (
            "external_surface:chatgpt_session_plane",
            "external_surface:activepieces_ghiths_project",
            "external_surface:supabase_ghaiooth_org",
        ):
            surf = by_id[surf_id]
            self.assertEqual(surf["execution_plane"], "external_operator_session")
            self.assertIs(surf["quotas_and_cost"]["can_engine_call_directly"], False)

    def test_live_observations_and_drift_gates_capture_exact_run_ids_and_hazards(self) -> None:
        obs_ids = {obs["run_id"] for obs in self.inventory["live_account_observations"]}
        self.assertTrue(
            {
                42284892504,
                42284232726,
                42285013735,
                42285028161,
                42285042447,
                42285058780,
                42285072627,
            }.issubset(obs_ids)
        )
        codes = {f["code"] for f in self.inventory["drift_and_risk_findings"]}
        self.assertIn("production_d1_pending_migrations_gate", codes)
        self.assertIn("inventory_workflow_declares_write_permission", codes)
        self.assertIn("cloudflare_external_spend_policy_unverified", codes)
        self.assertIn("external_connector_broad_action_posture", codes)

    def test_advancing_clock_past_expiry_marks_observations_and_routes_expired(self) -> None:
        future = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)
        expired_inv = build_resource_inventory(ROOT, reference_time=future)
        by_id = {item["id"]: item for item in expired_inv["resources"]}
        self.assertEqual(by_id["route:groq-free-gpt-oss-20b-primary"]["lifecycle_state"], "expired")
        codes = {f["code"] for f in expired_inv["drift_and_risk_findings"]}
        self.assertIn("live_observation_expired", codes)

    def test_validator_rejects_literal_secret_tokens_unquarantined_120b_and_unblocked_xai(self) -> None:
        leaked = copy.deepcopy(self.inventory)
        leaked["resources"][0]["notes"] = "accidental token gsk_1234567890abcdefghijklmnop"
        self.assertTrue(
            any("literal credential token" in err for err in validate_resource_inventory(leaked))
        )

        bad_120b = copy.deepcopy(self.inventory)
        for item in bad_120b["resources"]:
            if item["id"] == "route:groq-free-gpt-oss-120b-fallback":
                item["quotas_and_cost"]["production_use_allowed"] = True
        self.assertTrue(
            any("quarantined 120B" in err for err in validate_resource_inventory(bad_120b))
        )

        bad_xai = copy.deepcopy(self.inventory)
        for item in bad_xai["resources"]:
            if item["id"] == "provider:xai":
                item["lifecycle_state"] = "configured"
        self.assertTrue(
            any("xAI Grok must remain blocked" in err for err in validate_resource_inventory(bad_xai))
        )

    def test_cli_resource_inventory_json_and_markdown_succeed_strictly(self) -> None:
        proc_json = subprocess.run(
            [sys.executable, "-m", "engine.cli", "resource-inventory", "--strict", "--format", "json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        parsed = json.loads(proc_json.stdout)
        self.assertEqual(parsed["validation_errors"], [])

        md = render_resource_markdown(self.inventory)
        self.assertIn("Groq (`groq`) vs xAI Grok (`xai`)", md)
        self.assertIn("`42285058780`", md)
        self.assertIn("`production_d1_pending_migrations_gate`", md)


if __name__ == "__main__":
    unittest.main()
