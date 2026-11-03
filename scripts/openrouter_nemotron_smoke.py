"""One-request OpenRouter Nemotron Free smoke test; never prints prompts, keys, or output text."""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
USER_AGENT = "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
MAX_RESPONSE_BYTES = 64 * 1024
SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def safe_error_code(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            for value in (error.get("code"), error.get("type")):
                if isinstance(value, (str, int)):
                    candidate = str(value)
                    if SAFE_CODE.fullmatch(candidate):
                        return candidate
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        pass
    return None


def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("FAILED: OPENROUTER_API_KEY is missing or empty.")
        return 2

    # One fixed, public, non-sensitive prompt. No user, customer, or Raawaa data.
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly this text and nothing else: RAAWAA_NEMOTRON_SMOKE_OK",
            }
        ],
        "max_tokens": 64,
        "temperature": 0,
        "stream": False,
    }
    request = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": "https://github.com/ghith93/raawaa-autonomous-engine",
            "X-Title": "Raawaa Autonomous Engine one-shot smoke test",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=35) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raw_error = exc.read(16 * 1024)
        code = safe_error_code(raw_error)
        print(json.dumps({
            "status": "failed",
            "http_status": exc.code,
            "provider_error_code": code,
            "inference_requests_attempted": 1,
            "paid_fallback": False,
        }, sort_keys=True))
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(json.dumps({
            "status": "failed",
            "transport_error": type(exc).__name__,
            "inference_requests_attempted": 1,
            "paid_fallback": False,
        }, sort_keys=True))
        return 1

    if status != 200 or len(raw) > MAX_RESPONSE_BYTES:
        print(json.dumps({
            "status": "failed",
            "http_status": status,
            "response_within_size_limit": len(raw) <= MAX_RESPONSE_BYTES,
            "inference_requests_attempted": 1,
            "paid_fallback": False,
        }, sort_keys=True))
        return 1

    try:
        result = json.loads(raw.decode("utf-8"))
        choices = result.get("choices") if isinstance(result, dict) else None
        has_choice = isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict)
        message = choices[0].get("message") if has_choice else None
        message_is_object = isinstance(message, dict)
        content = message.get("content") if message_is_object else None
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        returned_model = result.get("model") if isinstance(result, dict) else None
        content_matches = isinstance(content, str) and content.strip() == "RAAWAA_NEMOTRON_SMOKE_OK"
        model_matches = returned_model == MODEL
        finish_reason = choices[0].get("finish_reason") if has_choice else None
        if not content_matches or not model_matches:
            safe_finish_reason = finish_reason if isinstance(finish_reason, str) and SAFE_CODE.fullmatch(finish_reason) else None
            print(json.dumps({
                "status": "failed",
                "reason": "completion_or_model_validation_failed",
                "response_is_json_object": isinstance(result, dict),
                "has_choices": isinstance(choices, list),
                "has_first_choice": has_choice,
                "has_message_object": message_is_object,
                "content_type": type(content).__name__,
                "content_matches_sentinel": content_matches,
                "returned_model_matches_requested": model_matches,
                "finish_reason": safe_finish_reason,
                "inference_requests_attempted": 1,
                "paid_fallback": False,
                "response_text_logged": False,
            }, sort_keys=True))
            return 1
        if not isinstance(usage, dict):
            usage = {}
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        print(json.dumps({
            "status": "failed",
            "reason": "invalid_json_or_response_structure",
            "inference_requests_attempted": 1,
            "paid_fallback": False,
            "response_text_logged": False,
        }, sort_keys=True))
        return 1

    print(json.dumps({
        "status": "success",
        "provider": "openrouter",
        "model": MODEL,
        "returned_model_matches": True,
        "completion_received": True,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "inference_requests_attempted": 1,
        "paid_fallback": False,
        "response_text_logged": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
