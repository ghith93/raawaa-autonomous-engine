import unittest

from engine.cloudflare_access_audit import is_access_not_enabled


class CloudflareAccessAuditTests(unittest.TestCase):
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
