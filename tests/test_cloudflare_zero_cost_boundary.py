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
    activity_annotation,
    assess_subscriptions,
    check_boundary,
    fetch_subscription_inventory,
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

    def test_request_uses_the_documented_single_page_contract(self):
        """The endpoint is documented as SinglePage: no page/per_page parameters.

        Sending undocumented pagination parameters is the observed cause of the
        HTTP 400 that kept zero-cost eligibility false. This locks the shape of
        the request, not just the shape of the response handling.
        """
        free = {"id": "sub-free", "state": "Provisioned", "price": 0,
                "rate_plan": {"id": "free", "public_name": "Workers Free"}}
        seen = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(page([free])).encode()
        def opener(request, timeout):
            seen.append((request, timeout))
            return Response()
        result = check_boundary("synthetic-account", "synthetic-token", opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(len(seen), 1, "a SinglePage inventory must be read in exactly one request")
        url = seen[0][0].full_url
        self.assertEqual(url, "https://api.cloudflare.com/client/v4/accounts/synthetic-account/subscriptions")
        self.assertEqual(urlparse(url).query, "")
        self.assertEqual(seen[0][0].get_method(), "GET")
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer synthetic-token")

    def test_absent_result_info_is_accepted_because_it_is_documented_optional(self):
        """result_info is optional for this endpoint; the result array is the inventory."""
        body = {"success": True, "result": [
            {"id": "sub-free", "state": "Provisioned", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Workers Free"}},
        ]}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(body).encode()
        result = check_boundary("account", "token", opener=lambda *_a, **_k: Response())
        self.assertTrue(result["eligible"])

    def test_metadata_that_contradicts_the_returned_inventory_fails_closed(self):
        """A single page cannot be completed by requesting more, so inconsistency blocks."""
        free = {"id": "sub-free", "state": "Provisioned", "price": 0,
                "rate_plan": {"id": "free", "public_name": "Workers Free"}}
        cancelled = {"id": "sub-cancelled", "state": "Cancelled"}
        cases = {
            # total_count larger than what one response returned
            "incomplete_subscription_inventory": page([free], total=51, per_page=50),
            # count disagrees with len(result)
            "incomplete_subscription_inventory": {
                "success": True, "result": [free],
                "result_info": {"count": 49, "page": 1, "per_page": 50, "total_count": 49},
            },
            # a SinglePage response claiming to be a later page
            "unexpected_subscription_page": page([free], number=2),
        }
        for reason, body in cases.items():
            with self.subTest(reason=reason):
                class Response:
                    status = 200
                    def __init__(self, payload): self.payload = payload
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def read(self, limit): return json.dumps(self.payload).encode()
                result = check_boundary("account", "token",
                                        opener=lambda *_a, _r=Response(body), **_k: _r)
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], reason)
        # more records than the declared page size
        overfull = page([free, cancelled], per_page=1)
        class OverfullResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(overfull).encode()
        self.assertFalse(check_boundary("account", "token",
                                        opener=lambda *_a, **_k: OverfullResponse())["eligible"])

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

    def test_malformed_or_missing_credentials_fail_closed(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        self.assertFalse(check_boundary("account", "token", opener=lambda *_a, **_k: Response({
            "success": True, "result": [],
        }))["eligible"])
        self.assertFalse(check_boundary("account", "token", opener=lambda *_a, **_k: Response({
            "success": True, "result": "not-a-list",
        }))["eligible"])
        self.assertFalse(check_boundary("account", "token", opener=lambda *_a, **_k: Response({
            "success": True, "result": [], "result_info": "not-an-object",
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
        with self.assertRaises(ValueError):
            fetch_subscription_inventory("", "")

    def test_synthetic_http_400_keeps_the_gate_false(self):
        """The exact historical failure: undocumented pagination produced a 400."""
        def fail(request, timeout):
            raise HTTPError(request.full_url, 400, "synthetic request body", {}, None)
        result = check_boundary("synthetic-account", "synthetic-token", opener=fail)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "billing_http_400")
        self.assertNotIn("synthetic", json.dumps(result))

    def test_duplicate_or_missing_subscription_ids_fail_closed(self):
        duplicate = page([
            {"id": "same", "state": "Cancelled"},
            {"id": "same", "state": "Expired"},
        ])
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return json.dumps(self.body).encode()
        result = check_boundary("account", "token", opener=lambda *_a, **_k: Response(duplicate))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "missing_or_duplicate_subscription_id")
        missing_id_page = page([{"state": "Cancelled"}], total=1, per_page=1)
        self.assertFalse(check_boundary("account", "token",
                                        opener=lambda *_a, **_k: Response(missing_id_page))["eligible"])

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

    def test_activity_annotation_publishes_only_a_fixed_reason_token(self):
        """Step logs live in GitHub's results blob store and are not always
        retrievable, so the reason must also be readable from the check API."""
        text = activity_annotation({"eligible": False, "reason": "billing_http_400"})
        self.assertTrue(text.startswith("::error title=Cloudflare zero-cost preflight::"))
        self.assertIn("eligible=false", text)
        self.assertIn("reason=billing_http_400", text)
        self.assertEqual(len(text.splitlines()), 1)
        allowed = activity_annotation({"eligible": True, "reason": "no_active_paid_subscriptions"})
        self.assertIn("::notice", allowed)
        self.assertIn("eligible=true", allowed)

    def test_activity_annotation_refuses_anything_that_is_not_a_safe_token(self):
        for reason in ("", "Billing HTTP 400", "reason\n::error::injected",
                       "::warning::injected", "a" * 65, None, 400):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                activity_annotation({"eligible": False, "reason": reason})

    def test_main_emits_the_outcome_annotation_on_the_variable_paths(self):
        import contextlib
        import io
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {"CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                   "CLOUDFLARE_API_TOKEN": "synthetic-token",
                   "GITHUB_OUTPUT": output.name}
            for eligible, reason, command in (
                (False, "billing_http_400", "::error"),
                (True, "no_active_paid_subscriptions", "::notice"),
            ):
                with self.subTest(reason=reason), patch.dict(os.environ, env, clear=False), patch(
                    "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                    return_value={"eligible": eligible, "reason": reason,
                                  "active_paid_subscriptions": 0 if eligible else None},
                ):
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        code = main()
                    self.assertEqual(code, 0 if eligible else 1)
                    self.assertIn(f"{command} title=Cloudflare zero-cost preflight::", buffer.getvalue())
                    self.assertNotIn("synthetic", buffer.getvalue())

    def test_redirects_are_rejected(self):
        handler = _RejectRedirects()
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                Request("https://api.cloudflare.com/client/v4/accounts/account/subscriptions"),
                None, 302, "Found", {}, "https://attacker.example/collect",
            )


if __name__ == "__main__":
    unittest.main()
