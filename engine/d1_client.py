"""Small, dependency-free Cloudflare D1 REST client.

The REST API is used for administrative/bootstrap operations and bounded jobs.
It is not a mounted SQLite connection; callers must use parameterized SQL and
must not assume SQLite connection/transaction semantics across HTTP requests.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class D1ConfigurationError(RuntimeError):
    pass


class D1APIError(RuntimeError):
    pass


class D1Client:
    def __init__(self, account_id: str | None = None, database_id: str | None = None,
                 api_token: str | None = None, timeout: int = 20):
        self.account_id = account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.database_id = database_id or os.getenv("D1_DATABASE_ID", "")
        self.api_token = api_token or os.getenv("CLOUDFLARE_API_TOKEN", "")
        self.timeout = timeout
        missing = [name for name, value in (
            ("CLOUDFLARE_ACCOUNT_ID", self.account_id),
            ("D1_DATABASE_ID", self.database_id),
            ("CLOUDFLARE_API_TOKEN", self.api_token),
        ) if not value]
        if missing:
            raise D1ConfigurationError("Missing required environment variables: " + ", ".join(missing))
        if not self.account_id.isalnum() or len(self.account_id) != 32:
            raise D1ConfigurationError("CLOUDFLARE_ACCOUNT_ID must be a 32-character account identifier")
        if len(self.database_id) != 36:
            raise D1ConfigurationError("D1_DATABASE_ID must be a UUID")

    @property
    def endpoint(self) -> str:
        return (f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/d1/database/{self.database_id}/query")

    def _post(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_token}",
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Error bodies are untrusted and may echo submitted data. Keep them
            # out of exceptions and logs; the status code is enough to classify
            # transport/auth/rate-limit failures without leaking response content.
            raise D1APIError(f"Cloudflare D1 API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise D1APIError(f"Cloudflare D1 API connection failed: {type(exc).__name__}") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D1APIError(f"Cloudflare D1 API returned invalid JSON: {type(exc).__name__}") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            errors = data.get("errors", []) if isinstance(data, dict) else []
            safe_errors = [{"code": item.get("code"), "message": item.get("message")}
                           for item in errors if isinstance(item, dict)]
            raise D1APIError(f"Cloudflare D1 query failed: {safe_errors or 'unspecified API error'}")
        results = data.get("result")
        if not isinstance(results, list):
            raise D1APIError("Cloudflare D1 API response omitted result list")
        return results

    def query(self, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("SQL statement is required")
        if params is not None and not isinstance(params, list):
            raise ValueError("params must be a list")
        results = self._post({"sql": sql, "params": params or []})
        if len(results) != 1 or not isinstance(results[0], dict):
            raise D1APIError("Unexpected single-query response shape")
        item = results[0]
        if item.get("success") is False:
            raise D1APIError("Cloudflare D1 reported query failure")
        return item

    def batch(self, statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not statements:
            raise ValueError("at least one statement is required")
        normalized = []
        for statement in statements:
            if not isinstance(statement, dict) or not isinstance(statement.get("sql"), str) or not statement["sql"].strip():
                raise ValueError("each statement requires non-empty SQL")
            params = statement.get("params", [])
            if not isinstance(params, list):
                raise ValueError("statement params must be a list")
            normalized.append({"sql": statement["sql"], "params": params})
        results = self._post({"batch": normalized})
        if len(results) != len(normalized):
            raise D1APIError("Cloudflare D1 batch response count did not match request")
        if any(not isinstance(item, dict) or item.get("success") is False for item in results):
            raise D1APIError("One or more Cloudflare D1 batch statements failed")
        return results

    def healthcheck(self) -> dict[str, Any]:
        result = self.query("SELECT 1 AS ok")
        rows = result.get("results", [])
        if not rows or rows[0].get("ok") != 1:
            raise D1APIError("Cloudflare D1 healthcheck returned an unexpected result")
        meta = result.get("meta", {})
        return {"ok": True, "rows_read": meta.get("rows_read", 0),
                "duration_ms": meta.get("duration", 0)}
