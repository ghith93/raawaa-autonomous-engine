import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from scripts.verify_cloudflare_zero_cost_boundary import (
    _RejectRedirects,
    assess_subscriptions,
    check_boundary,
)


class ZeroCostBoundaryTests(unittest.TestCase):
    def test_default_free_account_without_paid_subscriptions_is_eligible(self):
        result = assess_subscriptions({"success": True, "result": []})
        self.assertEqual(result, {
            "eligible": True,
            "reason": "no_active_paid_subscriptions",
            "active_paid_subscriptions": 0,
        })

    def test_active_paid_subscription_blocks_staging(self):
        result = assess_subscriptions({"success": True, "result": [
            {"state": "Paid", "price": 5, "product_id": "workers"},
        ]})
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_subscription_present")
        self.assertEqual(result["active_paid_subscriptions"], 1)

    def test_cancelled_subscription_does_not_count_as_active_paid(self):
        result = assess_subscriptions({"success": True, "result": [
            {"state": "Cancelled", "price": 5, "product_id": "workers"},
            {"state": "Expired", "price": 10, "product_id": "other"},
        ]})
        self.assertTrue(result["eligible"])
        self.assertEqual(result["active_paid_subscriptions"], 0)

    def test_active_subscription_with_unknown_price_fails_closed(self):
        result = assess_subscriptions({"success": True, "result": [
            {"state": "Provisioned", "product_id": "workers"},
        ]})
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_subscription_price_unknown")

    def test_unknown_or_malformed_billing_data_fails_closed(self):
        for payload in ({}, {"success": False, "result": []}, {"success": True, "result": {}}):
            with self.subTest(payload=payload):
                self.assertFalse(assess_subscriptions(payload)["eligible"])
        self.assertFalse(check_boundary("", "synthetic-token")["eligible"])
        self.assertFalse(check_boundary("account-id", "")["eligible"])

    def test_authenticated_subscription_read_is_bounded_and_does_not_follow_redirects(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit):
                return json.dumps({"success": True, "result": []}).encode("utf-8")

        seen = {}
        def opener(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return Response()

        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(seen["request"].full_url,
                         "https://api.cloudflare.com/client/v4/accounts/synthetic-account/subscriptions")
        self.assertEqual(seen["request"].get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(seen["timeout"], 15)

        handler = _RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://api.cloudflare.com/client/v4/accounts/synthetic-account/subscriptions"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
