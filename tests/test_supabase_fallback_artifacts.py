from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
FUNCTION = ROOT / "supabase" / "functions" / "engine-fallback" / "index.ts"


class SupabaseFallbackArtifactTests(unittest.TestCase):
    def test_database_migrations_keep_a_private_rls_enabled_exact_probe_schema(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
        self.assertIn("create schema if not exists engine_fallback", source)
        for table in ("tasks", "audit_log", "task_process_dispatches", "heartbeat"):
            self.assertIn(f"alter table engine_fallback.{table} enable row level security", source)
        self.assertIn("task_type = 'supervised_probe'", source)
        self.assertIn("python_platform_probe", source)
        self.assertIn("claim_scope', 'supervised_probe_id'", source)
        self.assertIn("grant execute on function public.engine_fallback_process_dispatch", source)
        self.assertIn("from public, anon, authenticated", source)
        self.assertIn("v_outcome ?& array[", source)
        self.assertNotIn("jsonb_object_length(v_outcome)", source)

    def test_audit_task_index_exists_and_no_broad_table_policies(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql")))
        self.assertIn("on engine_fallback.audit_log (task_id)", source)
        # The RLS-enabled/no-policy posture is intentional (advisor INFO):
        # anon/authenticated have no table or schema access and the
        # service_role-only security-definer RPCs are the access boundary.
        # Do not add broad policies merely to silence the linter.
        self.assertNotIn("create policy", source.lower())

    def test_edge_function_verifies_workflow_identity_before_service_role_rpc(self):
        source = FUNCTION.read_text(encoding="utf-8")
        for required in (
            'claims.iss !== EXPECTED_ISSUER',
            'claims.sub !== EXPECTED_SUBJECT',
            'claims.event_name !== "schedule"',
            'claims.ref !== EXPECTED_REF',
            'claims.workflow_ref !== EXPECTED_WORKFLOW_REF',
            'claims.repository_id',
            '"RS256"',
            'Deno.env.get("SUPABASE_SECRET_KEYS")',
            'engine_fallback_enqueue',
            'claim-supervised-probe',
            'process-dispatches',
            'MAX_REQUEST_BYTES',
            'redirect: "error"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("service_role eyJ", source)
        self.assertNotIn("GROQ_API_KEY", source)
        self.assertNotIn("workflow_dispatch", source)

    def test_supabase_gateway_jwt_skip_is_explicit_and_paired_with_custom_auth(self):
        config = (ROOT / "supabase" / "config.toml").read_text(encoding="utf-8")
        function = FUNCTION.read_text(encoding="utf-8")
        self.assertIn("[functions.engine-fallback]", config)
        self.assertIn("verify_jwt = false", config)
        self.assertIn("verifyGithubOidc(tokenMatch[1])", function)
        self.assertIn("EXPECTED_AUDIENCE", function)


if __name__ == "__main__":
    unittest.main()
