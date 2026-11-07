import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from scripts.verify_cloudflare_zero_cost_boundary import (
    MAX_RESPONSE_BYTES,
    _RejectRedirects,
    activity_annotation,
    assess_subscriptions,
    assess_zone_subscription,
    check_boundary,
    fetch_subscription_inventory,
    fetch_zone_inventory,
    main,
)


ACCOUNT = "synthetic-account"
TOKEN = "synthetic-token"


def subscription_payload(records, *, result_info=True):
    payload = {"success": True, "result": records}
    if result_info:
        payload["result_info"] = {
            "count": len(records), "page": 1, "per_page": 20,
            "total_count": len(records),
        }
    return payload


def zone_payload(zones, *, count=None, total=None, page=1, per_page=50, total_pages=None):
    return {
        "success": True,
        "result": zones,
        "result_info": {
            "count": len(zones) if count is None else count,
            "page": page,
            "per_page": per_page,
            "total_count": len(zones) if total is None else total,
            "total_pages": (0 if not zones else 1) if total_pages is None else total_pages,
        },
    }


def free_addon():
    return {
        "id": "addon-free", "state": "Provisioned", "price": 0,
        "rate_plan": {"id": "free", "public_name": "Workers Free"},
    }


def free_zone():
    return {"id": "zone-1", "name": "example.test",
            "account": {"id": ACCOUNT, "name": "Synthetic Account"}, "status": "active"}


def free_zone_subscription():
    return {
        "id": "zone-sub-1",
        "price": 0,
        "state": "Provisioned",
        "rate_plan": {
            "id": "free", "public_name": "Free Plan", "scope": "zone",
            "externally_managed": False, "is_contract": False,
        },
    }


class Response:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return json.dumps(self.body).encode("utf-8")


class ZeroCostBoundaryTests(unittest.TestCase):
    def test_empty_addon_inventory_is_allowed_only_after_zone_inventory_is_checked(self):
        # #682 recorded that an empty add-on list is not proof of a Free
        # account, so it must not be eligible on its own. This assertion
        # previously claimed the opposite, which contradicted the documented
        # finding; the list-level answer is "unknown", and only the zone
        # inventory can turn that into positive evidence.
        self.assertFalse(assess_subscriptions([])["eligible"])
        self.assertEqual(assess_subscriptions([])["reason"],
                         "empty_subscription_inventory_unverified")
        zones = zone_payload([])
        def opener(request, timeout):
            if "/accounts/" in request.full_url:
                return Response(subscription_payload([]))
            if "/zones?" in request.full_url:
                return Response(zones)
            raise AssertionError("unexpected request " + request.full_url)
        result = check_boundary(ACCOUNT, TOKEN, opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["zones_checked"], 0)
        self.assertEqual(result["active_paid_zone_plans"], 0)
        # Cloudflare has emitted both 0 and 1 total_pages for an empty result.
        empty_variant = zone_payload([], total_pages=1)
        self.assertEqual(
            fetch_zone_inventory(ACCOUNT, TOKEN, opener=lambda *_a, **_k: Response(empty_variant)),
            [],
        )

    def test_active_paid_addon_blocks_before_zone_reads(self):
        paid = [{"id": "workers-paid", "state": "Paid", "price": 5,
                 "rate_plan": {"id": "workers_paid", "public_name": "Workers Paid"}}]
        seen = []
        def opener(request, timeout):
            seen.append(request.full_url)
            return Response(subscription_payload(paid))
        result = check_boundary(ACCOUNT, TOKEN, opener=opener)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_subscription_present")
        self.assertEqual(len(seen), 1)

    def test_explicit_zero_price_free_addon_is_allowed(self):
        self.assertTrue(assess_subscriptions([free_addon()])["eligible"])
        without_optional_name = {
            "id": "addon-free-no-name", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free"},
        }
        self.assertTrue(assess_subscriptions([without_optional_name])["eligible"])

    def test_unknown_or_paid_addon_states_fail_closed(self):
        cases = [
            {"id": "trial", "state": "Trial", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Workers Free"}},
            {"id": "awaiting", "state": "AwaitingPayment", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Workers Free"}},
            {"id": "paid", "state": "Paid", "price": 5,
             "rate_plan": {"id": "workers_paid", "public_name": "Workers Paid"}},
            {"id": "unknown", "state": "Provisioned", "price": 0,
             "rate_plan": {"id": "unknown", "public_name": "Unknown Plan"}},
            {"id": "external", "state": "Provisioned", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Workers Free", "externally_managed": True}},
            {"id": "contract", "state": "Provisioned", "price": 0,
             "rate_plan": {"id": "free", "public_name": "Workers Free", "is_contract": True}},
            {"id": "bad", "state": "Provisioned", "price": float("nan"),
             "rate_plan": {"id": "free", "public_name": "Workers Free"}},
        ]
        for record in cases:
            with self.subTest(record=record):
                self.assertFalse(assess_subscriptions([record])["eligible"])

    def test_account_subscriptions_request_uses_documented_single_page_path(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            return Response(subscription_payload([]))
        self.assertEqual(fetch_subscription_inventory(ACCOUNT, TOKEN, opener=opener), [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].full_url,
                         f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/subscriptions")
        self.assertEqual(urlparse(seen[0].full_url).query, "")
        self.assertEqual(seen[0].get_method(), "GET")
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer " + TOKEN)

    def test_optional_result_info_is_accepted_for_single_page_addon_endpoint(self):
        self.assertEqual(fetch_subscription_inventory(
            ACCOUNT, TOKEN,
            opener=lambda *_a, **_k: Response(subscription_payload([], result_info=False)),
        ), [])

    def test_account_subscription_metadata_mismatch_fails_closed(self):
        body = subscription_payload([])
        body["result_info"]["count"] = 1
        with self.assertRaisesRegex(ValueError, "incomplete_subscription_inventory"):
            fetch_subscription_inventory(ACCOUNT, TOKEN, opener=lambda *_a, **_k: Response(body))

    def test_account_subscription_total_count_mismatch_fails_closed(self):
        body = subscription_payload([])
        body["result_info"]["total_count"] = 1
        with self.assertRaisesRegex(ValueError, "incomplete_subscription_inventory"):
            fetch_subscription_inventory(ACCOUNT, TOKEN, opener=lambda *_a, **_k: Response(body))

    def test_negative_pagination_metadata_fails_closed(self):
        for name in ("count", "page", "per_page", "total_count"):
            body = subscription_payload([])
            body["result_info"][name] = -1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid_pagination_" + name):
                fetch_subscription_inventory(ACCOUNT, TOKEN, opener=lambda *_a, **_k: Response(body))

    def test_zone_inventory_uses_documented_account_filter_and_pagination(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            return Response(zone_payload([free_zone()]))
        zones = fetch_zone_inventory(ACCOUNT, TOKEN, opener=opener)
        self.assertEqual(zones, [free_zone()])
        self.assertEqual(len(seen), 1)
        parsed = urlparse(seen[0].full_url)
        self.assertEqual(parsed.path, "/client/v4/zones")
        query = parse_qs(parsed.query)
        self.assertEqual(query["account.id"], [ACCOUNT])
        self.assertEqual(query["page"], ["1"])
        self.assertEqual(query["per_page"], ["50"])
        self.assertEqual(query["type"], ["full,partial,secondary,internal"])

    def test_zone_inventory_requires_count_metadata_and_blocks_a_full_page(self):
        incomplete = zone_payload([free_zone()])
        incomplete["result_info"].pop("count")
        with self.assertRaisesRegex(ValueError, "zone_inventory_incomplete"):
            fetch_zone_inventory(
                ACCOUNT, TOKEN,
                opener=lambda *_a, **_k: Response(incomplete),
            )
        full_page = [dict(free_zone(), id=f"zone-{i}") for i in range(50)]
        with self.assertRaisesRegex(ValueError, "too_many_zones_to_verify"):
            fetch_zone_inventory(
                ACCOUNT, TOKEN,
                opener=lambda *_a, **_k: Response(zone_payload(full_page, total=2000, per_page=50, total_pages=40)),
            )

    def test_zone_inventory_ignores_unfiltered_total_count_metadata(self):
        zones = [free_zone()]
        result = fetch_zone_inventory(
            ACCOUNT, TOKEN,
            opener=lambda *_a, **_k: Response(zone_payload(zones, total=2000, per_page=50, total_pages=40)),
        )
        self.assertEqual(result, zones)

    def test_zone_account_mismatch_and_duplicate_ids_fail_closed(self):
        wrong = dict(free_zone(), account={"id": "another-account"})
        with self.assertRaisesRegex(ValueError, "zone_account_mismatch"):
            fetch_zone_inventory(
                ACCOUNT, TOKEN,
                opener=lambda *_a, **_k: Response(zone_payload([wrong])),
            )
        duplicate = [free_zone(), free_zone()]
        with self.assertRaisesRegex(ValueError, "missing_or_duplicate_zone_id"):
            fetch_zone_inventory(
                ACCOUNT, TOKEN,
                opener=lambda *_a, **_k: Response(zone_payload(duplicate)),
            )

    def test_explicit_zero_price_free_zone_plan_is_eligible(self):
        self.assertTrue(assess_zone_subscription(free_zone_subscription())["eligible"])

    def test_paid_unknown_trial_external_or_malformed_zone_plan_blocks(self):
        cases = [
            (dict(free_zone_subscription(), price=20), "active_paid_zone_plan"),
            ({**free_zone_subscription(), "rate_plan": {"id": "pro", "public_name": "Pro Plan"}}, "active_paid_zone_plan"),
            ({**free_zone_subscription(), "state": "Trial"}, "zone_plan_state_unverified"),
            ({**free_zone_subscription(), "rate_plan": {"id": "free", "public_name": "Business Plan"}}, "unknown_zone_plan"),
            ({**free_zone_subscription(), "rate_plan": {"id": "free", "public_name": "Free Plan", "externally_managed": True}}, "zone_plan_external"),
            ({**free_zone_subscription(), "rate_plan": {"id": "free", "public_name": "Free Plan", "is_contract": True}}, "zone_plan_external"),
            ({**free_zone_subscription(), "rate_plan": {"id": "free", "public_name": "Free Plan", "scope": "account"}}, "zone_plan_scope_mismatch"),
            ({**free_zone_subscription(), "price": float("nan")}, "active_paid_zone_plan"),
            ({"price": 0, "state": "Provisioned"}, "unknown_zone_plan"),
        ]
        for subscription, reason in cases:
            with self.subTest(subscription=subscription):
                result = assess_zone_subscription(subscription)
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], reason)

    def test_check_boundary_requires_all_zones_to_be_explicitly_free(self):
        seen = []
        def opener(request, timeout):
            url = request.full_url
            seen.append(url)
            if "/accounts/" in url:
                return Response(subscription_payload([]))
            if "/zones?" in url:
                return Response(zone_payload([free_zone()]))
            if url.endswith("/zones/zone-1/subscription"):
                return Response({"success": True, "result": free_zone_subscription()})
            raise AssertionError("unexpected request " + url)
        result = check_boundary(ACCOUNT, TOKEN, opener=opener)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason"], "all_addons_and_zone_plans_explicitly_free")
        self.assertEqual(result["zones_checked"], 1)
        self.assertEqual(len(seen), 3)

    def test_check_boundary_blocks_paid_zone_plan_even_when_addon_list_is_empty(self):
        paid_zone = dict(free_zone_subscription(), price=10,
                         rate_plan={"id": "pro", "public_name": "Pro Plan", "scope": "zone"})
        def opener(request, timeout):
            url = request.full_url
            if "/accounts/" in url:
                return Response(subscription_payload([]))
            if "/zones?" in url:
                return Response(zone_payload([free_zone()]))
            if url.endswith("/zones/zone-1/subscription"):
                return Response({"success": True, "result": paid_zone})
            raise AssertionError("unexpected request " + url)
        result = check_boundary(ACCOUNT, TOKEN, opener=opener)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "active_paid_zone_plan")
        self.assertEqual(result["zones_checked"], 1)

    def test_missing_zone_permission_or_api_error_fails_closed(self):
        def fail_zone_list(request, timeout):
            if "/accounts/" in request.full_url:
                return Response(subscription_payload([]))
            raise HTTPError(request.full_url, 403, "private body", {}, None)
        result = check_boundary(ACCOUNT, TOKEN, opener=fail_zone_list)
        self.assertFalse(result["eligible"])
        # The failing endpoint names itself. Reporting "billing_http_403" here
        # pointed at the billing endpoint, which had in fact answered 200.
        self.assertEqual(result["reason"], "zone_inventory_http_403")

    def test_each_endpoint_names_itself_in_the_failure_reason(self):
        """A zone-side 404 must not be reported as a billing 404.

        Observed live on 2026-11-07 (run 42329717569): the post-#685 preflight
        reported `billing_http_404` for three separate workflows. The billing
        endpoint had answered 200; the 404 came from the zone endpoints. One
        shared handler for every endpoint made the reason point at the wrong
        resource, so the reported cause could not be trusted.
        """
        free_addon = {
            "id": "addon-free", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free", "public_name": "Free Plan"},
        }

        def opener_for(billing, zone_inventory_status, zone_plan_status=200):
            def opener(request, timeout):
                url = request.full_url
                if url.split("?")[0].endswith("/subscriptions"):
                    if billing != 200:
                        raise HTTPError(url, billing, "private body", {}, None)
                    return Response(subscription_payload([free_addon]))
                if url.split("?")[0].endswith("/zones"):
                    if zone_inventory_status != 200:
                        raise HTTPError(url, zone_inventory_status, "private body", {}, None)
                    return Response(zone_payload([{"id": "zone-1", "name": "example.test",
                                                   "account": {"id": ACCOUNT}}]))
                if url.endswith("/subscription"):
                    if zone_plan_status != 200:
                        raise HTTPError(url, zone_plan_status, "private body", {}, None)
                    return Response({"success": True, "result": {
                        "price": 0, "state": "Provisioned",
                        "rate_plan": {"id": "free", "public_name": "Free Website Plan"},
                    }})
                raise AssertionError("unexpected request " + url)
            return opener

        cases = {
            ("billing", 404): "billing_http_404",
            ("billing", 403): "billing_http_403",
            ("zone_inventory", 404): "zone_inventory_http_404",
            ("zone_inventory", 403): "zone_inventory_http_403",
            ("zone_plan", 404): "zone_subscription_http_404",
        }
        for (where, status), expected in cases.items():
            with self.subTest(where=where, status=status):
                opener = opener_for(
                    status if where == "billing" else 200,
                    status if where == "zone_inventory" else 200,
                    status if where == "zone_plan" else 200,
                )
                result = check_boundary(ACCOUNT, TOKEN, opener=opener)
                self.assertFalse(result["eligible"])
                self.assertEqual(result["reason"], expected)
                # No response body, URL or account id may reach the reason.
                self.assertNotIn("private", json.dumps(result))
                self.assertNotIn(ACCOUNT, json.dumps(result))

    def test_empty_addon_list_still_passes_when_every_zone_plan_is_free(self):
        """The empty-inventory finding must not make a Free account unreachable."""
        free_addon = {
            "id": "addon-free", "state": "Provisioned", "price": 0,
            "rate_plan": {"id": "free", "public_name": "Free Plan"},
        }
        for addons in ([], [free_addon]):
            with self.subTest(addons=len(addons)):
                def opener(request, timeout):
                    url = request.full_url
                    if url.split("?")[0].endswith("/subscriptions"):
                        return Response(subscription_payload(addons))
                    if url.split("?")[0].endswith("/zones"):
                        return Response(zone_payload([{"id": "zone-1", "name": "example.test",
                                                       "account": {"id": ACCOUNT}}]))
                    if url.endswith("/subscription"):
                        return Response({"success": True, "result": {
                            "price": 0, "state": "Provisioned",
                            "rate_plan": {"id": "free", "public_name": "Free Website Plan"},
                        }})
                    raise AssertionError("unexpected request " + url)
                result = check_boundary(ACCOUNT, TOKEN, opener=opener)
                self.assertTrue(result["eligible"], result)
                self.assertEqual(result["zones_checked"], 1)
                self.assertEqual(result["active_paid_zone_plans"], 0)

    def test_response_size_and_invalid_json_fail_closed(self):
        class OversizedResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b"x" * (MAX_RESPONSE_BYTES + 1)
        result = check_boundary(ACCOUNT, TOKEN, opener=lambda *_a, **_k: OversizedResponse())
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "billing_response_too_large")

        class InvalidJsonResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b'{"private_account_detail":"must-not-leak"'
        result = check_boundary(ACCOUNT, TOKEN, opener=lambda *_a, **_k: InvalidJsonResponse())
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "invalid_billing_json")
        self.assertNotIn("private_account_detail", json.dumps(result))

    def test_main_fails_ci_when_zero_cost_eligibility_is_unknown(self):
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN,
                   "GITHUB_OUTPUT": output.name}
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary",
                return_value={"eligible": False, "reason": "zone_inventory_unavailable",
                              "active_paid_subscriptions": 0, "active_paid_zone_plans": None,
                              "zones_checked": 0},
            ):
                self.assertEqual(main(), 1)
            output.seek(0)
            self.assertEqual(output.read(), "zero_cost_eligible=false\n")

    def test_http_failures_expose_only_safe_status_codes(self):
        for code in (400, 401, 403, 404, 429, 500):
            with self.subTest(code=code):
                def fail(request, timeout):
                    raise HTTPError("https://api.cloudflare.com/private", code,
                                    "private response body", {}, None)
                result = check_boundary(ACCOUNT, TOKEN, opener=fail)
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


class ActivityAnnotationTests(unittest.TestCase):
    def test_activity_annotation_emits_only_a_fixed_reason_token(self):
        text = activity_annotation({"eligible": False, "reason": "zone_inventory_unavailable"})
        self.assertEqual(
            text,
            "::error title=Cloudflare zero-cost preflight::eligible=false; reason=zone_inventory_unavailable",
        )
        allowed = activity_annotation({"eligible": True, "reason": "all_addons_and_zone_plans_explicitly_free"})
        self.assertTrue(allowed.startswith("::notice "))
        self.assertIn("eligible=true", allowed)

    def test_activity_annotation_rejects_untrusted_reason_text(self):
        for reason in ("", "Billing API 400", "x\\n::error::injected", "::warning::injected", "a" * 65, None):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                activity_annotation({"eligible": False, "reason": reason})

    def test_main_publishes_sanitized_annotation_without_response_data(self):
        import contextlib
        import io
        with tempfile.NamedTemporaryFile(mode="r+", encoding="utf-8") as output:
            env = {
                "CLOUDFLARE_ACCOUNT_ID": "synthetic-account",
                "CLOUDFLARE_API_TOKEN": "synthetic-token",
                "GITHUB_OUTPUT": output.name,
            }
            result = {"eligible": False, "reason": "zone_inventory_unavailable",
                      "active_paid_subscriptions": None, "active_paid_zone_plans": None,
                      "zones_checked": 0}
            with patch.dict(os.environ, env, clear=False), patch(
                "scripts.verify_cloudflare_zero_cost_boundary.check_boundary", return_value=result
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    self.assertEqual(main(), 1)
            emitted = buffer.getvalue()
            self.assertIn("::error title=Cloudflare zero-cost preflight::", emitted)
            self.assertIn("reason=zone_inventory_unavailable", emitted)
            self.assertNotIn("synthetic-account", emitted)
            self.assertNotIn("synthetic-token", emitted)


if __name__ == "__main__":
    unittest.main()
