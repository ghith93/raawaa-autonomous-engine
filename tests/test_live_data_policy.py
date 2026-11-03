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

    def test_proprietary_formula_terms_in_topic_are_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "Review Raawaa hair serum formula and ingredient blend"
        allowed, reason = validate_live_payload(
            "research_brief", "Public research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sensitive_content_detected")

    def test_customer_and_personal_data_terms_in_questions_are_blocked(self):
        payload = self.valid_payload()
        payload["questions"] = ["Summarize customer order and payment details"]
        allowed, reason = validate_live_payload(
            "research_brief", "Public research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sensitive_content_detected")

    def test_confidential_marker_in_title_is_blocked(self):
        allowed, reason = validate_live_payload(
            "research_brief", "Confidential internal analysis", self.valid_payload(), POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sensitive_content_detected")

    def test_public_market_order_book_research_is_not_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "Compare public exchange order-book depth data"
        allowed, reason = validate_live_payload(
            "research_brief", "Public market microstructure", payload, POLICY
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "allowed")

    def test_oversized_topic_is_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "x" * 501
        allowed, reason = validate_live_payload(
            "research_brief", "Research", payload, POLICY
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "payload_schema_invalid")


    def test_customer_contact_and_account_records_are_blocked(self):
        for text in (
            "Summarize customer email addresses from the CRM",
            "Review phone numbers of clients",
            "Extract user account contact records",
        ):
            with self.subTest(text=text):
                payload = self.valid_payload()
                payload["questions"] = [text]
                allowed, reason = validate_live_payload(
                    "research_brief", "Public research", payload, POLICY
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "sensitive_content_detected")

    def test_reverse_order_plural_contact_markers_are_blocked(self):
        for text in (
            "Review emails for customers",
            "Summarize order records of users",
        ):
            with self.subTest(text=text):
                payload = self.valid_payload()
                payload["questions"] = [text]
                allowed, reason = validate_live_payload(
                    "research_brief", "Public research", payload, POLICY
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "sensitive_content_detected")

    def test_inline_credential_assignments_are_blocked(self):
        for text in (
            "The account password=correct-horse-battery",
            "Inspect API key: sk_live_examplecredential",
        ):
            with self.subTest(text=text):
                payload = self.valid_payload()
                payload["topic"] = text
                allowed, reason = validate_live_payload(
                    "research_brief", "Public research", payload, POLICY
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "sensitive_content_detected")

    def test_public_customer_satisfaction_research_is_not_blocked(self):
        payload = self.valid_payload()
        payload["topic"] = "Analyze customer satisfaction trends in public product reviews"
        allowed, reason = validate_live_payload(
            "research_brief", "Public review analysis", payload, POLICY
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "allowed")

    def test_publicly_disclosed_ingredient_research_without_private_markers_is_allowed(self):
        payload = self.valid_payload()
        payload["topic"] = "Compare publicly disclosed shampoo ingredient lists"
        payload["questions"] = ["Summarize ingredients disclosed on official product labels"]
        allowed, reason = validate_live_payload(
            "research_brief", "Public ingredient research", payload, POLICY
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "allowed")

    def test_sensitive_context_patterns_match_across_line_breaks(self):
        for text in (
            "Raawaa\nhair serum formula",
            "customer\nemail addresses from the CRM",
            "Review payment\nrecords for a client account",
            "Confidential\ninternal formula details",
        ):
            with self.subTest(text=text):
                payload = self.valid_payload()
                payload["topic"] = text
                allowed, reason = validate_live_payload(
                    "research_brief", "Public research", payload, POLICY
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "sensitive_content_detected")


if __name__ == "__main__":
    unittest.main()
