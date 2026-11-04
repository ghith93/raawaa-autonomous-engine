import json
import tempfile
import unittest
from pathlib import Path

from engine.trusted_main_policy import (
    TRUSTED_REMOTE_HTTPS,
    verify_commit_is_on_main,
    verify_trusted_checkout,
)


COMMIT = "a" * 40
MAIN = "b" * 40


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class FakeResponse:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.raw


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None
    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class TrustedMainPolicyTests(unittest.TestCase):
    def _runner(self, root, overrides=None):
        values = {
            ("git", "rev-parse", "--show-toplevel"): str(root),
            ("git", "branch", "--show-current"): "main",
            ("git", "rev-parse", "HEAD"): COMMIT,
            ("git", "status", "--porcelain", "--untracked-files=all"): "",
            ("git", "remote", "get-url", "origin"): "https://github.com/ghith93/raawaa-autonomous-engine.git",
        }
        values.update(overrides or {})
        def run(command, **kwargs):
            self.assertEqual(kwargs["cwd"], str(root.resolve()))
            self.assertFalse(kwargs["shell"])
            self.assertLessEqual(kwargs["timeout"], 10)
            return FakeCompleted(values.get(tuple(command), ""), 0)
        return run

    def test_accepts_clean_canonical_main_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root, runner=self._runner(root),
                commit_verifier=lambda sha: sha == COMMIT,
            )
        self.assertTrue(result["trusted"])
        self.assertEqual(result["commit"], COMMIT)

    def test_rejects_non_main_branch_before_network_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root,
                runner=self._runner(root, {
                    ("git", "branch", "--show-current"): "feature/unreviewed",
                }),
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "branch_not_main")

    def test_rejects_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root,
                runner=self._runner(root, {
                    ("git", "status", "--porcelain", "--untracked-files=all"): " M engine/worker.py",
                }),
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "working_tree_not_clean")

    def test_rejects_noncanonical_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root,
                runner=self._runner(root, {
                    ("git", "remote", "get-url", "origin"): "https://github.com/fork/raawaa-autonomous-engine.git",
                }),
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "origin_not_canonical")

    def test_rejects_commit_not_reachable_from_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root, runner=self._runner(root), commit_verifier=lambda sha: False,
            )
        self.assertEqual(result["reason"], "commit_not_on_canonical_main")

    def test_compare_api_accepts_only_ancestor_or_identical(self):
        for status in ("ahead", "identical"):
            opener = FakeOpener(FakeResponse({
                "status": status,
                "base_commit": {"sha": COMMIT},
                "head_commit": {"sha": MAIN},
            }))
            self.assertTrue(verify_commit_is_on_main(
                COMMIT, opener_factory=lambda handler, opener=opener: opener,
            ))
            self.assertIn(f"/compare/{COMMIT}...main", opener.request.full_url)
            self.assertEqual(opener.timeout, 10)

        for status in ("behind", "diverged"):
            opener = FakeOpener(FakeResponse({
                "status": status,
                "base_commit": {"sha": COMMIT},
                "head_commit": {"sha": MAIN},
            }))
            self.assertFalse(verify_commit_is_on_main(
                COMMIT, opener_factory=lambda handler, opener=opener: opener,
            ))

    def test_invalid_commit_sha_fails_without_network(self):
        self.assertFalse(verify_commit_is_on_main("not-a-sha", opener_factory=lambda handler: self.fail()))


    def test_rejects_workspace_root_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root,
                runner=self._runner(root, {
                    ("git", "rev-parse", "--show-toplevel"): str(root / "nested"),
                }),
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "workspace_root_mismatch")

    def test_git_preflight_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def failed_runner(command, **kwargs):
                return FakeCompleted("", returncode=1)
            result = verify_trusted_checkout(
                root, runner=failed_runner,
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "git_preflight_failed")

    def test_compare_api_network_and_http_errors_fail_closed(self):
        from urllib.error import HTTPError, URLError
        class BrokenOpener:
            def __init__(self, error):
                self.error = error
            def open(self, request, timeout):
                raise self.error
        errors = [
            URLError("offline"),
            HTTPError("https://api.github.com/", 403, "forbidden", None, None),
        ]
        for error in errors:
            self.assertFalse(verify_commit_is_on_main(
                COMMIT, opener_factory=lambda handler, error=error: BrokenOpener(error),
            ))

    def test_compare_api_oversized_and_invalid_json_fail_closed(self):
        class RawResponse(FakeResponse):
            def __init__(self, raw):
                self.raw = raw
        oversized = FakeOpener(RawResponse(b"x" * (64 * 1024 + 1)))
        invalid_json = FakeOpener(RawResponse(b"{not-json"))
        for opener in (oversized, invalid_json):
            self.assertFalse(verify_commit_is_on_main(
                COMMIT, opener_factory=lambda handler, opener=opener: opener,
            ))

    def test_compare_api_rejects_invalid_head_sha(self):
        opener = FakeOpener(FakeResponse({
            "status": "ahead",
            "base_commit": {"sha": COMMIT},
            "head_commit": {"sha": "not-a-sha"},
        }))
        self.assertFalse(verify_commit_is_on_main(
            COMMIT, opener_factory=lambda handler: opener,
        ))

    def test_invalid_local_commit_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = verify_trusted_checkout(
                root,
                runner=self._runner(root, {
                    ("git", "rev-parse", "HEAD"): "not-a-sha",
                }),
                commit_verifier=lambda sha: self.fail("must not call GitHub"),
            )
        self.assertEqual(result["reason"], "commit_sha_invalid")

    def test_missing_or_invalid_github_evidence_fails_closed(self):
        opener = FakeOpener(FakeResponse({"status": "ahead", "base_commit": {"sha": "wrong"}}))
        self.assertFalse(verify_commit_is_on_main(
            COMMIT, opener_factory=lambda handler: opener,
        ))


if __name__ == "__main__":
    unittest.main()
