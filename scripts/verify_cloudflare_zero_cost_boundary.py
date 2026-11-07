"""Read-only, fail-closed Cloudflare billing guard for automatic Staging.

Cloudflare documents Workers Free as the default and documents that Workers
Free/D1 Free limits fail requests rather than converting excess use into paid
usage. This guard only permits Staging when the complete account subscription
inventory is positively verified to contain no active paid/unknown plan.
"""
from __future__ import annotations

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/2.0"
MAX_RESPONSE_BYTES = 1024 * 1024
PER_PAGE = 50
MAX_PAGES = 100
MAX_SUBSCRIPTIONS = PER_PAGE * MAX_PAGES
ACTIVE_STATES = {"Trial", "Provisioned", "Paid", "AwaitingPayment"}
INACTIVE_STATES = {"Cancelled", "Failed", "Expired"}
ALL_STATES = ACTIVE_STATES | INACTIVE_STATES


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "billing redirects are disabled", headers, fp)


def _invalid(reason: str) -> dict:
    return {"eligible": False, "reason": reason, "active_paid_subscriptions": None}


def _nonnegative_finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def assess_subscriptions(subscriptions: list) -> dict:
    if not isinstance(subscriptions, list):
        return _invalid("invalid_subscription_list")
    paid = 0
    unknown = 0
    seen_ids = set()
    for item in subscriptions:
        if not isinstance(item, dict):
            return _invalid("invalid_subscription_record")
        subscription_id = item.get("id")
        if not isinstance(subscription_id, str) or not subscription_id or subscription_id in seen_ids:
            return _invalid("missing_or_duplicate_subscription_id")
        seen_ids.add(subscription_id)
        state = item.get("state")
        if state not in ALL_STATES:
            return _invalid("unknown_subscription_state")
        if state in INACTIVE_STATES:
            continue
        price = item.get("price")
        if not _nonnegative_finite_number(price):
            return _invalid("invalid_subscription_price")
        plan = item.get("rate_plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("id"), str) or not plan["id"]:
            return _invalid("unknown_subscription_plan")
        if state in {"Trial", "AwaitingPayment"}:
            unknown += 1
        elif state == "Paid" or price > 0:
            paid += 1
        elif plan["id"].lower() != "free":
            unknown += 1
    if paid:
        return {"eligible": False, "reason": "active_paid_subscription_present",
                "active_paid_subscriptions": paid}
    if unknown:
        return _invalid("active_zero_price_nonfree_plan")
    return {"eligible": True, "reason": "no_active_paid_subscriptions",
            "active_paid_subscriptions": 0}


def _read_page(account_id: str, token: str, page: int, opener, timeout: int) -> tuple[list, dict]:
    query = urlencode({"page": page, "per_page": PER_PAGE})
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions?{query}",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET",
    )
    with opener(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("billing_api_unavailable")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("billing_response_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("invalid_billing_response")
    records = payload.get("result")
    info = payload.get("result_info")
    if not isinstance(records, list) or not isinstance(info, dict):
        raise ValueError("missing_pagination_metadata")
    count, current_page = info.get("count"), info.get("page")
    per_page, total = info.get("per_page"), info.get("total_count")
    values = (count, current_page, per_page, total)
    if any(not isinstance(v, int) or isinstance(v, bool) for v in values):
        raise ValueError("invalid_pagination_metadata")
    if current_page != page or per_page < 1 or per_page > PER_PAGE:
        raise ValueError("invalid_pagination_metadata")
    if count != len(records) or count > per_page or total < count or total > MAX_SUBSCRIPTIONS:
        raise ValueError("incomplete_subscription_page")
    return records, info


def fetch_all_subscriptions(account_id: str, token: str, opener=None, timeout: int = 15) -> list:
    if not account_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = opener or build_opener(_RejectRedirects).open
    collected = []
    expected_total = None
    expected_per_page = None
    for page in range(1, MAX_PAGES + 1):
        records, info = _read_page(account_id, token, page, open_request, timeout)
        if expected_total is None:
            expected_total = info["total_count"]
            expected_per_page = info["per_page"]
        elif info["total_count"] != expected_total or info["per_page"] != expected_per_page:
            raise ValueError("subscription_inventory_changed_during_pagination")
        expected_page_count = min(expected_per_page, expected_total - len(collected))
        if len(records) != expected_page_count:
            raise ValueError("incomplete_subscription_page")
        seen_ids = {item.get("id") for item in collected if isinstance(item, dict)}
        for item in records:
            subscription_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(subscription_id, str) or not subscription_id or subscription_id in seen_ids:
                raise ValueError("missing_or_duplicate_subscription_id")
            seen_ids.add(subscription_id)
        collected.extend(records)
        if len(collected) == expected_total:
            return collected
        if len(collected) > expected_total or not records:
            raise ValueError("incomplete_subscription_inventory")
    raise ValueError("subscription_inventory_page_limit_exceeded")


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    try:
        subscriptions = fetch_all_subscriptions(account_id, token, opener=opener, timeout=timeout)
        return assess_subscriptions(subscriptions)
    except ValueError as exc:
        return _invalid(str(exc))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return _invalid("billing_inventory_unavailable_or_incomplete")


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
