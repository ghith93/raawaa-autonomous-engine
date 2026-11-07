"""Read-only fail-closed guard for automatic Cloudflare Staging operations.

The Workers Free plan is the default. Paid subscriptions are a hard stop for
automatic Staging side effects under the owner's zero-spend policy. If the
billing API is unavailable or its response is ambiguous, Staging is skipped.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = "RaawaaAutonomousEngine-ZeroCostPreflight/1.0"
MAX_RESPONSE_BYTES = 1024 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "billing redirects are disabled", headers, fp)


def assess_subscriptions(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return {"eligible": False, "reason": "invalid_billing_response", "active_paid_subscriptions": None}
    subscriptions = payload.get("result")
    if not isinstance(subscriptions, list):
        return {"eligible": False, "reason": "invalid_subscription_list", "active_paid_subscriptions": None}
    if any(not isinstance(item, dict) for item in subscriptions):
        return {"eligible": False, "reason": "invalid_subscription_record", "active_paid_subscriptions": None}
    active = [
        item for item in subscriptions
        if isinstance(item, dict) and item.get("state") not in {"Cancelled", "Failed", "Expired"}
    ]
    unknown_price = [
        item for item in active
        if not isinstance(item.get("price"), (int, float))
        or isinstance(item.get("price"), bool)
    ]
    paid = [
        item for item in active
        if isinstance(item.get("price"), (int, float))
        and not isinstance(item.get("price"), bool)
        and item.get("price", 0) > 0
    ]
    eligible = not paid and not unknown_price
    reason = (
        "active_paid_subscription_present" if paid
        else "active_subscription_price_unknown" if unknown_price
        else "no_active_paid_subscriptions"
    )
    return {
        "eligible": eligible,
        "reason": reason,
        "active_paid_subscriptions": len(paid),
    }


def check_boundary(account_id: str, token: str, opener=None, timeout: int = 15) -> dict:
    if not account_id or not token:
        return {"eligible": False, "reason": "billing_credentials_missing", "active_paid_subscriptions": None}
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/subscriptions",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    open_request = opener or build_opener(_RejectRedirects).open
    try:
        with open_request(request, timeout=timeout) as response:
            if response.status != 200:
                return {"eligible": False, "reason": "billing_api_unavailable", "active_paid_subscriptions": None}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return {"eligible": False, "reason": "billing_response_too_large", "active_paid_subscriptions": None}
            payload = json.loads(raw.decode("utf-8"))
        return assess_subscriptions(payload)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"eligible": False, "reason": "billing_api_unavailable", "active_paid_subscriptions": None}


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
