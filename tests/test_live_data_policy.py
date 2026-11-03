import unittest

from engine.live_data_policy import validate_live_payload


POLICY = {
    "require_explicit_classification": True,
    "allowed_classifications": ["public"],
    "allowed_task_types": ["research_brief"],
    "allowed_payload_keys": [
        "data_classification", "topic", "questions", "source_urls"
    ],
    "max_prompt_bytes": 7000,
    "blocked_key_fragments": [
        "password", "secret", "apikey", "formula", "ingredient",
        "customer", "email", "phone", "address", "proprietary",
    ],
}


class LiveDataPolicyTests(unittest.TestCase):
    def valid_payload(self):
        return {
            "data_classification": "public",
            "topic": "Open-source agent runtime architectures",
            "questions": ["Compare orchestration and sandbox models"],
            "source_urls": ["https://github.com"],
        }

    def test_explicit_public_research_brief_with_allowlisted_schema_is_allowed(self):
        allowed, reason = validate_live_payload(
            "research_brief", "Compare open-source agent runtimes",
            self.valid_payload(), POLICY,
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "allowed")

    def test_internal_classification_is_blocked(self):
        payload = self.valid_payload()
        payload["data_classification"] = "internal"
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_classification_required_or_disallowed")

    def test_missing_classification_is_blocked(self):
        payload = self.valid_payload()
        payload.pop("data_classification")
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_classification_required_or_disallowed")

    def test_non_research_task_kind_is_blocked_even_when_public(self):
        allowed, reason = validate_live_payload(
            "code_review", "Review public patch", self.valid_payload(), POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "task_type_not_allowed")

    def test_policy_cannot_widen_live_inference_to_other_known_task_types(self):
        widened = dict(POLICY)
        widened["allowed_task_types"] = ["research_brief", "content_draft"]
        allowed, reason = validate_live_payload(
            "content_draft", "Draft marketing copy", self.valid_payload(), widened
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_policy_invalid")

    def test_policy_cannot_replace_research_allowlist_with_another_known_task(self):
        widened = dict(POLICY)
        widened["allowed_task_types"] = ["system_audit"]
        allowed, reason = validate_live_payload(
            "system_audit", "Audit", self.valid_payload(), widened
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_policy_invalid")

    def test_unrecognized_payload_fields_are_blocked(self):
        payload = self.valid_payload()
        payload["notes"] = "ordinary non-sensitive note"
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "payload_schema_invalid")

    def test_sensitive_field_names_are_blocked(self):
        payload = self.valid_payload()
        payload["formula_details"] = "private"
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sensitive_field_detected")

    def test_secret_like_values_are_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "Investigate key gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "secret_pattern_detected")

    def test_personal_email_values_are_blocked(self):
        payload = self.valid_payload()
        payload["questions"] = ["Review account owner@example.com"]
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "personal_data_detected")

    def test_source_url_query_string_is_blocked(self):
        payload = self.valid_payload()
        payload["source_urls"] = ["https://example.com/search?api_key=abc"]
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "source_url_invalid")

    def test_source_url_userinfo_and_local_hosts_are_blocked(self):
        for url in (
            "https://user:pass@example.com/path",
            "https://localhost/private",
            "https://127.0.0.1/admin",
            "http://example.com/insecure",
        ):
            with self.subTest(url=url):
                payload = self.valid_payload()
                payload["source_urls"] = [url]
                allowed, reason = validate_live_payload(
                    "research_brief", "Research", payload, POLICY
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "source_url_invalid")

    def test_invalid_policy_cannot_expand_to_internal_data(self):
        policy = dict(POLICY)
        policy["allowed_classifications"] = ["public", "internal"]
        allowed, reason = validate_live_payload(
            "research_brief", "Research", self.valid_payload(), policy
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "data_policy_invalid")

    def test_oversized_topic_is_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "x" * 501
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "payload_schema_invalid")


if __name__ == "__main__":
    unittest.main()
