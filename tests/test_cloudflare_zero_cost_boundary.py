import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request

from scripts.verify_cloudflare_zero_cost_boundary import (
    _RejectRedirects,
    assess_subscriptions,
    check_boundary,
    main,
)


def page(records, number=1, total=None, per_page=1000):
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

    def test_cancelled_and_expired_subscriptions_do_not_prove_an_active_free_plan(self):
        result = assess_subscriptions([
            {"id": "sub-cancelled", "state": "Cancelled", "rate_plan": {"id": "paid"}},
            {"id": "sub-expired", "state": "Expired", "rate_plan": {"id": "paid"}},
            {"id": "sub-failed", "state": "Failed", "rate_plan": {"id": "paid"}},
        ])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "no_active_free_plan_verified")

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

    def test_subscription_endpoint_uses_one_documented_request_without_query_params(self):
        seen = []
        records = [
            {"id": f"sub-{index}", "state": "Cancelled"}
            for index in range(50)
        ]
        records.append({
            "id": "sub-free", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free", "public_name": "Workers Free"},
        })
        body = page(records, number=1, total=51, per_page=1000)

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(body).encode()

        def opener(request, timeout):
            seen.append((request, timeout))
            return Response()

        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(urlparse(seen[0][0].full_url).query, "")
        self.assertTrue(seen[0][0].full_url.endswith("/accounts/synthetic-account/subscriptions"))
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer synthetic-token")

    def test_subscription_inventory_metadata_must_prove_full_single_page(self):
        for info in (
            {"count": 1, "page": 1, "per_page": 20, "total_count": 2},
            {"count": 0, "page": 1, "per_page": 20, "total_count": 1},
            {"count": 1, "page": 2, "per_page": 20, "total_count": 1},
            {"count": 1, "page": 1, "per_page": 0, "total_count": 1},
            {"count": 1, "page": 1, "per_page": 20},
        ):
            with self.subTest(info=info):
                body = {
                    "success": True,
                    "result": [{
                        "id": "sub-free", "state": "Provisioned", "price": 0,
                        "rate_plan": {"id": "free", "public_name": "Workers Free"},
                    }],
                    "result_info": info,
                }

                class Response:
                    status = 200
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def read(self, limit): return json.dumps(body).encode()

                result = check_boundary(
                    "synthetic-account", "synthetic-token",
                    opener=lambda *_a, **_k: Response(),
                )
                self.assertFalse(result["eligible"])
                self.assertIn(result["reason"], {
                    "invalid_subscription_inventory_metadata",
                    "subscription_inventory_page_mismatch",
                    "incomplete_subscription_inventory",
                })

    def test_single_page_contract_accepts_missing_optional_result_info(self):
        body = {
            "success": True,
            "result": [{
                "id": "sub-free", "state": "Provisioned", "price": 0,
                "rate_plan": {"id": "free", "public_name": "Workers Free"},
            }],
        }

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(body).encode()

        result = check_boundary(
            "synthetic-account", "synthetic-token",
            opener=lambda *_a, **_k: Response(),
        )
        self.assertTrue(result["eligible"])

    def test_integer_valued_numeric_pagination_metadata_is_accepted(self):
        body = page([{"id": "sub-free", "state": "Provisioned", "price": 0,
                      "rate_plan": {"id": "free", "public_name": "Workers Free"}}],
                    number=1.0, total=1.0, per_page=20.0)
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(body).encode()
        result = check_boundary("account", "token", opener=lambda *_a, **_k: Response())
        self.assertTrue(result["eligible"])

    def test_fractional_or_nonfinite_pagination_metadata_fails_closed(self):
        for field, value in (("page", 1.5), ("count", float("nan")), ("per_page", float("inf"))):
            with self.subTest(field=field):
                body = page([{"id": "sub-free", "state": "Cancelled"}], total=1)
                body["result_info"][field] = value
                class Response:
                    status = 200
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def read(self, limit): return json.dumps(body).encode()
                result = check_boundary("account", "token", opener=lambda *_a, **_k: Response())
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], "invalid_pagination_" + field)

    def test_missing_or_incomplete_inventory_fails_closed_without_leaking_response(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()

        incomplete = page([{"id": "sub-cancelled", "state": "Cancelled"}], total=2)
        self.assertFalse(check_boundary(
            "account", "token",
            opener=lambda *_a, **_k: Response(incomplete),
        )["eligible"])
        self.assertFalse(check_boundary(
            "account", "token",
            opener=lambda *_a, **_k: Response({"success": True, "result": []}),
        )["eligible"])
        invalid_json = check_boundary(
            "account", "token",
            opener=lambda *_a, **_k: type("InvalidJsonResponse", (), {
                "status": 200, "__enter__": lambda self: self,
                "__exit__": lambda self, *args: False,
                "read": lambda self, limit: b'{"private_account_detail":"must-not-leak"',
            })(),
        )
        self.assertFalse(invalid_json["eligible"])
        self.assertEqual(invalid_json["reason"], "invalid_billing_json")
        self.assertNotIn("private_account_detail", json.dumps(invalid_json))
        self.assertFalse(check_boundary("", "token")["eligible"])
        self.assertFalse(check_boundary("account", "")["eligible"])

    def test_duplicate_or_missing_subscription_ids_fail_closed(self):
        duplicate_records = [
            {"id": "same", "state": "Cancelled"},
            {"id": "same", "state": "Expired"},
        ]

        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()

        duplicate_body = {"success": True, "result": duplicate_records}
        self.assertFalse(check_boundary(
            "account", "token",
            opener=lambda *_a, **_k: Response(duplicate_body),
        )["eligible"])
        missing_id_body = {"success": True, "result": [{"state": "Cancelled"}]}
        self.assertFalse(check_boundary(
            "account", "token",
            opener=lambda *_a, **_k: Response(missing_id_body),
        )["eligible"])

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

    def test_http_failures_expose_only_safe_status_codes(self):
        for code in (401, 403, 404, 429, 500):
            with self.subTest(code=code):
                def fail(_request, timeout):
                    raise HTTPError("https://api.cloudflare.com/private", code,
                                    "private response body", {}, None)
                result = check_boundary("synthetic-account", "synthetic-token", opener=fail)
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], f"billing_http_{code}")
                self.assertNotIn("private", json.dumps(result))
                self.assertNotIn("synthetic", json.dumps(result))

    def test_redirects_are_rejected(self):
        handler = _RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://api.cloudflare.com/client/v4/accounts/account/subscriptions"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
