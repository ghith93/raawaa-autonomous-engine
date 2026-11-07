"""Read-only, fail-closed Cloudflare cost boundary for remote automation.

Cloudflare bills zone plans separately from account/domain add-on subscriptions.
The guard therefore checks both:
1. account-level add-ons via the documented SinglePage subscriptions endpoint;
2. every zone's current plan via the paginated List Zones endpoint and the
   dedicated Zone Subscription Details endpoint.

No mutation, billing-profile update, or spend-policy change is performed.
Unknown, incomplete, inaccessible, paid, trial, or externally-managed plan
evidence blocks remote operations.
"""
from __future__ import annotations

import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/4.0"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ZONES_TO_VERIFY = 20
ZONE_PAGE_SIZE = 50
FREE_ZONE_PLAN_IDS = {"free"}
INACTIVE_STATES = {"Cancelled", "Failed", "Expired"}
ACTIVE_STATES = {"Trial", "Provisioned", "Paid", "AwaitingPayment"}
# Workflow annotations accept only fixed, sanitized reason tokens.
SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "billing redirects are disabled", headers, fp)


def _invalid(reason: str, *, zones_checked: int = 0) -> dict:
    return {
        "eligible": False,
        "reason": reason,
        "active_paid_subscriptions": None,
        "active_paid_zone_plans": None,
        "zones_checked": zones_checked,
    }


def _nonnegative_finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _open_request(opener=None):
    return opener or build_opener(_RejectRedirects).open


def _read_json(request: Request, opener, timeout: int, unavailable_reason: str) -> dict:
    with opener(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(unavailable_reason)
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("billing_response_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("invalid_billing_response")
    return payload


def _metadata_integer(info: dict, name: str) -> int | None:
    if name not in info:
        return None
    value = info[name]
    if isinstance(value, bool):
        raise ValueError("invalid_pagination_" + name)
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and value >= 0:
        return int(value)
    raise ValueError("invalid_pagination_" + name)


def assess_subscriptions(subscriptions: list) -> dict:
    """Assess account-level add-ons; an empty list means no add-ons, not no zone plans."""
    if not isinstance(subscriptions, list):
        return {"eligible": False, "reason": "invalid_subscription_list", "active_paid_subscriptions": None}
    paid = 0
    unknown = 0
    seen_ids = set()
    for item in subscriptions:
        if not isinstance(item, dict):
            return {"eligible": False, "reason": "invalid_subscription_record", "active_paid_subscriptions": None}
        subscription_id = item.get("id")
        if not isinstance(subscription_id, str) or not subscription_id or subscription_id in seen_ids:
            return {"eligible": False, "reason": "missing_or_duplicate_subscription_id", "active_paid_subscriptions": None}
        seen_ids.add(subscription_id)
        state = item.get("state")
        if state not in ACTIVE_STATES | INACTIVE_STATES:
            return {"eligible": False, "reason": "unknown_subscription_state", "active_paid_subscriptions": None}
        if state in INACTIVE_STATES:
            continue
        price = item.get("price")
        if not _nonnegative_finite_number(price):
            return {"eligible": False, "reason": "invalid_subscription_price", "active_paid_subscriptions": None}
        plan = item.get("rate_plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("id"), str) or not plan["id"]:
            return {"eligible": False, "reason": "unknown_subscription_plan", "active_paid_subscriptions": None}
        plan_id = plan["id"].lower()
        plan_name = plan.get("public_name")
        if plan.get("externally_managed") is True or plan.get("is_contract") is True:
            unknown += 1
            continue
        explicitly_free = (
            plan_id in FREE_ZONE_PLAN_IDS
            and price == 0
            and (plan_name is None or isinstance(plan_name, str) and "free" in plan_name.lower())
        )
        if state in {"Trial", "AwaitingPayment"}:
            unknown += 1
        elif price > 0 or state == "Paid" and not explicitly_free:
            paid += 1
        elif not explicitly_free:
            unknown += 1
    if paid:
        return {"eligible": False, "reason": "active_paid_subscription_present",
                "active_paid_subscriptions": paid}
    if unknown:
        return {"eligible": False, "reason": "active_zero_price_nonfree_plan",
                "active_paid_subscriptions": 0}
    return {"eligible": True, "reason": "no_active_paid_addons",
            "active_paid_subscriptions": 0}


def fetch_subscription_inventory(account_id: str, token: str, opener=None, timeout: int = 15) -> list:
    """Read account add-ons via the documented SinglePage endpoint (no query string)."""
    if not account_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = _open_request(opener)
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET",
    )
    payload = _read_json(request, open_request, timeout, "billing_api_unavailable")
    records = payload.get("result")
    if not isinstance(records, list):
        raise ValueError("invalid_billing_response")
    info = payload.get("result_info")
    if info is not None:
        if not isinstance(info, dict):
            raise ValueError("invalid_billing_response")
        count = _metadata_integer(info, "count")
        page = _metadata_integer(info, "page")
        per_page = _metadata_integer(info, "per_page")
        total = _metadata_integer(info, "total_count")
        if page is not None and page != 1:
            raise ValueError("unexpected_subscription_page")
        if count is not None and count != len(records):
            raise ValueError("incomplete_subscription_inventory")
        if total is not None and total != len(records):
            raise ValueError("incomplete_subscription_inventory")
        if per_page is not None and (per_page < 1 or len(records) > per_page):
            raise ValueError("incomplete_subscription_inventory")
    return records


def fetch_zone_inventory(account_id: str, token: str, opener=None, timeout: int = 10) -> list:
    """List every zone in the account using the documented paginated List Zones API.

    One 50-record page is requested. Because Cloudflare's total_count metadata
    is explicitly unfiltered (it can describe zones outside this account), it
    is validated for shape but never used as the account-specific result count.
    A full page is ambiguous and fails closed; only 0-20 returned zones can pass.
    """
    if not account_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = _open_request(opener)
    query = urlencode({
        "account.id": account_id,
        "page": 1,
        "per_page": ZONE_PAGE_SIZE,
        # List Zones excludes internal zones by default. Include every zone
        # type so an omitted zone can never be mistaken for a Free account.
        "type": "full,partial,secondary,internal",
    })
    request = Request(
        f"https://api.cloudflare.com/client/v4/zones?{query}",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET",
    )
    payload = _read_json(request, open_request, timeout, "zone_inventory_unavailable")
    zones = payload.get("result")
    info = payload.get("result_info")
    if not isinstance(zones, list) or not isinstance(info, dict):
        raise ValueError("zone_inventory_incomplete")
    count = _metadata_integer(info, "count")
    page = _metadata_integer(info, "page")
    per_page = _metadata_integer(info, "per_page")
    # Cloudflare documents total_count as the total available without search
    # parameters. With account.id filtering it is not the account's zone count.
    # Validate optional metadata types, but do not compare those global totals.
    _metadata_integer(info, "total_count")
    _metadata_integer(info, "total_pages")
    if count is None or page != 1 or per_page != ZONE_PAGE_SIZE:
        raise ValueError("zone_inventory_incomplete")
    if count != len(zones):
        raise ValueError("zone_inventory_incomplete")
    if count >= per_page or count > MAX_ZONES_TO_VERIFY:
        raise ValueError("too_many_zones_to_verify")
    seen = set()
    for zone in zones:
        if not isinstance(zone, dict):
            raise ValueError("invalid_zone_record")
        zone_id = zone.get("id")
        zone_account = zone.get("account")
        if not isinstance(zone_id, str) or not zone_id or zone_id in seen:
            raise ValueError("missing_or_duplicate_zone_id")
        if not isinstance(zone_account, dict) or zone_account.get("id") != account_id:
            raise ValueError("zone_account_mismatch")
        if not isinstance(zone.get("name"), str) or not zone["name"]:
            raise ValueError("invalid_zone_record")
        seen.add(zone_id)
    return zones


def assess_zone_subscription(subscription: dict) -> dict:
    if not isinstance(subscription, dict):
        return {"eligible": False, "reason": "unknown_zone_plan"}
    rate_plan = subscription.get("rate_plan")
    if not isinstance(rate_plan, dict):
        return {"eligible": False, "reason": "unknown_zone_plan"}
    plan_id = rate_plan.get("id")
    price = subscription.get("price")
    state = subscription.get("state")
    public_name = rate_plan.get("public_name")
    if plan_id not in FREE_ZONE_PLAN_IDS:
        return {"eligible": False, "reason": "active_paid_zone_plan"}
    if not _nonnegative_finite_number(price) or price != 0:
        return {"eligible": False, "reason": "active_paid_zone_plan"}
    if state not in {"Provisioned", "Paid"}:
        return {"eligible": False, "reason": "zone_plan_state_unverified"}
    if public_name is not None and (not isinstance(public_name, str) or "free" not in public_name.lower()):
        return {"eligible": False, "reason": "unknown_zone_plan"}
    if rate_plan.get("externally_managed") is True or rate_plan.get("is_contract") is True:
        return {"eligible": False, "reason": "zone_plan_external"}
    if rate_plan.get("scope") not in {None, "zone"}:
        return {"eligible": False, "reason": "zone_plan_scope_mismatch"}
    return {"eligible": True, "reason": "explicit_free_zone_plan"}


def fetch_zone_subscription(zone_id: str, token: str, opener=None, timeout: int = 5) -> dict:
    if not zone_id or not token:
        raise ValueError("billing_credentials_missing")
    open_request = _open_request(opener)
    request = Request(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/subscription",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET",
    )
    payload = _read_json(request, open_request, timeout, "zone_subscription_unavailable")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("unknown_zone_plan")
    return result


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    zones_checked = 0
    try:
        if not account_id or not token:
            raise ValueError("billing_credentials_missing")
        open_request = _open_request(opener)
        add_ons = fetch_subscription_inventory(account_id, token, opener=open_request, timeout=timeout)
        add_on_result = assess_subscriptions(add_ons)
        if not add_on_result["eligible"]:
            return _invalid(add_on_result["reason"])
        zones = fetch_zone_inventory(account_id, token, opener=open_request, timeout=min(timeout, 10))
        zones_checked = len(zones)
        for zone in zones:
            details = fetch_zone_subscription(zone["id"], token, opener=open_request, timeout=min(timeout, 5))
            assessment = assess_zone_subscription(details)
            if not assessment["eligible"]:
                return {
                    "eligible": False,
                    "reason": assessment["reason"],
                    "active_paid_subscriptions": add_on_result["active_paid_subscriptions"],
                    "active_paid_zone_plans": 1 if assessment["reason"] == "active_paid_zone_plan" else None,
                    "zones_checked": zones_checked,
                }
        return {
            "eligible": True,
            "reason": "all_addons_and_zone_plans_explicitly_free",
            "active_paid_subscriptions": add_on_result["active_paid_subscriptions"],
            "active_paid_zone_plans": 0,
            "zones_checked": zones_checked,
        }
    except json.JSONDecodeError:
        return _invalid("invalid_billing_json", zones_checked=zones_checked)
    except UnicodeDecodeError:
        return _invalid("invalid_billing_encoding", zones_checked=zones_checked)
    except ValueError as exc:
        safe_reasons = {
            "billing_credentials_missing", "billing_api_unavailable", "billing_response_too_large",
            "invalid_billing_response", "unexpected_subscription_page", "invalid_pagination_count",
            "invalid_pagination_page", "invalid_pagination_per_page", "invalid_pagination_total_count",
            "incomplete_subscription_inventory", "zone_inventory_unavailable", "zone_inventory_incomplete",
            "too_many_zones_to_verify", "invalid_zone_record", "missing_or_duplicate_zone_id",
            "zone_account_mismatch", "zone_subscription_unavailable", "unknown_zone_plan",
            "active_paid_zone_plan", "zone_plan_state_unverified", "zone_plan_external",
            "zone_plan_scope_mismatch",
        }
        reason = str(exc)
        return _invalid(reason if reason in safe_reasons else "billing_inventory_unavailable_or_incomplete",
                        zones_checked=zones_checked)
    except HTTPError as exc:
        if 400 <= exc.code <= 599:
            return _invalid(f"billing_http_{exc.code}", zones_checked=zones_checked)
        return _invalid("billing_http_error", zones_checked=zones_checked)
    except URLError:
        return _invalid("billing_transport_error", zones_checked=zones_checked)
    except TimeoutError:
        return _invalid("billing_timeout", zones_checked=zones_checked)
    except OSError:
        return _invalid("billing_os_error", zones_checked=zones_checked)


def activity_annotation(result: dict) -> str:
    """Emit a readable, sanitized check annotation without account data."""
    reason = result.get("reason")
    if not isinstance(reason, str) or not SAFE_REASON_RE.fullmatch(reason):
        raise ValueError("unsafe_zero_cost_reason")
    eligible = result.get("eligible") is True
    level = "notice" if eligible else "error"
    return f"::{level} title=Cloudflare zero-cost preflight::eligible={str(eligible).lower()}; reason={reason}"


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
        "active_paid_zone_plans": result["active_paid_zone_plans"],
        "zones_checked": result["zones_checked"],
        "remote_operations": "allowed" if result["eligible"] else "skipped",
    }, sort_keys=True))
    print(activity_annotation(result))
    if not result["eligible"]:
        print("Remote operations skipped: complete zero-cost eligibility was not established.")
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
