"""Create and revoke a short-lived Cloudflare Access service token for Staging CI."""
from __future__ import annotations
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.cloudflare.com/client/v4"

def _request(method: str, path: str, payload: dict | None = None) -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token or not account:
        raise SystemExit("Cloudflare API credentials are missing.")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        API + path.format(account_id=account), data=body, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(req, timeout=20) as response:
            data = json.loads(response.read(64 * 1024 + 1).decode("utf-8"))
    except HTTPError as exc:
        raise SystemExit(f"Cloudflare Access service-token API returned HTTP {exc.code}; required permission: Access: Service Tokens Write.") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit("Cloudflare Access service-token API failed: " + type(exc).__name__) from None
    if not isinstance(data, dict) or data.get("success") is not True:
        errors = data.get("errors", []) if isinstance(data, dict) else []
        codes = [str(item.get("code")) for item in errors if isinstance(item, dict)]
        raise SystemExit("Cloudflare Access service-token API rejected the request; error codes=" + ",".join(codes[:5]))
    return data.get("result") or {}

def _write_env(values: dict[str, str]) -> None:
    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        raise SystemExit("GITHUB_ENV is unavailable; refusing to persist temporary token.")
    with open(env_file, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise SystemExit("Unexpected newline in temporary service-token value.")
            handle.write(f"{key}={value}\n")
            if key in {"CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "CF_ACCESS_SERVICE_TOKEN_ID"}:
                print("::add-mask::" + value)

def create() -> None:
    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    result = _request("POST", "/accounts/{account_id}/access/service_tokens", {
        "name": "raawaa-staging-smoke-" + run_id,
        "duration": "15m",
        "tags": {"project": "raawaa-autonomous-engine", "purpose": "one-shot-staging-smoke"},
    })
    token_id = result.get("id")
    client_id = result.get("client_id")
    client_secret = result.get("client_secret")
    if not all(isinstance(value, str) and value for value in (token_id, client_id, client_secret)):
        raise SystemExit("Cloudflare did not return a complete ephemeral service token.")
    _write_env({
        "CF_ACCESS_SERVICE_TOKEN_ID": token_id,
        "CF_ACCESS_CLIENT_ID": client_id,
        "CF_ACCESS_CLIENT_SECRET": client_secret,
    })
    print(json.dumps({"status": "created", "duration": result.get("duration"), "token_id_present": True}))

def delete() -> None:
    token_id = os.environ.get("CF_ACCESS_SERVICE_TOKEN_ID", "").strip()
    if not token_id:
        print(json.dumps({"status": "no_token_to_revoke"}))
        return
    _request("DELETE", "/accounts/{account_id}/access/service_tokens/" + token_id)
    print(json.dumps({"status": "revoked", "token_id_present": True}))

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"create", "delete"}:
        raise SystemExit("Usage: manage_staging_access_token.py create|delete")
    create() if sys.argv[1] == "create" else delete()
