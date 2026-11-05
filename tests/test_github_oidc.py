from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from engine.github_oidc import GithubOidcError, request_github_oidc_token


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return json.dumps(self.value).encode("utf-8")


class GithubOidcTests(unittest.TestCase):
    def setUp(self):
        self.environ = {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_REF": "refs/heads/main",
            "ACTIONS_ID_TOKEN_REQUEST_URL": (
                "https://pipelines.actions.githubusercontent.com/abc/token?api-version=2.0"
                "&audience=old-audience"
            ),
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-secret-" + "x" * 60,
        }

    def test_requests_short_lived_audience_bound_token_without_redirects(self):
        token = "header.payload.signature"
        response = FakeResponse({"value": token})
        opener = Mock()
        opener.open.return_value = response
        with patch("engine.github_oidc.build_opener", return_value=opener):
            result = request_github_oidc_token(self.environ, audience="raawaa-engine-staging-fallback")
        self.assertEqual(result, token)
        request = opener.open.call_args.args[0]
        self.assertIn("audience=raawaa-engine-staging-fallback", request.full_url)
        self.assertNotIn("old-audience", request.full_url)
        self.assertEqual(request.get_header("Authorization"),
                         "Bearer " + self.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10.0)

    def test_rejects_non_schedule_or_non_main_context(self):
        for override in (
            {"GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_REF": "refs/heads/feature"},
        ):
            with self.subTest(override=override), self.assertRaises(GithubOidcError):
                request_github_oidc_token(
                    {**self.environ, **override}, audience="raawaa-engine-staging-fallback",
                )

    def test_rejects_untrusted_token_endpoint(self):
        environ = {**self.environ, "ACTIONS_ID_TOKEN_REQUEST_URL": "https://attacker.invalid/token"}
        with self.assertRaisesRegex(GithubOidcError, "endpoint is invalid"):
            request_github_oidc_token(environ, audience="raawaa-engine-staging-fallback")

    def test_does_not_include_request_credential_in_errors(self):
        secret = self.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
        opener = Mock()
        opener.open.side_effect = OSError(secret)
        with patch("engine.github_oidc.build_opener", return_value=opener):
            with self.assertRaises(GithubOidcError) as caught:
                request_github_oidc_token(self.environ, audience="raawaa-engine-staging-fallback")
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
