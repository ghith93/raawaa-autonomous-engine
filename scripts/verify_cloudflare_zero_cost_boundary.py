"""Read-only, fail-closed Cloudflare billing guard for automatic Staging.

Cloudflare's List Subscriptions operation is documented as a single-page GET
with no page/per_page query parameters. The generated Cloudflare SDKs expose
SinglePage response types. This guard therefore makes one request to the
documented endpoint, validates the returned record count, and never invents
pagination parameters or assumes an incomplete page can be fetched.
"""
from __future__ import annotations

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/3.0"
MAX_RESPONSE_BYTES = 1024 * 1024
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


def _metadata_count(value) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid_subscription_count")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError("invalid_subscription_count")


def _read_subscriptions(account_id: str, token: str, opener, timeout: int) -> list:
    # This endpoint has no documented page/per_page query parameters. Keep the
    # request path exact; unsupported query parameters previously produced
    # HTTP 400 and disabled the entire zero-cost gate.
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions",
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
        raise ValueError("missing_subscription_metadata")
    count = _metadata_count(info.get("count"))
    if count != len(records):
        raise ValueError("incomplete_subscription_inventory")
    # Do not use result_info.total_count to invent paging semantics: this
    # endpoint is a SinglePage operation in the generated official SDKs.
    return records


def fetch_all_subscriptions(account_id: str, token: str, opener=None, timeout: int = 15) -> list:
    if not account_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = opener or build_opener(_RejectRedirects).open
    records = _read_subscriptions(account_id, token, open_request, timeout)
    seen_ids = set()
    for item in records:
        subscription_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(subscription_id, str) or not subscription_id or subscription_id in seen_ids:
            raise ValueError("missing_or_duplicate_subscription_id")
        seen_ids.add(subscription_id)
    return records


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    try:
        subscriptions = fetch_all_subscriptions(account_id, token, opener=opener, timeout=timeout)
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
            "missing_subscription_metadata",
            "invalid_subscription_count",
            "incomplete_subscription_inventory",
            "missing_or_duplicate_subscription_id",
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
