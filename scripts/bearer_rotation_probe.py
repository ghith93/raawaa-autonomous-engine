#!/usr/bin/env python3
"""Prove the deployed Worker and GitHub agree on the bearer secret before deploying.

Cloudflare cannot return an existing secret value, and GitHub Actions cannot read
its own secret values either. The production deploy workflow used to bridge that
gap by rewriting the Worker's ``ENGINE_API_TOKEN`` secret on *every* deployment,
which meant a routine code merge could replace a working live credential with a
value the deployed Worker then rejected. That is a self-inflicted outage path,
not a deployment requirement.

This probe removes the guesswork without mutating anything. It sends the
repository secret to ``POST /v1/diagnostics/auth``, a route that performs no D1
access (see ``worker/index.js``) and only reports whether the presented bearer
token matches the deployed Worker secret. Only then does the workflow deploy.

Exit codes:

=== =====================================================================
0   the deployed Worker accepts the repository value; deploy may proceed
3   the Worker answered and rejected the value (HTTP 401); stop — an
    operator must decide which of the two values is authoritative
4   no verdict (transport failure, edge 403, 5xx, missing secret); stop
    rather than deploy on an unverified credential state
=== =====================================================================

The token is never printed, echoed, or placed on the workflow command line: it
is read from the environment, and only the numeric HTTP status reaches the log.
"""

from __future__ import annotations

import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DIAGNOSTICS_AUTH_URL = (
    "https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev/v1/diagnostics/auth"
)
USER_AGENT = (
    "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
)

EXIT_VERIFIED = 0
EXIT_MISMATCH = 3
EXIT_NO_VERDICT = 4


def probe(token: str, *, timeout: float = 20.0, url: str = DIAGNOSTICS_AUTH_URL) -> int | None:
    """Return the HTTP status from the auth preflight, or ``None`` on transport failure.

    Transport failures are reported as "no verdict", never as a rejection: a
    network problem must not be recorded as evidence that a credential is wrong.
    ``HTTPError`` is different — the Worker answered, so the status is a real
    verdict and is returned as-is.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
        "User-Agent": USER_AGENT,
    }
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID", "")
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret

    request = Request(url, headers=headers, data=b"", method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except (OSError, ValueError):
        return None


def classify(status: int | None) -> tuple[int, str]:
    """Map a probe result to (exit code, log-safe message). No secret material."""
    if status == 200:
        return (
            EXIT_VERIFIED,
            "POST /v1/diagnostics/auth returned HTTP 200: the deployed Worker accepts the "
            "repository ENGINE_API_TOKEN, so the deployment cannot lock out the Task Runner.",
        )
    if status == 401:
        return (
            EXIT_MISMATCH,
            "POST /v1/diagnostics/auth returned HTTP 401: the deployed Worker does not accept "
            "the repository ENGINE_API_TOKEN. This workflow no longer rewrites production "
            "secrets, because a merge must not be able to replace a live credential. Confirm "
            "which value is authoritative, update the stale side, and re-run. Nothing was "
            "deployed and no secret was written.",
        )
    if status is None:
        return (
            EXIT_NO_VERDICT,
            "The auth preflight produced no verdict (transport failure). Refusing to deploy on "
            "an unverified credential state. Nothing was deployed and no secret was written.",
        )
    return (
        EXIT_NO_VERDICT,
        f"The auth preflight returned HTTP {status}, which is neither acceptance nor a clean "
        "rejection. Refusing to deploy. Investigate edge controls (Cloudflare Access/WAF) and "
        "Worker health; do not disable them. Nothing was deployed and no secret was written.",
    )


def main() -> int:
    token = os.environ.get("ENGINE_API_TOKEN", "")
    if not token:
        print("ENGINE_API_TOKEN is not configured; cannot verify the deployed Worker.")
        return EXIT_NO_VERDICT
    code, message = classify(probe(token))
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
