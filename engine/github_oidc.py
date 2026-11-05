"""Minimal, bounded GitHub Actions OIDC token request helper."""
from __future__ import annotations

import json
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GithubOidcError(RuntimeError):
    """A GitHub Actions identity token could not be obtained safely."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_github_oidc_token(
    environ: Mapping[str, str], *, audience: str, timeout: float = 10.0,
) -> str:
    """Request a short-lived audience-bound ID token without logging credentials."""
    if (environ.get("GITHUB_EVENT_NAME") != "schedule"
            or environ.get("GITHUB_REF") != "refs/heads/main"):
        raise GithubOidcError("GitHub OIDC is restricted to the main-branch schedule")
    endpoint = environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or parsed.hostname != "pipelines.actions.githubusercontent.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment or not parsed.path.startswith("/")):
        raise GithubOidcError("GitHub OIDC request endpoint is invalid")
    if len(request_token) < 32 or not audience or len(audience) > 128:
        raise GithubOidcError("GitHub OIDC request configuration is incomplete")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key != "audience"]
    query.append(("audience", audience))
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    request = Request(
        url,
        method="GET",
        headers={
            "Authorization": "Bearer " + request_token,
            "Accept": "application/json",
            "User-Agent": "RaawaaAutonomousEngine/1.0",
        },
    )
    try:
        with build_opener(_RejectRedirects).open(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 + 1)
        if len(raw) > 8 * 1024:
            raise GithubOidcError("GitHub OIDC response exceeded its size limit")
        value = json.loads(raw.decode("utf-8"))
    except GithubOidcError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise GithubOidcError("GitHub OIDC token request failed") from None
    token = value.get("value") if isinstance(value, dict) else None
    if not isinstance(token, str) or len(token) > 16 * 1024 or token.count(".") != 2:
        raise GithubOidcError("GitHub OIDC response did not contain a valid token")
    return token
