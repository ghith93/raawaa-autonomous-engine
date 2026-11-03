"""Verify Groq API authentication and free-model catalog visibility without inference."""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.groq.com/openai/v1/models"
USER_AGENT = "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
FREE_MODEL_CANDIDATES = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
)
SAFE_RATE_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
)
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def extract_model_ids(payload: object) -> set[str]:
    """Extract model IDs from the bounded /models response."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("unexpected model catalog shape")
    return {
        row["id"] for row in payload["data"]
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def safe_provider_error_code(raw: bytes) -> str | None:
    """Return only a bounded machine code; never return provider messages."""
    try:
        payload = json.loads(raw.decode("utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return None
        for candidate in (error.get("code"), error.get("type")):
            if isinstance(candidate, str) and _SAFE_CODE.fullmatch(candidate):
                return candidate
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        pass
    return None


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
        raw_error = exc.read(16 * 1024 + 1)
        code = safe_provider_error_code(raw_error[:16 * 1024])
        print(f"Groq model-catalog request failed: HTTP {exc.code}.")
        if code:
            print(f"provider_error_code={code}")
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
        model_ids = extract_model_ids(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        print("Groq model catalog returned an unexpected response shape.")
        return 1

    print("Groq API authentication: successful.")
    print("Operation: model-catalog read only; inference requests: 0.")
    for model_id in FREE_MODEL_CANDIDATES:
        visible = model_id in model_ids
        print(f"candidate_model={model_id} catalog_visible={str(visible).lower()}")
    print(
        "Important: catalog visibility is not proof that organization/project "
        "model permissions allow inference."
    )
    for name in SAFE_RATE_HEADERS:
        value = headers.get(name)
        if value:
            print(f"{name}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
