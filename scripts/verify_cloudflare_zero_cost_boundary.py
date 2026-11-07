"""Read-only, fail-closed Cloudflare Workers/D1 Staging cost preflight.

Only a complete account subscription listing with no active paid, trial,
awaiting-payment, unknown-state, or unknown-price subscription can allow the
automatic Staging lifecycle. No account or billing data is printed.
"""
from __future__ import annotations

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/1.1"
MAX_RESPONSE_BYTES = 1024 * 1024
REQUESTED_PER_PAGE = 50
MAX_PAGES = 20
KNOWN_STATES = {"Trial", "Provisioned", "Paid", "AwaitingPayment", "Cancelled", "Failed", "Expired"}
TERMINAL_STATES = {"Cancelled", "Failed", "Expired"}
NON_PERMANENT_STATES = {"Trial", "AwaitingPayment", "Paid"}


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "billing redirects are disabled", headers, fp)


def _ineligible(reason: str, active_paid_subscriptions=None) -> dict:
    return {
        "eligible": False,
        "reason": reason,
        "active_paid_subscriptions": active_paid_subscriptions,
    }


def assess_subscriptions(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return _ineligible("invalid_billing_response")
    subscriptions = payload.get("result")
    info = payload.get("result_info")
    if not isinstance(subscriptions, list) or not isinstance(info, dict):
        return _ineligible("subscription_list_incomplete")
    total = info.get("total_count")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0 or total != len(subscriptions):
        return _ineligible("subscription_list_incomplete")
    if any(not isinstance(item, dict) for item in subscriptions):
        return _ineligible("invalid_subscription_record")

    paid_count = 0
    for item in subscriptions:
        state = item.get("state")
        if state not in KNOWN_STATES:
            return _ineligible("active_subscription_state_unknown", paid_count)
        if state in TERMINAL_STATES:
            continue
        price = item.get("price")
        if (
            not isinstance(price, (int, float))
            or isinstance(price, bool)
            or not math.isfinite(price)
            or price < 0
        ):
            return _ineligible("active_subscription_price_unknown", paid_count)
        if state == "Paid" or price > 0:
            paid_count += 1
            return _ineligible("active_paid_subscription_present", paid_count)
        if state in NON_PERMANENT_STATES:
            return _ineligible("active_subscription_state_not_permanent_free", paid_count)
        # The only active state accepted is Provisioned with an explicit $0 price.

    return {
        "eligible": True,
        "reason": "no_active_paid_subscriptions",
        "active_paid_subscriptions": paid_count,
    }


def _read_subscription_page(account_id: str, token: str, page: int, opener, timeout: int) -> dict:
    query = urlencode({"page": page, "per_page": REQUESTED_PER_PAGE})
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions?{query}",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with opener(request, timeout=timeout) as response:
        if response.status != 200:
            raise OSError("billing_api_unavailable")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("billing_response_too_large")
        return json.loads(raw.decode("utf-8"))


def _read_all_subscriptions(account_id: str, token: str, opener, timeout: int) -> dict:
    collected = []
    expected_total = None
    expected_per_page = None

    for page in range(1, MAX_PAGES + 1):
        payload = _read_subscription_page(account_id, token, page, opener, timeout)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return _ineligible("invalid_billing_response")
        items = payload.get("result")
        info = payload.get("result_info")
        if not isinstance(items, list) or not isinstance(info, dict):
            return _ineligible("subscription_list_incomplete")
        page_number = info.get("page")
        per_page = info.get("per_page")
        total = info.get("total_count")
        if (
            not isinstance(page_number, int) or isinstance(page_number, bool) or page_number != page
            or not isinstance(per_page, int) or isinstance(per_page, bool) or per_page < 1
            or per_page > REQUESTED_PER_PAGE
            or not isinstance(total, int) or isinstance(total, bool) or total < 0
        ):
            return _ineligible("subscription_pagination_metadata_invalid")
        if expected_total is None:
            expected_total = total
            expected_per_page = per_page
        elif total != expected_total or per_page != expected_per_page:
            return _ineligible("subscription_pagination_changed")
        if len(items) > per_page:
            return _ineligible("subscription_page_oversized")
        expected_page_size = min(per_page, max(0, expected_total - len(collected)))
        if len(items) != expected_page_size:
            return _ineligible("subscription_page_incomplete")
        collected.extend(items)
        if len(collected) == expected_total:
            return assess_subscriptions({
                "success": True,
                "result": collected,
                "result_info": {"total_count": len(collected)},
            })
        if page == MAX_PAGES:
            return _ineligible("subscription_list_exceeds_safety_bound")
        if not items:
            return _ineligible("subscription_list_incomplete")

    return _ineligible("subscription_list_incomplete")


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    if not account_id or not token:
        return _ineligible("billing_credentials_missing")
    open_request = opener or build_opener(_RejectRedirects).open
    try:
        return _read_all_subscriptions(account_id, token, open_request, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return _ineligible("billing_api_unavailable")


def main() -> int:
    result = check_boundary(
        os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(),
        os.environ.get("CLOUDFLARE_API_TOKEN", "").strip(),
    )
    eligible = "true" if result["eligible"] else "false"
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as stream:
            stream.write(f"zero_cost_eligible={eligible}\n")
    print(json.dumps({
        "zero_cost_eligible": result["eligible"],
        "reason": result["reason"],
        "active_paid_subscriptions": result["active_paid_subscriptions"],
        "staging_operations": "allowed" if result["eligible"] else "skipped",
    }, sort_keys=True))
    if not result["eligible"]:
        print("Staging operations skipped: zero-cost eligibility was not established.")
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
