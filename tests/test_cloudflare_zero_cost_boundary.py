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
    def test_empty_subscription_inventory_is_not_proof_of_zero_cost(self):
        result = assess_subscriptions([])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "empty_subscription_inventory_unverified")

    def test_active_paid_subscription_blocks_staging(self):
        result = assess_subscriptions([{
            "id": "sub-paid", "state": "Paid", "price": 5, "rate_plan": {"id": "workers_paid"},
        }])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_subscription_present")
        self.assertEqual(result["active_paid_subscriptions"], 1)

    def test_only_explicit_free_zero_price_active_subscription_is_eligible(self):
        self.assertTrue(assess_subscriptions([{
            "id": "sub-free", "state": "Provisioned", "price": 0, "rate_plan": {"id": "free", "public_name": "Workers Free"},
        }])["eligible"])
        self.assertFalse(assess_subscriptions([{
            "id": "sub-trial", "state": "Provisioned", "price": 0, "rate_plan": {"id": "paid_trial", "public_name": "Workers Paid"},
        }])["eligible"])
        for state in ("Trial", "AwaitingPayment", "Paid"):
            with self.subTest(state=state):
                self.assertFalse(assess_subscriptions([{
                    "id": "sub-state", "state": state, "price": 0, "rate_plan": {"id": "free", "public_name": "Workers Free"},
                }])["eligible"])

    def test_free_rate_plan_requires_explicit_free_public_name(self):
        self.assertTrue(assess_subscriptions([{
            "id": "sub-free", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free", "public_name": "Workers Free"},
        }])["eligible"])
        self.assertFalse(assess_subscriptions([{
            "id": "sub-business", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free", "public_name": "Business Plan"},
        }])["eligible"])

    def test_cancelled_and_expired_subscriptions_are_inactive(self):
        result = assess_subscriptions([
            {"id": "sub-cancelled", "state": "Cancelled", "rate_plan": {"id": "paid"}},
            {"id": "sub-expired", "state": "Expired", "rate_plan": {"id": "paid"}},
            {"id": "sub-failed", "state": "Failed", "rate_plan": {"id": "paid"}},
        ])
        self.assertTrue(result["eligible"])

    def test_unknown_state_plan_and_malformed_prices_fail_closed(self):
        bad_records = [
            {"id": "sub-unknown", "state": "mystery", "price": 0, "rate_plan": {"id": "free"}},
            {"id": "sub-missing-plan", "state": "Paid", "price": 0},
            {"id": "sub-empty-plan", "state": "Paid", "price": 0, "rate_plan": {}},
            {"id": "sub-free-id-business-name", "state": "Provisioned", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Business Plan"}},
            {"id": "sub-negative", "state": "Paid", "price": -1, "rate_plan": {"id": "free"}},
            {"id": "sub-nan", "state": "Paid", "price": float("nan"), "rate_plan": {"id": "free"}},
            {"id": "sub-inf", "state": "Paid", "price": float("inf"), "rate_plan": {"id": "free"}},
            {"id": "sub-bool", "state": "Paid", "price": True, "rate_plan": {"id": "free"}},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                self.assertFalse(assess_subscriptions([record])["eligible"])
        self.assertFalse(assess_subscriptions([None])["eligible"])

    def test_pagination_is_followed_until_the_complete_inventory_is_read(self):
        seen = []
        first_page = [{"id": f"sub-{index}", "state": "Cancelled"} for index in range(50)]
        second_page = [{"id": "sub-50", "state": "Expired"}]
        responses = [
            page(first_page, number=1, total=51, per_page=50),
            page(second_page, number=2, total=51, per_page=50),
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
        self.assertEqual(result["reason"], "no_active_paid_subscriptions")
        self.assertTrue(result["eligible"])
        self.assertEqual(len(seen), 2)
        self.assertEqual(parse_qs(urlparse(seen[0][0].full_url).query), {"page": ["1"], "per_page": ["50"]})
        self.assertEqual(parse_qs(urlparse(seen[1][0].full_url).query), {"page": ["2"], "per_page": ["50"]})
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer synthetic-token")

    def test_integral_numeric_pagination_metadata_accepts_cloudflare_number_schema(self):
        record = {"id": "sub-cancelled", "state": "Cancelled"}
        payload = page([record], number=1, total=1, per_page=50)
        payload["result_info"] = {key: float(value) for key, value in payload["result_info"].items()}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(payload).encode()

        result = check_boundary(
            "synthetic-account", "synthetic-token",
            opener=lambda request, timeout: Response(),
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "no_active_paid_subscriptions")

    def test_partial_nonfinal_page_fails_closed(self):
        partial = page(
            [{"id": f"sub-{index}", "state": "Cancelled"} for index in range(49)],
            number=1, total=51, per_page=50,
        )
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(partial).encode()
        result = check_boundary(
            "synthetic-account", "synthetic-token",
            opener=lambda request, timeout: Response(),
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "incomplete_subscription_page")

    def test_missing_or_incomplete_pagination_fails_closed(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        incomplete = page([{"id": "sub-1", "state": "Cancelled"}], number=1, total=2, per_page=1)
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
        class InvalidJsonResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b'{"private_account_detail":"must-not-leak"'
        invalid_json = check_boundary("account", "token", opener=lambda *_a, **_k: InvalidJsonResponse())
        self.assertFalse(invalid_json["eligible"])
        self.assertEqual(invalid_json["reason"], "invalid_billing_json")
        self.assertNotIn("private_account_detail", json.dumps(invalid_json))
        self.assertFalse(check_boundary("", "token")["eligible"])
        self.assertFalse(check_boundary("account", "")["eligible"])

    def test_duplicate_or_missing_subscription_ids_fail_closed(self):
        duplicate_pages = [
            page([{"id": "same", "state": "Cancelled"}], number=1, total=2, per_page=1),
            page([{"id": "same", "state": "Expired"}], number=2, total=2, per_page=1),
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
        missing_id_page = page([{"state": "Cancelled"}], number=1, total=1, per_page=1)
        self.assertFalse(check_boundary("account", "token", opener=lambda *_a, **_k: Response(missing_id_page))["eligible"])

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

    def test_redirects_are_rejected(self):
        handler = _RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://api.cloudflare.com/client/v4/accounts/account/subscriptions"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
