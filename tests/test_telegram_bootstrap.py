import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import telegram_bootstrap as bootstrap

VALID_TOKEN = "123456789:" + "A" * 40


class GenerateSecretTest(unittest.TestCase):
    def test_generated_secret_is_inside_telegrams_alphabet_and_bounds(self):
        for _ in range(20):
            value = bootstrap.generate_webhook_secret()
            self.assertTrue(bootstrap.SECRET_RE.fullmatch(value), value)
            self.assertGreaterEqual(len(value), 16)
            self.assertLessEqual(len(value), 256)

    def test_generated_secrets_do_not_repeat(self):
        values = {bootstrap.generate_webhook_secret() for _ in range(50)}
        self.assertEqual(len(values), 50)


class TokenInputTest(unittest.TestCase):
    def test_a_malformed_token_is_refused_before_any_network_call(self):
        for bad in ("", "abc", "12345:short", "123456789:" + "A" * 10):
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.read_token(bad)

    def test_a_well_formed_token_is_accepted_from_the_environment(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
            self.assertEqual(bootstrap.read_token(None), VALID_TOKEN)


class ChatDiscoveryTest(unittest.TestCase):
    def test_only_private_human_chats_with_matching_sender_are_accepted(self):
        replies = {
            "getMe": {"username": "raawaa_bot"},
            "getUpdates": [
                {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
                {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
                {"message": {"chat": {"id": 43, "type": "private"}, "from": {"id": 99, "is_bot": False}}},
                {"message": {"chat": {"id": 44, "type": "private"}, "from": {"id": 44, "is_bot": True}}},
                {"message": {"chat": {"id": 45, "type": "private"}}},
                {"message": {"chat": {"id": -100123, "type": "channel"}, "from": {"id": 1, "is_bot": False}}},
                {"message": {"chat": {"id": -5, "type": "group"}, "from": {"id": 1, "is_bot": False}}},
                {"edited_message": {"chat": {"id": 99, "type": "private"}, "from": {"id": 99, "is_bot": False}}},
            ],
        }

        def fake_call(token, method, payload=None, *, timeout=15.0, expect="dict"):
            self.assertEqual(token, VALID_TOKEN)
            return replies[method]

        with patch.object(bootstrap, "_call", side_effect=fake_call):
            chat_ids, username, seen = bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertEqual(chat_ids, [42])
        self.assertEqual(username, "raawaa_bot")
        self.assertEqual(seen, 8)

    def test_multiple_private_humans_are_not_all_added_as_owners(self):
        updates = [
            {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
            {"message": {"chat": {"id": 43, "type": "private"}, "from": {"id": 43, "is_bot": False}}},
        ]
        def fake_call(token, method, payload=None, **kwargs):
            return {"username": "raawaa_bot"} if method == "getMe" else updates
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "multiple private human chats"):
                bootstrap.find_private_chat_ids(VALID_TOKEN)

    def test_no_pending_updates_yields_no_chat(self):
        def fake_call(token, method, payload=None, **kwargs):
            return {"username": "raawaa_bot"} if method == "getMe" else []
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            chat_ids, _, seen = bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertEqual(seen, 0)
        self.assertEqual(chat_ids, [])


class RerunTest(unittest.TestCase):
    """Re-runs must not silently interrupt an existing webhook."""

    def test_a_409_does_not_delete_webhook_without_explicit_opt_in(self):
        calls = []
        def fake_call(token, method, payload=None, **kwargs):
            calls.append(method)
            if method == "getMe":
                return {"username": "raawaa_bot"}
            raise bootstrap.BootstrapError("getUpdates failed: HTTP 409 (token not shown)")
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "--replace-webhook-to-discover"):
                bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertNotIn("deleteWebhook", calls)

    def test_explicit_replace_preserves_pending_updates_and_retries(self):
        calls = []
        updates_after = [{
            "update_id": 2,
            "message": {
                "chat": {"id": 55, "type": "private"},
                "from": {"id": 55, "is_bot": False},
            },
        }]
        def fake_call(token, method, payload=None, **kwargs):
            calls.append((method, payload))
            if method == "getMe":
                return {"username": "raawaa_bot"}
            if method == "getUpdates":
                if sum(1 for name, _ in calls if name == "getUpdates") == 1:
                    raise bootstrap.BootstrapError("getUpdates failed: HTTP 409 (token not shown)")
                return updates_after
            if method == "deleteWebhook":
                self.assertFalse(payload["drop_pending_updates"])
                return True
            raise AssertionError(method)
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            chat_ids, username, seen = bootstrap.find_private_chat_ids(VALID_TOKEN, replace_webhook=True)
        self.assertEqual(chat_ids, [55])
        self.assertEqual(username, "raawaa_bot")
        self.assertEqual(seen, 1)
        self.assertEqual(sum(1 for name, _ in calls if name == "deleteWebhook"), 1)

    def test_another_api_error_is_not_swallowed(self):
        def fake_call(token, method, payload=None, **kwargs):
            if method == "getMe":
                return {"username": "raawaa_bot"}
            raise bootstrap.BootstrapError("getUpdates failed: HTTP 500 (token not shown)")
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "HTTP 500"):
                bootstrap.find_private_chat_ids(VALID_TOKEN)


class RearmTest(unittest.TestCase):
    """Re-arming must work when no chat id can be discovered anywhere."""

    def test_rearm_registers_without_a_chat_id_and_leaves_the_allowlist_empty(self):
        calls = []

        def fake_call(token, method, payload=None, **kwargs):
            calls.append(method)
            if method == "getMe":
                return {"username": "raawaa_bot"}
            if method == "getWebhookInfo":
                return {"url": "https://w.example/v1/telegram/webhook", "pending_update_count": 0}
            return True

        env_file = Path(tempfile.mkdtemp()) / "telegram.env"
        with patch.object(bootstrap, "_call", side_effect=fake_call):
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = bootstrap.main([
                        "--url", "https://w.example/v1/telegram/webhook",
                        "--env-file", str(env_file), "--rearm-only",
                    ])
        self.assertEqual(code, 0)
        self.assertIn("setWebhook", calls)
        self.assertNotIn("getUpdates", calls, "re-arm must not depend on a discoverable chat")
        written = dict(
            line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line
        )
        self.assertEqual(written["TELEGRAM_ALLOWED_CHAT_IDS"], "",
                         "an empty value tells the workflow to leave the Worker's allowlist alone")
        self.assertTrue(written["TELEGRAM_WEBHOOK_SECRET"])
        self.assertEqual(oct(env_file.stat().st_mode)[-3:], "600")

    def test_rearm_still_requires_a_url(self):
        with patch.object(bootstrap, "_call", side_effect=lambda *a, **k: {"username": "b"}):
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
                with contextlib.redirect_stderr(io.StringIO()):
                    code = bootstrap.main(["--env-file", "/tmp/x.env", "--rearm-only"])
        self.assertEqual(code, 2)


class ResultShapeTest(unittest.TestCase):
    """Bot API results are not all objects; getUpdates is a list."""

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_a_list_result_is_returned_as_a_list(self):
        payload = {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}}},
        ]}
        with patch.object(bootstrap, "urlopen", return_value=self._Response(payload)):
            result = bootstrap._call(VALID_TOKEN, "getUpdates", {}, expect="list")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["message"]["chat"]["id"], 42)

    def test_the_same_call_without_the_list_expectation_would_have_hidden_it(self):
        # This is the bug, pinned: silently {} instead of the seven pending updates
        # Telegram was reporting, which read as "the owner never messaged the bot".
        payload = {"ok": True, "result": [{"update_id": 1}]}
        with patch.object(bootstrap, "urlopen", return_value=self._Response(payload)):
            as_dict = bootstrap._call(VALID_TOKEN, "getUpdates", {})
        self.assertEqual(as_dict, {})

    def test_discovery_finds_a_chat_from_a_real_list_result(self):
        updates = {"ok": True, "result": [
            {"update_id": 7, "message": {"chat": {"id": 4242, "type": "private"}}},
        ]}
        me = {"ok": True, "result": {"username": "raawaa_bot", "is_bot": True}}

        def fake_urlopen(request, timeout=None):
            return self._Response(me if "/getMe" in request.full_url else updates)

        with patch.object(bootstrap, "urlopen", side_effect=fake_urlopen):
            chat_ids, username, seen = bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertEqual(chat_ids, [4242])
        self.assertEqual(username, "raawaa_bot")
        self.assertEqual(seen, 1)


class ErrorHygieneTest(unittest.TestCase):
    def test_an_api_failure_never_echoes_the_token(self):
        with patch.object(bootstrap, "urlopen", side_effect=bootstrap.URLError("boom")):
            with self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap._call(VALID_TOKEN, "getMe")
        self.assertNotIn(VALID_TOKEN, str(caught.exception))
        self.assertNotIn("A" * 40, str(caught.exception))

    def test_a_rejected_request_reports_telegrams_description_not_the_url(self):
        class FakeResponse:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        body = json.dumps({"ok": False, "description": "Bad Request: invalid token"}).encode()
        with patch.object(bootstrap, "urlopen", return_value=FakeResponse(body)):
            with self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap._call(VALID_TOKEN, "getMe")
        self.assertIn("invalid token", str(caught.exception))
        self.assertNotIn(VALID_TOKEN, str(caught.exception))


class EnvFileTest(unittest.TestCase):
    def test_the_env_file_is_written_read_only_for_the_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "telegram.env"
            bootstrap.write_env_file(path, {"A": "1", "B": "2"})
            mode = path.stat().st_mode
            self.assertEqual(stat.S_IMODE(mode), 0o600)
            self.assertEqual(path.read_text(), "A=1\nB=2\n")

    def test_rewriting_tightens_permissions_rather_than_keeping_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telegram.env"
            path.write_text("OLD=1\n")
            path.chmod(0o644)
            bootstrap.write_env_file(path, {"A": "1"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_text(), "A=1\n")


class WebhookStateTest(unittest.TestCase):
    def test_secret_token_presence_is_unknown_not_inferred_from_tls_certificate(self):
        for custom_certificate in (False, True):
            with self.subTest(has_custom_certificate=custom_certificate):
                with patch.object(bootstrap, "_call", return_value={
                    "url": "https://worker.test/v1/telegram/webhook",
                    "pending_update_count": 0,
                    "last_error_message": "",
                    "has_custom_certificate": custom_certificate,
                }):
                    state = bootstrap.webhook_state(VALID_TOKEN)
                self.assertEqual(state["url"], "https://worker.test/v1/telegram/webhook")
                self.assertIsNone(
                    state["secret_token_set"],
                    "getWebhookInfo does not disclose secret_token presence",
                )


class RegisterTest(unittest.TestCase):
    def test_registration_refuses_an_insecure_url(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.register(VALID_TOKEN, "http://example.test/hook", "x" * 32, [1])

    def test_setwebhook_payload_carries_the_secret_and_drops_stale_updates(self):
        calls = []

        def fake_call(token, method, payload=None, *, timeout=15.0, expect="dict"):
            calls.append((method, payload))
            if method == "getWebhookInfo":
                return {"url": "https://worker.test/v1/telegram/webhook", "pending_update_count": 0}
            return {}

        with patch.object(bootstrap, "_call", side_effect=fake_call):
            state = bootstrap.register(VALID_TOKEN, "https://worker.test/v1/telegram/webhook", "s" * 32, [42])

        method, payload = calls[0]
        self.assertEqual(method, "setWebhook")
        self.assertEqual(payload["secret_token"], "s" * 32)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertTrue(payload["drop_pending_updates"])
        self.assertTrue(state["url"].startswith("https://"))


class MainFlowTest(unittest.TestCase):
    def test_dry_run_writes_the_file_and_says_what_to_do_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "telegram.env"
            replies = {
                "getMe": {"username": "raawaa_bot"},
                "getUpdates": [{"message": {"chat": {"id": 7, "type": "private"}}}],
            }
            with patch.object(bootstrap, "_call", side_effect=lambda t, m, p=None, **k: replies[m]):
                with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = bootstrap.main(["--env-file", str(env_file)])
            self.assertEqual(code, 0)
            written = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
            self.assertEqual(written["TELEGRAM_ALLOWED_CHAT_IDS"], "7")
            self.assertTrue(bootstrap.SECRET_RE.fullmatch(written["TELEGRAM_WEBHOOK_SECRET"]))

    def test_register_without_a_url_fails_with_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            replies = {
                "getMe": {"username": "raawaa_bot"},
                "getUpdates": [{"message": {"chat": {"id": 7, "type": "private"}}}],
            }
            with patch.object(bootstrap, "_call", side_effect=lambda t, m, p=None, **k: replies[m]):
                with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        code = bootstrap.main(["--env-file", str(Path(tmp) / "e"), "--register"])
            self.assertEqual(code, 2)

    def test_a_bad_token_exits_two_without_touching_the_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "telegram.env"
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "nope"}):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = bootstrap.main(["--env-file", str(target)])
            self.assertEqual(code, 2)
            self.assertFalse(target.exists())


class NoChatFoundTest(unittest.TestCase):
    """The failure an owner actually hits has to carry the fix, not just the fact."""

    def test_the_error_names_the_bot_and_links_to_it(self):
        import contextlib, io
        from unittest.mock import patch

        from scripts import telegram_bootstrap as bootstrap

        # _call() already unwraps "result", so these are the payloads it returns.
        replies = {
            "getMe": {"username": "raawaa_control_bot", "is_bot": True},
            "getUpdates": [],
            # reached only on the failure path, to report webhook/pending state
            "getWebhookInfo": {"url": "", "pending_update_count": 0},
        }
        env_file = Path(tempfile.mkdtemp()) / "telegram.env"
        with patch.object(bootstrap, "_call", side_effect=lambda t, m, p=None, **k: replies[m]):
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    code = bootstrap.main(["--env-file", str(env_file)])
        self.assertEqual(code, 2)
        message = err.getvalue()
        self.assertIn("https://t.me/raawaa_control_bot", message)
        self.assertIn("no private chat found", message)
        self.assertNotIn(VALID_TOKEN, message)
        self.assertFalse(env_file.exists(), "nothing may be written when discovery fails")


if __name__ == "__main__":
    unittest.main()
