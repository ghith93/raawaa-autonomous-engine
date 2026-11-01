"""Verify Groq API authentication and list accessible model IDs without inference."""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.groq.com/openai/v1/models"
USER_AGENT = "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
SAFE_RATE_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
)


def main() -> int:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        print("GROQ_API_KEY secret is missing or empty.")
        return 2

    request = Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            headers = response.headers
            raw = response.read(256 * 1024 + 1)
    except HTTPError as exc:
        # Never print response bodies or request headers; both may contain sensitive data.
        print(f"Groq model-catalog request failed: HTTP {exc.code}.")
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(f"Groq model-catalog request failed: {type(exc).__name__}.")
        return 1

    if status != 200:
        print(f"Groq model-catalog request returned HTTP {status}.")
        return 1
    if len(raw) > 256 * 1024:
        print("Groq model catalog exceeded the accepted response size.")
        return 1

    try:
        payload = json.loads(raw.decode("utf-8"))
        rows = payload["data"]
        model_ids = sorted({
            row["id"] for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        })
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        print("Groq model catalog returned an unexpected response shape.")
        return 1

    print("Groq API authentication: successful.")
    print("Operation: model-catalog read only; inference requests: 0.")
    print(f"Accessible model IDs returned: {len(model_ids)}.")
    for model_id in model_ids:
        print(f"MODEL {model_id}")
    for name in SAFE_RATE_HEADERS:
        value = headers.get(name)
        if value:
            print(f"{name}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
