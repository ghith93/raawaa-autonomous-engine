"""Read-only, fail-closed Cloudflare billing guard for automatic Staging.

Cloudflare documents Workers Free as the default and documents that Workers
Free/D1 Free limits fail requests rather than converting excess use into paid
usage. This guard only permits Staging when the complete account subscription
inventory is positively verified to contain no active paid/unknown plan.

The inventory endpoint is documented as a SinglePage list with no `page` or
`per_page` query parameters, so exactly one request without a query string is
sent. Sending undocumented pagination parameters produced the HTTP 400 that
kept the gate permanently false.
"""
from __future__ import annotations

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/3.0"
MAX_RESPONSE_BYTES = 1024 * 1024
# Upper bound for a plausible SinglePage `per_page`; not a request parameter.
MAX_SUBSCRIPTIONS = 100_000
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
    if not subscriptions:
        # An empty list proves no subscription records were returned, not that
        # every metered resource is on an enforceable Free plan. Fail closed.
        return _invalid("empty_subscription_inventory_unverified")
    paid = 0
    unknown = 0
    active_free = 0
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
        plan_name = plan.get("public_name")
        is_explicit_free_plan = (
            plan["id"].lower() == "free"
            and isinstance(plan_name, str)
            and "free" in plan_name.lower()
        )
        if state in {"Trial", "AwaitingPayment"}:
            unknown += 1
        elif state == "Paid" or price > 0:
            paid += 1
        elif not is_explicit_free_plan:
            unknown += 1
        else:
            active_free += 1
    if paid:
        return {"eligible": False, "reason": "active_paid_subscription_present",
                "active_paid_subscriptions": paid}
    if unknown:
        return _invalid("active_zero_price_nonfree_plan")
    if not active_free:
        return _invalid("no_active_free_plan_verified")
    return {"eligible": True, "reason": "no_active_paid_subscriptions",
            "active_paid_subscriptions": 0}


def _pagination_integer(info: dict, name: str) -> int | None:
    """Optional integer field of result_info; None when the field is absent."""
    if name not in info:
        return None
    value = info[name]
    if isinstance(value, bool):
        raise ValueError("invalid_pagination_" + name)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError("invalid_pagination_" + name)


def fetch_subscription_inventory(account_id: str, token: str, opener=None, timeout: int = 15) -> list:
    """Read the account subscription inventory in exactly one documented request.

    Cloudflare documents this endpoint as a SinglePage list: the API reference
    and the generated Python/Go SDKs declare only path parameters and return a
    `SinglePage`/`SyncSinglePage` type. It does not document `page` or
    `per_page`, so sending them is not a documented request and was the
    observed cause of HTTP 400 from the previous paginating implementation.

    The whole inventory therefore arrives in one response. Truncation cannot be
    repaired by paging, so anything inconsistent is treated as unknown and
    fails closed rather than being assumed complete.
    """
    if not account_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = opener or build_opener(_RejectRedirects).open
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET",
    )
    with open_request(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("billing_api_unavailable")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("billing_response_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("invalid_billing_response")
    records = payload.get("result")
    if not isinstance(records, list):
        raise ValueError("invalid_billing_response")
    info = payload.get("result_info")
    if info is None:
        # Documented as optional for this SinglePage endpoint. The result array
        # is the complete inventory; there is no second page to request.
        return records
    if not isinstance(info, dict):
        raise ValueError("invalid_billing_response")
    count = _pagination_integer(info, "count")
    page = _pagination_integer(info, "page")
    per_page = _pagination_integer(info, "per_page")
    total = _pagination_integer(info, "total_count")
    if page is not None and page != 1:
        # A SinglePage response that claims to be a later page means the
        # inventory returned here is not the whole inventory.
        raise ValueError("unexpected_subscription_page")
    if count is not None and count != len(records):
        raise ValueError("incomplete_subscription_inventory")
    if total is not None and total != len(records):
        raise ValueError("incomplete_subscription_inventory")
    if per_page is not None:
        if per_page < 1 or per_page > MAX_SUBSCRIPTIONS:
            raise ValueError("invalid_pagination_per_page")
        if len(records) > per_page:
            raise ValueError("incomplete_subscription_inventory")
    return records


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    try:
        subscriptions = fetch_subscription_inventory(account_id, token, opener=opener, timeout=timeout)
        return assess_subscriptions(subscriptions)
    except json.JSONDecodeError:
        return _invalid("invalid_billing_json")
    except UnicodeDecodeError:
        return _invalid("invalid_billing_encoding")
    except ValueError as exc:
        safe_reasons = {
            "billing_credentials_missing",
            "billing_api_unavailable",
            "billing_response_too_large",
            "invalid_billing_response",
            "unexpected_subscription_page",
            "invalid_pagination_count",
            "invalid_pagination_page",
            "invalid_pagination_per_page",
            "invalid_pagination_total_count",
            "incomplete_subscription_inventory",
        }
        reason = str(exc)
        return _invalid(reason if reason in safe_reasons else "billing_inventory_unavailable_or_incomplete")
    except HTTPError as exc:
        # HTTP status is useful for permission/rate-limit diagnosis; never log
        # response bodies, URLs, account identifiers or authorization data.
        if 400 <= exc.code <= 599:
            return _invalid(f"billing_http_{exc.code}")
        return _invalid("billing_http_error")
    except URLError:
        return _invalid("billing_transport_error")
    except TimeoutError:
        return _invalid("billing_timeout")
    except OSError:
        return _invalid("billing_os_error")


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
