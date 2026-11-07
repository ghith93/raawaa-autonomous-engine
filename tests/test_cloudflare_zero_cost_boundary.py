import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from scripts.verify_cloudflare_zero_cost_boundary import (
    _RejectRedirects,
    assess_subscriptions,
    check_boundary,
    main,
)


def payload(items, *, page=1, per_page=50, total=None):
    return {
        "success": True,
        "result": items,
        "result_info": {
            "page": page,
            "per_page": per_page,
            "total_count": len(items) if total is None else total,
        },
    }


class Response:
    status = 200

    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.data[:limit]


class ZeroCostBoundaryTests(unittest.TestCase):
    def test_empty_complete_subscription_list_is_eligible(self):
        self.assertTrue(assess_subscriptions(payload([]))["eligible"])

    def test_only_provisioned_zero_price_subscription_is_eligible(self):
        result = assess_subscriptions(payload([{"state": "Provisioned", "price": 0}]))
        self.assertTrue(result["eligible"])

    def test_paid_subscription_blocks_staging(self):
        result = assess_subscriptions(payload([{"state": "Paid", "price": 5}]))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_subscription_present")

    def test_paid_state_blocks_even_if_reported_price_is_zero(self):
        result = assess_subscriptions(payload([{"state": "Paid", "price": 0}]))
        self.assertFalse(result["eligible"])

    def test_trial_and_awaiting_payment_block_even_if_price_is_zero(self):
        for state in ("Trial", "AwaitingPayment"):
            with self.subTest(state=state):
                self.assertFalse(assess_subscriptions(payload([{"state": state, "price": 0}]))["eligible"])

    def test_unknown_state_fails_closed(self):
        result = assess_subscriptions(payload([{"state": "NewCloudflareState", "price": 0}]))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_subscription_state_unknown")

    def test_cancelled_failed_and_expired_subscriptions_are_terminal(self):
        result = assess_subscriptions(payload([
            {"state": "Cancelled", "price": 5},
            {"state": "Failed", "price": 10},
            {"state": "Expired", "price": 15},
        ]))
        self.assertTrue(result["eligible"])

    def test_unknown_negative_boolean_and_non_finite_prices_fail_closed(self):
        invalid_prices = (None, -1, True, float("nan"), float("inf"))
        for price in invalid_prices:
            with self.subTest(price=price):
                result = assess_subscriptions(payload([{"state": "Provisioned", "price": price}]))
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], "active_subscription_price_unknown")

    def test_incomplete_or_malformed_listing_fails_closed(self):
        bad_payloads = (
            {},
            {"success": False, "result": [], "result_info": {"total_count": 0}},
            {"success": True, "result": {}, "result_info": {"total_count": 0}},
            {"success": True, "result": [None], "result_info": {"total_count": 1}},
            {"success": True, "result": [], "result_info": {"total_count": 1}},
            {"success": True, "result": [], "result_info": {"total_count": True}},
        )
        for value in bad_payloads:
            with self.subTest(value=value):
                self.assertFalse(assess_subscriptions(value)["eligible"])

    def test_check_boundary_reads_all_pages_before_allowing_staging(self):
        first = [{"state": "Provisioned", "price": 0, "id": f"sub-{i}"} for i in range(50)]
        second = [{"state": "Provisioned", "price": 0, "id": "sub-50"}]
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            page = int(request.full_url.split("page=")[1].split("&")[0])
            return Response(payload(first if page == 1 else second, page=page, total=51))

        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(len(seen), 2)
        self.assertIn("page=1&per_page=50", seen[0])
        self.assertIn("page=2&per_page=50", seen[1])

    def test_check_boundary_rejects_changed_or_incomplete_pagination(self):
        def opener(request, timeout):
            page = int(request.full_url.split("page=")[1].split("&")[0])
            if page == 1:
                return Response(payload([{"state": "Provisioned", "price": 0}], page=1, total=2))
            return Response(payload([], page=2, total=3))
        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertFalse(result["eligible"])
        self.assertIn(result["reason"], {"subscription_pagination_changed", "subscription_page_incomplete"})

    def test_missing_pagination_metadata_fails_closed(self):
        result = check_boundary(
            "synthetic-account",
            "synthetic-token",
            opener=lambda request, timeout: Response({"success": True, "result": []}),
        )
        self.assertFalse(result["eligible"])

    def test_credentials_are_required(self):
        self.assertFalse(check_boundary("", "synthetic-token")["eligible"])
        self.assertFalse(check_boundary("synthetic-account", "")["eligible"])

    def test_main_fails_ci_and_writes_false_output_when_boundary_is_unknown(self):
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
                "GITHUB_OUTPUT": output.name,
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                return_value={"eligible": False, "reason": "billing_api_unavailable", "active_paid_subscriptions": None},
            ):
                self.assertEqual(main(), 1)
            output.seek(0)
            self.assertEqual(output.read(), "zero_cost_eligible=false\n")

    def test_main_succeeds_only_when_boundary_is_eligible(self):
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
                "GITHUB_OUTPUT": output.name,
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                return_value={"eligible": True, "reason": "no_active_paid_subscriptions", "active_paid_subscriptions": 0},
            ):
                self.assertEqual(main(), 0)
            output.seek(0)
            self.assertEqual(output.read(), "zero_cost_eligible=true\n")

    def test_authenticated_subscription_read_is_bounded_and_rejects_redirects(self):
        seen = {}

        def opener(request, timeout):
            seen["request"] = request
            seen["timeout"] = timeout
            return Response(payload([]))

        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(
            seen["request"].full_url,
            "https://api.cloudflare.com/client/v4/accounts/synthetic-account/subscriptions?page=1&per_page=50",
        )
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
