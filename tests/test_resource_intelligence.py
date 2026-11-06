"""Tests for Operational Resource & Capability Intelligence (Resource Brain v1)."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.resource_intelligence import (
    ACCOUNT_SUBDOMAIN_RE,
    EXECUTION_PLANES,
    FORBIDDEN_PUBLIC_KEYS,
    LIFECYCLE_STATES,
    UUID_RE,
    VERIFICATION_FACET_KEYS,
    _scan_repository_hygiene,
    _scan_workflows,
    build_resource_inventory,
    render_resource_markdown,
    validate_resource_inventory,
)


class ResourceIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ref_time = datetime(2026, 11, 6, 20, 42, 0, tzinfo=timezone.utc)
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
        self.assertGreaterEqual(len(resources), 45)
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

    def test_production_compatible_is_never_inferred_from_presence_or_staging(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        for role_id in (
            "credential_role:cloudflare_token",
            "credential_role:cloudflare_account",
            "credential_role:d1_database_id",
            "credential_role:engine_token",
        ):
            self.assertEqual(
                by_id[role_id]["verification_facets"]["production_compatible"],
                "unknown",
            )

        self.assertEqual(
            by_id["infra:cloudflare_isolated_staging_worker"]["verification_facets"]["production_compatible"],
            "not_applicable",
        )
        self.assertEqual(
            by_id["infra:cloudflare_commerce_pages_and_worker"]["verification_facets"]["production_compatible"],
            "unknown",
        )
        self.assertEqual(
            by_id["infra:cloudflare_billing_and_access_posture"]["verification_facets"]["production_compatible"],
            "unknown",
        )
        self.assertIs(
            by_id["infra:cloudflare_production_control_worker"]["verification_facets"]["production_compatible"],
            False,
        )

        # Validator rejects Staging or credential-presence resources claiming production_compatible=True
        bad_staging = copy.deepcopy(self.inventory)
        for item in bad_staging["resources"]:
            if item["id"] == "infra:cloudflare_isolated_staging_worker":
                item["verification_facets"]["production_compatible"] = True
        self.assertTrue(
            any("staging_isolated" in err for err in validate_resource_inventory(bad_staging))
        )

        bad_cred = copy.deepcopy(self.inventory)
        for item in bad_cred["resources"]:
            if item["id"] == "credential_role:cloudflare_token":
                item["verification_facets"]["production_compatible"] = True
        self.assertTrue(
            any("credential/secret presence" in err for err in validate_resource_inventory(bad_cred))
        )

    def test_external_surfaces_split_per_connector_evidence_without_umbrella_overclaiming(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        umbrella = by_id["external_surface:chatgpt_session_plane"]
        self.assertEqual(umbrella["lifecycle_state"], "catalogued")
        self.assertEqual(umbrella["verification_facets"]["authenticated"], "not_applicable")
        self.assertIs(umbrella["verification_facets"]["read_permission_verified"], False)
        self.assertIs(umbrella["quotas_and_cost"]["can_engine_call_directly"], False)

        cf_conn = by_id["external_connector:chatgpt_session_plane:cloudflare"]
        self.assertEqual(cf_conn["lifecycle_state"], "blocked")
        self.assertIs(cf_conn["verification_facets"]["present"], False)
        self.assertIs(cf_conn["verification_facets"]["authenticated"], False)
        self.assertIs(cf_conn["verification_facets"]["read_permission_verified"], False)

        slack_conn = by_id["external_connector:chatgpt_session_plane:slack"]
        self.assertEqual(slack_conn["lifecycle_state"], "unknown")
        self.assertEqual(slack_conn["verification_facets"]["authenticated"], "unknown")
        self.assertEqual(slack_conn["verification_facets"]["read_permission_verified"], "unknown")

        canva_conn = by_id["external_connector:chatgpt_session_plane:canva"]
        self.assertEqual(canva_conn["lifecycle_state"], "configured")
        self.assertEqual(canva_conn["verification_facets"]["authenticated"], "unknown")
        self.assertEqual(canva_conn["verification_facets"]["read_permission_verified"], "unknown")

        notion_conn = by_id["external_connector:chatgpt_session_plane:notion"]
        self.assertEqual(notion_conn["lifecycle_state"], "connected")
        self.assertIs(notion_conn["verification_facets"]["authenticated"], True)
        self.assertEqual(notion_conn["verification_facets"]["read_permission_verified"], "unknown")

        gh_conn = by_id["external_connector:chatgpt_session_plane:github"]
        self.assertEqual(gh_conn["lifecycle_state"], "permission_verified")
        self.assertIs(gh_conn["verification_facets"]["authenticated"], True)
        self.assertIs(gh_conn["verification_facets"]["read_permission_verified"], True)

        bad_conn = copy.deepcopy(self.inventory)
        for item in bad_conn["resources"]:
            if item["id"] == "external_connector:chatgpt_session_plane:slack":
                item["verification_facets"]["read_permission_verified"] = True
        self.assertTrue(
            any("cannot claim read_permission_verified=True" in err for err in validate_resource_inventory(bad_conn))
        )

    def test_groq_vs_xai_grok_and_expiry_bound_20b_inference_proof_with_both_attempts(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        groq = by_id["provider:groq"]
        xai = by_id["provider:xai"]
        ap_grok = by_id["external_catalog_model:activepieces:x-ai/grok-4.20"]
        route_20b = by_id["route:groq-free-gpt-oss-20b-primary"]
        route_120b = by_id["route:groq-free-gpt-oss-120b-fallback"]

        self.assertEqual(groq["lifecycle_state"], "permission_verified")
        self.assertEqual(xai["lifecycle_state"], "blocked")
        self.assertEqual(ap_grok["lifecycle_state"], "blocked")
        self.assertEqual(route_120b["lifecycle_state"], "blocked")
        self.assertIs(route_120b["quotas_and_cost"]["free_verified"], False)
        self.assertIs(route_120b["quotas_and_cost"]["production_use_allowed"], False)

        # Historical proof records both run 42218813645 and run 42254857112 attempt 1 vs attempt 2
        hist = route_20b["historical_evidence"]
        self.assertIs(hist["current_route_eligible"], False)
        runs = {r["run_id"]: r for r in hist["historical_staging_inference_runs"]}
        self.assertEqual(runs[42218813645]["conclusion"], "success")
        self.assertEqual(runs[42254857112]["attempt_1"], "succeeded_on_specialist_researcher")
        self.assertEqual(runs[42254857112]["attempt_2"], "failed_pre_io_non_replay_idempotency_guard")
        self.assertEqual(runs[42254857112]["overall_run_conclusion"], "failure")

        # After expiry, inference_verified degrades to False while historical_evidence is preserved
        future = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)
        expired_inv = build_resource_inventory(ROOT, reference_time=future)
        self.assertEqual(expired_inv["validation_errors"], [])
        exp_20b = {item["id"]: item for item in expired_inv["resources"]}["route:groq-free-gpt-oss-20b-primary"]
        self.assertEqual(exp_20b["lifecycle_state"], "expired")
        self.assertIs(exp_20b["verification_facets"]["inference_verified"], False)
        self.assertIs(exp_20b["quotas_and_cost"]["free_verified"], False)
        self.assertIn("historical_staging_inference_runs", exp_20b["historical_evidence"])

        # Validator rejects expired resources retaining inference_verified=True
        bad_exp = copy.deepcopy(expired_inv)
        for item in bad_exp["resources"]:
            if item["id"] == "route:groq-free-gpt-oss-20b-primary":
                item["verification_facets"]["inference_verified"] = True
        self.assertTrue(
            any("expired resource must not present inference_verified=True" in err for err in validate_resource_inventory(bad_exp))
        )

    def test_public_repository_redaction_strips_uuids_subdomains_and_private_keys(self) -> None:
        raw_integrations = (ROOT / "config" / "integrations.json").read_text(encoding="utf-8")
        md_doc = (ROOT / "docs" / "operations" / "resource-capability-inventory.md").read_text(encoding="utf-8")
        serialized_inv = json.dumps(self.inventory, sort_keys=True)

        for label, text in (
            ("config/integrations.json", raw_integrations),
            ("inventory_json", serialized_inv),
            ("resource-capability-inventory.md", md_doc),
        ):
            with self.subTest(surface=label):
                self.assertIsNone(UUID_RE.search(text))
                self.assertIsNone(ACCOUNT_SUBDOMAIN_RE.search(text))
                for forbidden in FORBIDDEN_PUBLIC_KEYS:
                    self.assertNotIn(f'"{forbidden}"', text)

        with_uuid = copy.deepcopy(self.inventory)
        with_uuid["resources"][0]["notes"] = "leaked synthetic uuid 00000000-0000-4000-8000-000000000001"
        self.assertTrue(any("UUID" in e for e in validate_resource_inventory(with_uuid)))

        with_subdomain = copy.deepcopy(self.inventory)
        with_subdomain["resources"][0]["notes"] = "https://example.workers.dev"
        self.assertTrue(any("hostname" in e for e in validate_resource_inventory(with_subdomain)))

        with_key = copy.deepcopy(self.inventory)
        with_key["resources"][0]["quotas_and_cost"]["credits_remaining"] = 999
        self.assertTrue(any("forbidden account-detail keys" in e for e in validate_resource_inventory(with_key)))

    def test_spend_policy_http_404_never_yields_verified_zero_cost_or_overage_block(self) -> None:
        by_id = {item["id"]: item for item in self.inventory["resources"]}
        for infra_id in (
            "infra:cloudflare_production_control_worker",
            "infra:cloudflare_isolated_staging_worker",
            "infra:cloudflare_commerce_pages_and_worker",
            "infra:cloudflare_billing_and_access_posture",
        ):
            qc = by_id[infra_id]["quotas_and_cost"]
            self.assertEqual(qc["engine_policy_cap_usd"], 0.0)
            self.assertIs(qc["engine_policy_paid_overages_authorized"], False)
            self.assertIs(qc["provider_external_hard_stop_verified"], False)
            self.assertEqual(qc["provider_overage_eligibility"], "unverified_spend_policy_http_404")
            self.assertEqual(qc["verified_account_actual_cost_usd"], "unknown")
            self.assertNotIn("paid_overages_allowed", qc)
            self.assertNotIn("cost_usd", qc)

        for field, bad_value in (
            ("provider_external_hard_stop_verified", True),
            ("paid_overages_allowed", False),
            ("cost_usd", 0.0),
            ("verified_account_actual_cost_usd", 0),
        ):
            mutated = copy.deepcopy(self.inventory)
            for item in mutated["resources"]:
                if item["id"] == "infra:cloudflare_production_control_worker":
                    item["quotas_and_cost"][field] = bad_value
            self.assertTrue(
                any("spend-policy is HTTP 404/unverified" in e for e in validate_resource_inventory(mutated)),
                msg=f"expected validator failure for {field}={bad_value!r}",
            )

    def test_hygiene_derives_git_tracked_files_and_python_dependency_manifests(self) -> None:
        hygiene = self.inventory["repository_hygiene"]
        self.assertEqual(hygiene["file_count_source"], "git_ls_files")
        self.assertIsInstance(hygiene["git_tracked_files_count"], int)
        self.assertGreaterEqual(hygiene["git_tracked_files_count"], 440)
        self.assertEqual(hygiene["python_core_manifests_found"], [])
        self.assertEqual(hygiene["python_core_runtime_dependencies"], [])
        self.assertIn("requirements-agent-framework-eval.txt", hygiene["python_optional_or_eval_manifests_found"])
        self.assertIn("agent-framework-core==1.24.0", hygiene["python_optional_or_eval_dependencies"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\ndependencies = ["httpx>=0.27.0"]\n'
                '[project.optional-dependencies]\ndev = ["ruff==0.8.0"]\n',
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text("# comment\npydantic==2.10.1\n-r ignored.txt\n", encoding="utf-8")
            (root / "experiments").mkdir()
            (root / "experiments" / "requirements-exp.txt").write_text("pytest==8.3.0\n", encoding="utf-8")
            derived = _scan_repository_hygiene(root)
            self.assertEqual(derived["file_count_source"], "workspace_walk_non_git")
            self.assertIsNone(derived["git_tracked_files_count"])
            self.assertEqual(derived["workspace_files_count"], 3)
            self.assertEqual(derived["python_core_manifests_found"], ["pyproject.toml", "requirements.txt"])
            self.assertEqual(derived["python_core_runtime_dependencies"], ["httpx>=0.27.0", "pydantic==2.10.1"])
            self.assertEqual(derived["python_optional_or_eval_dependencies"], ["pytest==8.3.0", "ruff==0.8.0"])

    def test_workflow_scanner_includes_dot_yaml_secret_bearing_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "unsafe-probe.yaml").write_text(
                "name: Unsafe YAML\n"
                "on:\n"
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  probe:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - env:\n"
                "          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}\n"
                "        run: echo ok\n",
                encoding="utf-8",
            )
            summaries, secret_map, findings = _scan_workflows(root)
            self.assertEqual([w["workflow_file"] for w in summaries], ["unsafe-probe.yaml"])
            self.assertEqual(secret_map["GROQ_API_KEY"], ["unsafe-probe.yaml"])
            self.assertTrue(
                any(f["code"] == "workflow_secret_or_dispatch_guard_violation" for f in findings)
            )

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
        self.assertIsNone(UUID_RE.search(md))
        self.assertIsNone(ACCOUNT_SUBDOMAIN_RE.search(md))


if __name__ == "__main__":
    unittest.main()
