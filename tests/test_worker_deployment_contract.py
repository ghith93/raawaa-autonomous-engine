"""Contract tests for the production Worker deployment workflow (issue #521).

These tests read ``.github/workflows/deploy-worker.yml`` as text and parse it as
YAML. They exist because a production deployment path is exactly the kind of
file that silently regresses: a "harmless" edit can reintroduce a moving
dependency reference or a credential write, and nothing in a normal unit-test
run would notice.

Scope is deliberately static. Nothing here contacts Cloudflare, GitHub, or the
deployed Worker, and no secret value is read, written, or compared.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/deploy-worker.yml")
PROBE = Path("scripts/bearer_rotation_probe.py")
TEST_STEP_NAMES = [
    "Check out reviewed main revision",
    "Require explicit deployment confirmation",
    "Set up Node.js",
    "Validate deployment secrets",
    "Refuse to deploy with an unapplied migration",
    "Verify the deployed Worker already accepts the repository bearer token",
    "Verify the deployed Worker accepts the general consumer capability",
    "Deploy authenticated D1 control Worker",
    "Verify authenticated watchdog readiness without exposing secrets",
    "Verify the deployed Worker can see the schema it depends on",
]


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _run_commands() -> list[str]:
    """Every ``run:`` block in the workflow, without the surrounding YAML."""
    commands: list[str] = []
    current: list[str] | None = None
    for line in _workflow_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- name:") or stripped.startswith("run:"):
            if current is not None:
                commands.append("\n".join(current))
                current = None
            if stripped.startswith("run:"):
                inline = stripped[len("run:"):].strip()
                current = [inline] if inline and not inline.startswith("|") else []
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        commands.append("\n".join(current))
    return commands


class ProductionWorkerDeploymentContractTests(unittest.TestCase):
    def test_deploy_preserves_dashboard_managed_worker_variables(self):
        self.assertIn(
            "wrangler@4.156.1 deploy --config wrangler.toml --keep-vars",
            _workflow_text(),
        )

    def test_migration_gate_precedes_credential_verification_and_deploy(self):
        workflow = _workflow_text()
        migration_gate = workflow.index("name: Refuse to deploy with an unapplied migration")
        preflight = workflow.index(
            "name: Verify the deployed Worker already accepts the repository bearer token"
        )
        deploy = workflow.index("name: Deploy authenticated D1 control Worker")
        self.assertLess(migration_gate, preflight)
        self.assertLess(preflight, deploy)
        gate = workflow[migration_gate:preflight]
        self.assertIn("SELECT name FROM d1_migrations", gate)
        self.assertNotIn("migrations apply", gate)

    def test_deploy_fails_when_watchdog_is_not_ready(self):
        workflow = _workflow_text()
        self.assertIn('if not watchdog["ready"]:', workflow)
        self.assertIn("refusing to report a healthy production release", workflow.lower())

    # --- issue #521: pinned Wrangler everywhere in the production path --------

    def test_no_moving_wrangler_reference_anywhere_in_production_path(self):
        """``wrangler@4`` follows a major line, so the CLI can change under review."""
        for command in _run_commands():
            for reference in re.findall(r"wrangler@[^\s\"']+", command):
                self.assertRegex(
                    reference,
                    r"^wrangler@\d+\.\d+\.\d+$",
                    f"unpinned Wrangler reference {reference!r} in the deployment workflow",
                )

    def test_every_wrangler_invocation_uses_the_same_exact_version(self):
        references = {
            reference
            for command in _run_commands()
            for reference in re.findall(r"wrangler@[^\s\"']+", command)
        }
        self.assertEqual(references, {"wrangler@4.156.1"})

    # --- issue #521: a merge must not rewrite a live credential --------------

    def test_deployment_never_writes_a_worker_secret(self):
        """Deployment ships code; credentials are a separate, deliberate action."""
        for command in _run_commands():
            self.assertNotIn(
                "secret put",
                command,
                "the production deployment must not mutate Worker secrets",
            )

    def test_token_rotation_marker_is_gone(self):
        """A merge-message marker must not be able to rotate the bearer secret."""
        self.assertNotIn("[rotate-engine-api-token]", _workflow_text())

    def test_credential_preflight_reads_no_d1_and_mutates_nothing(self):
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn("/v1/diagnostics/auth", source)
        self.assertIn('method="POST"', source)
        for forbidden in ("secret put", "d1 execute", "migrations apply"):
            self.assertNotIn(forbidden, source)
        # The token must travel in a header, never in the URL (URLs reach logs,
        # proxies and error text far more easily than Authorization headers).
        self.assertNotIn("?token=", source)
        self.assertIn('"Authorization": "Bearer " + token', source)

    def test_probe_module_is_importable_and_never_logs_the_token(self):
        tree = ast.parse(PROBE.read_text(encoding="utf-8"))
        printed: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                for argument in node.args:
                    if isinstance(argument, ast.Name):
                        printed.append(argument.id)
        self.assertNotIn("token", printed)
        self.assertNotIn("headers", printed)

    def test_probe_exit_codes_encode_verified_mismatch_and_no_verdict(self):
        namespace: dict[str, object] = {"__name__": "probe_under_test"}
        exec(compile(PROBE.read_text(encoding="utf-8"), str(PROBE), "exec"), namespace)
        classify = namespace["classify"]
        self.assertEqual(classify(200)[0], namespace["EXIT_VERIFIED"])
        self.assertEqual(classify(401)[0], namespace["EXIT_MISMATCH"])
        self.assertEqual(classify(None)[0], namespace["EXIT_NO_VERDICT"])
        # "not configured" is its own outcome, not a rejection and not a verdict.
        self.assertNotEqual(namespace["EXIT_SECRET_ABSENT"], namespace["EXIT_MISMATCH"])
        self.assertNotEqual(namespace["EXIT_SECRET_ABSENT"], namespace["EXIT_NO_VERDICT"])
        # A 403 or 5xx is an edge/Worker condition, not proof of a wrong token.
        for ambiguous in (403, 404, 500, 503):
            self.assertEqual(classify(ambiguous)[0], namespace["EXIT_NO_VERDICT"])

    def test_probe_messages_never_echo_secret_material(self):
        namespace: dict[str, object] = {"__name__": "probe_under_test"}
        exec(compile(PROBE.read_text(encoding="utf-8"), str(PROBE), "exec"), namespace)
        classify = namespace["classify"]
        for status in (200, 401, 403, 500, None):
            message = classify(status)[1]
            self.assertNotIn("Bearer ", message.replace("the presented bearer", ""))

    # --- the preflight is exercised against a local fake, never production ---

    def test_probe_against_local_server_maps_status_to_exit_code(self):
        """Drive ``probe()`` over real HTTP; no Cloudflare or GitHub is contacted."""
        import http.server
        import threading

        seen: list[tuple[str, str, str, str]] = []
        status_code = {"value": 200}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
                seen.append(
                    (
                        self.command,
                        self.path,
                        self.headers.get("Authorization", ""),
                        self.headers.get("User-Agent", ""),
                    )
                )
                self.send_response(status_code["value"])
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *args):  # keep the test output clean
                return

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/diagnostics/auth"
        try:
            namespace: dict[str, object] = {"__name__": "probe_under_test"}
            exec(compile(PROBE.read_text(encoding="utf-8"), str(PROBE), "exec"), namespace)
            probe = namespace["probe"]
            secret = "unit-test-value-not-a-real-credential"

            status_code["value"] = 200
            self.assertEqual(probe(secret, timeout=5, url=url), 200)
            self.assertEqual(seen[-1][0], "POST")
            self.assertEqual(seen[-1][1], "/v1/diagnostics/auth")
            self.assertEqual(seen[-1][2], "Bearer " + secret)
            self.assertTrue(seen[-1][3].startswith("RaawaaAutonomousEngine/"))

            status_code["value"] = 401
            self.assertEqual(probe(secret, timeout=5, url=url), 401)

            status_code["value"] = 503
            self.assertEqual(probe(secret, timeout=5, url=url), 503)
        finally:
            server.shutdown()
            server.server_close()

        # An unreachable endpoint must be a verdict-free failure, not a rejection.
        self.assertIsNone(probe("unit-test-value", timeout=2, url="http://127.0.0.1:1/x"))

    def test_absent_secret_is_reported_separately_and_never_as_a_mismatch(self):
        """A repository without the secret must not read as a rejected credential."""
        import os
        from unittest.mock import patch

        namespace: dict[str, object] = {"__name__": "probe_under_test"}
        exec(compile(PROBE.read_text(encoding="utf-8"), str(PROBE), "exec"), namespace)
        main = namespace["main"]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENGINE_API_TOKEN", None)
            # If the code tried to probe anyway, this would fail: there is no token.
            with patch.object(namespace["probe"], "__call__", side_effect=AssertionError("probed")):
                code = main()
        self.assertEqual(code, namespace["EXIT_SECRET_ABSENT"])

    def test_workflow_treats_absent_secret_as_a_warning_not_a_hard_stop(self):
        workflow = _workflow_text()
        preflight = workflow[workflow.index("name: Verify the deployed Worker already accepts"):]
        preflight = preflight[: preflight.index("name: Deploy authenticated D1 control Worker")]
        # 0 and 2 continue; anything else exits 1.
        self.assertIn("0)", preflight)
        self.assertIn("2)", preflight)
        self.assertIn("exit 1", preflight)

    def test_probe_never_puts_the_token_in_the_request_url(self):
        source = PROBE.read_text(encoding="utf-8")
        calls = re.findall(r"Request\((.*?)\)", source, re.DOTALL)
        self.assertTrue(calls, "expected a Request(...) construction")
        for call in calls:
            self.assertNotIn("token", call.split("headers=")[-1])

    # --- the workflow still parses as YAML with the expected step order ------

    def test_workflow_step_order_and_names(self):
        text = _workflow_text()
        names = re.findall(r"^      - name: (.+)$", text, re.MULTILINE)
        self.assertEqual(names, TEST_STEP_NAMES)
        # A step name may appear once; duplicates would make log attribution wrong.
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
