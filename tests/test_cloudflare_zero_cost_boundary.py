import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from scripts.verify_cloudflare_zero_cost_boundary import (
    _RejectRedirects,
    assess_subscriptions,
    check_boundary,
    main,
)


def page(records, number=1, total=None, per_page=50):
    return {
        "success": True,
        "result": records,
        "result_info": {
            "count": len(records), "page": number, "per_page": per_page,
            "total_count": len(records) if total is None else total,
        },
    }


class ZeroCostBoundaryTests(unittest.TestCase):
    def test_empty_complete_subscription_inventory_is_eligible(self):
        self.assertTrue(assess_subscriptions([])["eligible"])

    def test_active_paid_subscription_blocks_staging(self):
        result = assess_subscriptions([{
            "state": "Paid", "price": 5, "rate_plan": {"id": "workers_paid"},
        }])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_subscription_present")
        self.assertEqual(result["active_paid_subscriptions"], 1)

    def test_only_explicit_free_zero_price_active_subscription_is_eligible(self):
        self.assertTrue(assess_subscriptions([{
            "state": "Provisioned", "price": 0, "rate_plan": {"id": "free"},
        }])["eligible"])
        self.assertFalse(assess_subscriptions([{
            "state": "Provisioned", "price": 0, "rate_plan": {"id": "paid_trial"},
        }])["eligible"])

    def test_cancelled_and_expired_subscriptions_are_inactive(self):
        result = assess_subscriptions([
            {"state": "Cancelled", "rate_plan": {"id": "paid"}},
            {"state": "Expired", "rate_plan": {"id": "paid"}},
            {"state": "Failed", "rate_plan": {"id": "paid"}},
        ])
        self.assertTrue(result["eligible"])

    def test_unknown_state_plan_and_malformed_prices_fail_closed(self):
        bad_records = [
            {"state": "mystery", "price": 0, "rate_plan": {"id": "free"}},
            {"state": "Paid", "price": 0},
            {"state": "Paid", "price": 0, "rate_plan": {}},
            {"state": "Paid", "price": -1, "rate_plan": {"id": "free"}},
            {"state": "Paid", "price": float("nan"), "rate_plan": {"id": "free"}},
            {"state": "Paid", "price": float("inf"), "rate_plan": {"id": "free"}},
            {"state": "Paid", "price": True, "rate_plan": {"id": "free"}},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                self.assertFalse(assess_subscriptions([record])["eligible"])
        self.assertFalse(assess_subscriptions([None])["eligible"])

    def test_pagination_is_followed_until_the_complete_inventory_is_read(self):
        seen = []
        responses = [
            page([{"state": "Cancelled"}], number=1, total=2),
            page([{"state": "Expired"}], number=2, total=2),
        ]
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        def opener(request, timeout):
            seen.append((request, timeout))
            return Response(responses[len(seen) - 1])
        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(parse_qs(urlparse(seen[0][0].full_url).query), {"page": ["1"], "per_page": ["50"]})
        self.assertEqual(parse_qs(urlparse(seen[1][0].full_url).query), {"page": ["2"], "per_page": ["50"]})
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer synthetic-token")

    def test_missing_or_incomplete_pagination_fails_closed(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        incomplete = page([{"state": "Cancelled"}], number=1, total=2)
        calls = 0
        def opener(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return Response(incomplete)
            raise HTTPError(request.full_url, 500, "synthetic", {}, None)
        self.assertFalse(check_boundary("account", "token", opener=opener)["eligible"])
        self.assertFalse(check_boundary("account", "token", opener=lambda *_a, **_k: Response({
            "success": True, "result": [],
        }))["eligible"])
        self.assertFalse(check_boundary("", "token")["eligible"])
        self.assertFalse(check_boundary("account", "")["eligible"])

    def test_main_fails_ci_when_zero_cost_eligibility_is_unknown(self):
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
                "GITHUB_OUTPUT": output.name,
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                return_value={"eligible": False, "reason": "billing_inventory_unavailable_or_incomplete",
                              "active_paid_subscriptions": None},
            ):
                self.assertEqual(main(), 1)
            output.seek(0)
            self.assertEqual(output.read(), "zero_cost_eligible=false\n")

    def test_main_succeeds_only_when_zero_cost_eligibility_is_proven(self):
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
                "GITHUB_OUTPUT": output.name,
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                return_value={"eligible": True, "reason": "no_active_paid_subscriptions",
                              "active_paid_subscriptions": 0},
            ):
                self.assertEqual(main(), 0)
            output.seek(0)
            self.assertEqual(output.read(), "zero_cost_eligible=true\n")

    def test_duplicate_or_missing_subscription_ids_fail_closed(self):
        duplicate_pages = [
            page([{"id": "same", "state": "Cancelled"}], number=1, total=2),
            page([{"id": "same", "state": "Expired"}], number=2, total=2),
        ]
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        seen = 0
        def duplicate_opener(request, timeout):
            nonlocal seen
            body = duplicate_pages[seen]
            seen += 1
            return Response(body)
        self.assertFalse(check_boundary("account", "token", opener=duplicate_opener)["eligible"])
        self.assertFalse(assess_subscriptions([{"state": "Cancelled"}])["eligible"])

    def test_redirects_are_rejected(self):
        handler = _RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://api.cloudflare.com/client/v4/accounts/account/subscriptions"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
