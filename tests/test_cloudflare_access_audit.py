import unittest
import textwrap
from pathlib import Path

from engine.cloudflare_access_audit import is_access_not_enabled


class CloudflareAccessAuditTests(unittest.TestCase):
    def test_access_workflow_inline_python_is_syntactically_valid(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("cloudflare-access-audit.yml", "cloudflare-access-inventory.yml"):
            with self.subTest(filename=filename):
                workflow = (root / ".github" / "workflows" / filename).read_text(encoding="utf-8")
                marker = "python - <<'PY'\\n"
                self.assertIn(marker, workflow)
                block = workflow.split(marker, 1)[1]
                source = block.split("\\n          PY", 1)[0]
                source = textwrap.dedent(source)
                compile(source, filename + ":python-heredoc", "exec")

    def test_account_level_access_not_enabled_is_an_optional_state(self):
        self.assertTrue(is_access_not_enabled(403, [{
            "code": 9999,
            "message": "access.api.error.not_enabled: Access is not enabled.",
        }]))

    def test_permission_or_authentication_failures_are_not_optional(self):
        self.assertFalse(is_access_not_enabled(403, [{
            "code": 9109,
            "message": "Invalid access token",
        }]))
        self.assertFalse(is_access_not_enabled(401, [{
            "code": 9999,
            "message": "access.api.error.not_enabled: Access is not enabled.",
        }]))

    def test_unstructured_or_unrelated_errors_are_not_optional(self):
        self.assertFalse(is_access_not_enabled(403, []))
        self.assertFalse(is_access_not_enabled(403, [{"code": 9999, "message": "other error"}]))


if __name__ == "__main__":
    unittest.main()
