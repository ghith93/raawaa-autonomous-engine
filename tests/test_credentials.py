"""Role-based credential resolution and account discovery."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.credentials import (  # noqa: E402
    fingerprint, load_config, resolve, resolve_cloudflare,
)


def fake_api(routes):
    """Return a get() that answers from a dict of path -> (status, payload)."""
    def get(path, token):
        return routes.get(path, (404, {"success": False, "errors": [{"code": 7003}]}))
    return get


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_primary_alias_wins_over_fallback(self):
        env = {"CLOUDFLARE_API_TOKEN": "primary", "CLOUDFLARE_ACCESS_AUDIT_TOKEN": "audit"}
        found = resolve("cloudflare_token", env, self.config)
        self.assertEqual(found.name, "CLOUDFLARE_API_TOKEN")

    def test_a_fallback_alias_satisfies_the_role_when_the_primary_is_absent(self):
        found = resolve("cloudflare_token", {"CLOUDFLARE_ACCESS_AUDIT_TOKEN": "audit"}, self.config)
        self.assertTrue(found.present)
        self.assertEqual(found.name, "CLOUDFLARE_ACCESS_AUDIT_TOKEN")

    def test_absence_is_data_not_an_exception(self):
        found = resolve("telegram_bot", {}, self.config)
        self.assertFalse(found.present)
        self.assertIsNone(found.name)

    def test_whitespace_only_value_counts_as_absent(self):
        self.assertFalse(resolve("cloudflare_token", {"CLOUDFLARE_API_TOKEN": "   "},
                                 self.config).present)

    def test_unknown_role_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            resolve("nonexistent_role", {}, self.config)

    def test_repr_does_not_leak_the_secret(self):
        env = {"CLOUDFLARE_API_TOKEN": "super-secret"}
        self.assertNotIn("super-secret", repr(resolve("cloudflare_token", env, self.config)))


class FingerprintTests(unittest.TestCase):
    def test_same_value_same_fingerprint(self):
        self.assertEqual(fingerprint("abc"), fingerprint("abc"))

    def test_different_values_are_distinguishable(self):
        self.assertNotEqual(fingerprint("a" * 40), fingerprint("a" * 41))

    def test_absent_is_labelled(self):
        self.assertEqual(fingerprint(""), "absent")

    def test_fingerprint_does_not_contain_the_value(self):
        self.assertNotIn("zzzz", fingerprint("zzzzzzzz"))


class AccountDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_a_working_stored_id_is_used_without_further_calls(self):
        get = fake_api({"/accounts/acc1": (200, {"success": True})})
        cf = resolve_cloudflare({"CLOUDFLARE_API_TOKEN": "t",
                                 "CLOUDFLARE_ACCOUNT_ID": "acc1"}, self.config, get=get)
        self.assertEqual(cf.chosen_account_id, "acc1")
        self.assertIn("stored account id works", cf.evidence)

    def test_the_d1_database_id_settles_a_disagreement(self):
        get = fake_api({
            "/accounts/wrong": (403, {"success": False, "errors": [{"code": 9109}]}),
            "/accounts": (200, {"success": True, "result": [
                {"id": "accA", "name": "A"}, {"id": "accB", "name": "B"}]}),
            "/accounts/accA/d1/database": (200, {"success": True, "result": [
                {"name": "other", "uuid": "uuid-x"}]}),
            "/accounts/accB/d1/database": (200, {"success": True, "result": [
                {"name": "raawaa-autonomous-e", "uuid": "uuid-raawaa"}]}),
        })
        cf = resolve_cloudflare({"CLOUDFLARE_API_TOKEN": "t",
                                 "CLOUDFLARE_ACCOUNT_ID": "wrong",
                                 "D1_DATABASE_ID": "uuid-raawaa"}, self.config, get=get)
        self.assertEqual(cf.chosen_account_id, "accB")
        self.assertIn("D1 database id", cf.evidence)

    def test_a_coincidental_uuid_on_another_account_does_not_win(self):
        get = fake_api({
            "/accounts": (200, {"success": True, "result": [{"id": "accA", "name": "A"}]}),
            "/accounts/accA/d1/database": (200, {"success": True, "result": [
                {"name": "x", "uuid": "different"}]}),
        })
        cf = resolve_cloudflare({"CLOUDFLARE_API_TOKEN": "t",
                                 "D1_DATABASE_ID": "uuid-raawaa"}, self.config, get=get)
        # single visible account is still a valid fallback, but the evidence must say so
        self.assertEqual(cf.chosen_account_id, "accA")
        self.assertIn("no visible account owns", cf.evidence)

    def test_no_token_resolves_to_nothing_rather_than_a_guess(self):
        cf = resolve_cloudflare({}, self.config)
        self.assertIsNone(cf.chosen_account_id)
        self.assertIn("no Cloudflare token", cf.evidence)

    def test_summary_shortens_ids_and_hides_the_token(self):
        get = fake_api({"/accounts/acc1": (200, {"success": True})})
        cf = resolve_cloudflare({"CLOUDFLARE_API_TOKEN": "very-secret-token-value",
                                 "CLOUDFLARE_ACCOUNT_ID": "acc1"}, self.config, get=get)
        summary = cf.summary()
        self.assertNotIn("very-secret-token-value", repr(summary))
        self.assertEqual(summary["account_id"]["matches_stored"], True)

    def test_disagreement_is_visible_in_the_summary(self):
        get = fake_api({
            "/accounts/stored": (403, {"success": False}),
            "/accounts": (200, {"success": True, "result": [{"id": "real", "name": "R"}]}),
        })
        cf = resolve_cloudflare({"CLOUDFLARE_API_TOKEN": "t",
                                 "CLOUDFLARE_ACCOUNT_ID": "stored"}, self.config, get=get)
        self.assertFalse(cf.summary()["account_id"]["matches_stored"])


if __name__ == "__main__":
    unittest.main()
