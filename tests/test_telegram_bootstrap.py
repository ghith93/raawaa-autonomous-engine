"""Offline tests for Telegram setup, secret hygiene, and production safety gates."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import telegram_bootstrap as bootstrap

VALID_TOKEN = "123456789:" + "A" * 40
OTHER_TOKEN = "123456789:" + "B" * 40
SECRET = "test_secret_" + "s" * 32
BOT = "Raawaa_control_bot"
ORIGIN = "https://worker.test"
WEBHOOK = ORIGIN + "/v1/telegram/webhook"


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self, size=-1):
        data = self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode("utf-8")
        return data if size < 0 else data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def run_main(args, token=VALID_TOKEN):
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bootstrap.main(args)
    return code, out.getvalue(), err.getvalue()


def prepared(path, token=VALID_TOKEN, secret=SECRET, chats=""):
    bootstrap.write_env_file(path, {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_WEBHOOK_SECRET": secret,
        "TELEGRAM_ALLOWED_CHAT_IDS": chats,
    })


class TokenAndSecretTests(unittest.TestCase):
    def test_token_validation_and_environment(self):
        for token in ("", "abc", "12345:short", "123456789:" + "A" * 10):
            with self.subTest(token=token), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.read_token(token)
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": VALID_TOKEN}):
            self.assertEqual(bootstrap.read_token(None), VALID_TOKEN)

    def test_random_secrets_are_unique_and_in_telegram_alphabet(self):
        values = {bootstrap.generate_webhook_secret() for _ in range(50)}
        self.assertEqual(len(values), 50)
        self.assertTrue(all(bootstrap.SECRET_RE.fullmatch(value) for value in values))

    def test_explicit_chat_id_must_be_a_positive_safe_integer(self):
        self.assertEqual(bootstrap._parse_chat_ids(["42,42", "99"]), [42, 99])
        for bad in ("0", "-1", "9007199254740992", "42,", "oops", "1\n2"):
            with self.subTest(bad=bad), self.assertRaises(bootstrap.BootstrapError):
                bootstrap._parse_chat_ids([bad])


class TelegramApiTests(unittest.TestCase):
    def call_response(self, payload, *, expect="dict"):
        with patch.object(bootstrap, "urlopen", return_value=Response(payload)):
            return bootstrap._call(VALID_TOKEN, "getMe", {}, expect=expect)

    def test_result_shapes_are_strict_and_never_silently_become_empty(self):
        for kind, value in (("dict", {"username": BOT}), ("list", [{"update_id": 1}]), ("bool", True)):
            with self.subTest(kind=kind):
                self.assertEqual(self.call_response({"ok": True, "result": value}, expect=kind), value)
        for kind, value in (("dict", []), ("list", {}), ("bool", 1), ("bool", "true")):
            with self.subTest(kind=kind, value=value), self.assertRaisesRegex(bootstrap.BootstrapError, "unexpected result type"):
                self.call_response({"ok": True, "result": value}, expect=kind)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "request rejected"):
            self.call_response({"ok": 1, "result": {}})

    def test_api_errors_and_network_failures_never_echo_credentials_or_chat_ids(self):
        description = f"bad {VALID_TOKEN} {SECRET} 123456789012345"
        with patch.object(bootstrap, "urlopen", return_value=Response({
            "ok": False, "error_code": 400, "description": description,
        })):
            with self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap._call(VALID_TOKEN, "setWebhook", {"secret_token": SECRET})
        self.assertEqual(str(caught.exception), "setWebhook failed: Telegram API error 400")
        for exception in (bootstrap.URLError(VALID_TOKEN), TimeoutError(VALID_TOKEN), OSError(VALID_TOKEN)):
            with self.subTest(exception=type(exception).__name__), \
                    patch.object(bootstrap, "urlopen", side_effect=exception), \
                    self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap._call(VALID_TOKEN, "getMe")
            self.assertNotIn(VALID_TOKEN, str(caught.exception))

    def test_redirect_never_forwards_secret_header_to_a_new_destination(self):
        reached = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/sink")
                    self.end_headers()
                else:
                    reached.append(self.headers.get("X-Telegram-Bot-Api-Secret-Token"))
                    self.send_response(200)
                    self.end_headers()
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = bootstrap.Request(f"http://127.0.0.1:{server.server_port}/redirect",
                                        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
            with self.assertRaises(bootstrap.HTTPError) as caught:
                bootstrap.urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 302)
            self.assertEqual(reached, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_invalid_json_is_an_actionable_failure(self):
        with patch.object(bootstrap, "urlopen", return_value=Response(b"<html>")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "non-JSON"):
                bootstrap._call(VALID_TOKEN, "getMe")

    def test_bot_identity_requires_a_real_expected_bot(self):
        me = {"is_bot": True, "username": BOT.lower()}
        with patch.object(bootstrap, "_call", return_value=me):
            self.assertEqual(bootstrap.verify_bot(VALID_TOKEN, BOT), BOT.lower())
            with self.assertRaisesRegex(bootstrap.BootstrapError, "different bot"):
                bootstrap.verify_bot(VALID_TOKEN, "another_bot")
        for response in ({"is_bot": False, "username": BOT}, {"username": BOT}, {"is_bot": True}):
            with self.subTest(response=response), patch.object(bootstrap, "_call", return_value=response):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "valid bot identity"):
                    bootstrap.verify_bot(VALID_TOKEN, BOT)
        with self.assertRaisesRegex(bootstrap.BootstrapError, "username is required"):
            bootstrap.verify_bot(VALID_TOKEN, "")


class DiscoverySafetyTests(unittest.TestCase):
    def test_pending_updates_are_only_candidates_and_not_confirmed(self):
        updates = [
            {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
            {"message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
            {"message": {"chat": {"id": 55, "type": "private"}, "from": {"id": 77, "is_bot": False}}},
            {"message": {"chat": {"id": 88, "type": "private"}, "from": {"id": 88, "is_bot": True}}},
            {"message": {"chat": {"id": 99, "type": "private"}}},
            {"message": {"chat": {"id": -5, "type": "group"}, "from": {"id": -5, "is_bot": False}}},
        ]
        calls = []
        def fake(_token, method, payload=None, **_kwargs):
            calls.append((method, payload))
            return {"username": BOT} if method == "getMe" else updates
        with patch.object(bootstrap, "_call", side_effect=fake):
            ids, username, count = bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertEqual((ids, username, count), ([42], BOT, len(updates)))
        self.assertEqual(calls[1], ("getUpdates", {"timeout": 0, "limit": 100}))
        self.assertNotIn("offset", calls[1][1])
        self.assertNotIn("allowed_updates", calls[1][1])
        self.assertNotIn("deleteWebhook", [method for method, _ in calls])

    def test_conflicting_webhook_is_never_deleted(self):
        calls = []
        def fake(_token, method, *_args, **_kwargs):
            calls.append(method)
            if method == "getMe":
                return {"username": BOT}
            raise bootstrap.BootstrapError("getUpdates failed: HTTP 409 (token not shown)")
        with patch.object(bootstrap, "_call", side_effect=fake):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "webhook was not changed"):
                bootstrap.find_private_chat_ids(VALID_TOKEN)
        self.assertEqual(calls, ["getMe", "getUpdates"])

    def test_first_private_sender_is_never_automatically_authorized(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "secrets.env"
            with patch.object(bootstrap, "_call") as call:
                code, out, err = run_main(["--env-file", str(env)])
            self.assertEqual(code, 2)
            self.assertIn("private updates alone do not authenticate", err)
            self.assertFalse(env.exists())
            call.assert_not_called()
            self.assertNotIn(VALID_TOKEN, out + err)


class WebhookTests(unittest.TestCase):
    def test_url_normalizes_origin_to_exact_webhook_path(self):
        for value in (ORIGIN, ORIGIN + "/", WEBHOOK):
            with self.subTest(value=value):
                self.assertEqual(bootstrap.webhook_url(value), WEBHOOK)
        for bad in ("http://worker.test", "https://worker.test/v1/other", "https://u:p@worker.test",
                    WEBHOOK + "?secret=x", WEBHOOK + "#fragment", "https://worker.test:bad", "https://worker.test\\@evil.test"):
            with self.subTest(value=bad), self.assertRaises(bootstrap.BootstrapError):
                bootstrap.webhook_url(bad)

    def test_diagnostics_report_exact_match_and_redact_actual_url_and_error(self):
        info = {"url": ORIGIN, "pending_update_count": 7,
                "last_error_message": "Wrong response from the webhook: 404 Not Found secret=" + SECRET}
        def fake(_token, method, *_args, **_kwargs):
            return {"is_bot": True, "username": BOT} if method == "getMe" else info
        with patch.object(bootstrap, "_call", side_effect=fake):
            result = bootstrap.diagnose(VALID_TOKEN, WEBHOOK, BOT)
        self.assertEqual(result, {"bot": "@" + BOT, "webhook_matches": False, "pending_updates": 7,
                                  "last_delivery_error": "HTTP 404", "webhook_secret": "unknown"})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(ORIGIN, json.dumps(result))
        self.assertNotIn(VALID_TOKEN, json.dumps(result))
        info["url"] = WEBHOOK
        with patch.object(bootstrap, "_call", side_effect=fake):
            self.assertTrue(bootstrap.diagnose(VALID_TOKEN, ORIGIN, BOT)["webhook_matches"])

    def test_webhook_state_rejects_invalid_url_and_pending_shapes(self):
        for info in ({"url": None, "pending_update_count": 0}, {"url": "", "pending_update_count": True},
                     {"url": "", "pending_update_count": -1}, {"url": ""}):
            with self.subTest(info=info), patch.object(bootstrap, "_call", return_value=info):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "invalid URL or pending"):
                    bootstrap.webhook_state(VALID_TOKEN)

    def test_register_uses_exact_path_and_does_not_drop_pending(self):
        calls = []
        def fake(_token, method, payload=None, **kwargs):
            calls.append((method, payload, kwargs))
            if method == "setWebhook":
                return True
            return {"url": WEBHOOK, "pending_update_count": 0 if len(calls) == 1 else 3}
        with patch.object(bootstrap, "_call", side_effect=fake):
            state = bootstrap.register(VALID_TOKEN, ORIGIN, SECRET, [42])
        self.assertEqual([entry[0] for entry in calls], ["getWebhookInfo", "setWebhook", "getWebhookInfo"])
        payload = calls[1][1]
        self.assertEqual(payload["url"], WEBHOOK)
        self.assertEqual(payload["secret_token"], SECRET)
        self.assertIs(payload["drop_pending_updates"], False)
        self.assertEqual(payload["allowed_updates"], ["message"])
        self.assertEqual(state["pending_updates"], 3)
        self.assertIsNone(state["secret_token_set"])

    def test_registration_fails_on_false_result_or_wrong_readback(self):
        empty_queue = {"url": WEBHOOK, "pending_update_count": 0}
        with patch.object(bootstrap, "_call", side_effect=[empty_queue, False]) as call:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "did not report success"):
                bootstrap.register(VALID_TOKEN, WEBHOOK, SECRET, [42])
        self.assertEqual(call.call_count, 2)
        with patch.object(bootstrap, "_call", side_effect=[empty_queue, True, {"url": ORIGIN, "pending_update_count": 0}]):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "exact requested URL"):
                bootstrap.register(VALID_TOKEN, WEBHOOK, SECRET, [42])

    def test_registration_refuses_a_known_nonempty_pending_queue_before_setwebhook(self):
        with patch.object(bootstrap, "_call", return_value={"url": ORIGIN, "pending_update_count": 2}) as call:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "pending updates detected; setWebhook was not called"):
                bootstrap.register(VALID_TOKEN, WEBHOOK, SECRET, [42])
        self.assertEqual([item.args[1] for item in call.call_args_list], ["getWebhookInfo"])

    def test_check_fails_for_origin_even_when_https_url_is_set(self):
        with patch.object(bootstrap, "_call", return_value={"url": ORIGIN, "pending_update_count": 0}):
            code, out, err = run_main(["--check", "--url", WEBHOOK])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["webhook_matches"])
        self.assertNotIn(ORIGIN, out + err)
        self.assertNotIn(VALID_TOKEN, out + err)


class CommandMenuTests(unittest.TestCase):
    def test_menu_is_snapshotted_set_and_read_back_in_full(self):
        calls = []
        def fake(_token, method, payload=None, **kwargs):
            calls.append((method, payload, kwargs))
            if method == "setMyCommands":
                return True
            return [] if len(calls) == 1 else list(bootstrap.BOT_COMMANDS)
        with patch.object(bootstrap, "_call", side_effect=fake):
            bootstrap.configure_commands(VALID_TOKEN)
        self.assertEqual([c[0] for c in calls], ["getMyCommands", "setMyCommands", "getMyCommands"])
        self.assertEqual([c["command"] for c in calls[1][1]["commands"]], ["start", "help", "status", "report"])

    def test_matching_menu_is_not_changed(self):
        with patch.object(bootstrap, "_call", return_value=list(bootstrap.BOT_COMMANDS)) as call:
            bootstrap.configure_commands(VALID_TOKEN)
        self.assertEqual([c.args[1] for c in call.call_args_list], ["getMyCommands"])

    def test_failed_set_restores_previous_menu_and_reports_verified_rollback(self):
        previous = [{"command": "old", "description": "previous command"}]
        with patch.object(bootstrap, "_call", side_effect=[previous, False, True, previous]) as call:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "did not report success; previous menu read back"):
                bootstrap.configure_commands(VALID_TOKEN)
        self.assertEqual(call.call_args_list[2].args[2], {"commands": previous})

    def test_mismatching_readback_and_failed_rollback_are_not_success(self):
        with patch.object(bootstrap, "_call", side_effect=[[], True, [], False]):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "readback did not match.*rollback unverified"):
                bootstrap.configure_commands(VALID_TOKEN)

    def test_malformed_existing_menu_is_never_overwritten(self):
        with patch.object(bootstrap, "_call", return_value=[{"command": "missing_description"}]) as call:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "no changes made"):
                bootstrap.configure_commands(VALID_TOKEN)
        self.assertEqual(call.call_count, 1)

    def test_menu_operation_checks_bot_identity_before_mutation(self):
        with patch.object(bootstrap, "_call", return_value={"is_bot": True, "username": "another_bot"}) as call:
            code, _out, err = run_main(["--configure-commands", "--expected-bot", BOT])
        self.assertEqual(code, 2)
        self.assertIn("different bot", err)
        self.assertEqual([c.args[1] for c in call.call_args_list], ["getMe"])


class PreparedFileTests(unittest.TestCase):
    def test_mode_0600_and_atomic_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            path.write_text("old contents")
            path.chmod(0o666)
            prepared(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(bootstrap.read_prepared_env(path)["TELEGRAM_BOT_TOKEN"], VALID_TOKEN)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["secrets.env"])

    def test_symlink_hardlink_and_fifo_are_never_followed_truncated_or_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("important data")
            link = root / "sym"
            link.symlink_to(target)
            hardlink = root / "hard"
            os.link(target, hardlink)
            fifo = root / "pipe"
            os.mkfifo(fifo)
            for path in (link, hardlink, fifo):
                with self.subTest(path=path.name), self.assertRaises(bootstrap.BootstrapError):
                    prepared(path)
                self.assertEqual(target.read_text(), "important data")
                with self.subTest(read=path.name), self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.read_prepared_env(path)

    def test_read_rejects_group_access_duplicates_unknown_keys_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            prepared(path)
            path.chmod(0o640)
            with self.assertRaisesRegex(bootstrap.BootstrapError, "private"):
                bootstrap.read_prepared_env(path)
            for extra in ("TELEGRAM_BOT_TOKEN=x\n", "OTHER=x\n", "X" * 4100):
                prepared(path)
                with path.open("a") as handle:
                    handle.write(extra)
                with self.subTest(extra=extra[:10]), self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.read_prepared_env(path)

    def test_rearm_only_prepares_and_never_registers_or_changes_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            with patch.object(bootstrap, "_call", return_value={"username": BOT, "is_bot": True}) as call:
                code, out, err = run_main(["--rearm-only", "--url", ORIGIN, "--expected-bot", BOT,
                                           "--env-file", str(path)])
            self.assertEqual(code, 0, err)
            self.assertEqual([c.args[1] for c in call.call_args_list], ["getMe"])
            self.assertEqual(bootstrap.read_prepared_env(path)["TELEGRAM_ALLOWED_CHAT_IDS"], "")
            self.assertFalse(json.loads(out)["registered"])
            self.assertNotIn(VALID_TOKEN, out + err)
            self.assertNotIn(bootstrap.read_prepared_env(path)["TELEGRAM_WEBHOOK_SECRET"], out + err)

    def test_register_prepared_checks_bot_and_registers_after_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            prepared(path)
            def fake(_token, method, _payload=None, **_kwargs):
                if method == "getMe":
                    return {"is_bot": True, "username": BOT}
                if method == "setWebhook":
                    return True
                return {"url": WEBHOOK, "pending_update_count": 0}
            with patch.object(bootstrap, "_call", side_effect=fake) as call:
                code, out, err = run_main(["--register-prepared", "--url", ORIGIN,
                                           "--expected-bot", BOT, "--env-file", str(path)])
            self.assertEqual(code, 0, err)
            self.assertEqual([c.args[1] for c in call.call_args_list], ["getMe", "getWebhookInfo", "setWebhook", "getWebhookInfo"])
            self.assertNotIn(VALID_TOKEN, out + err)
            self.assertNotIn(SECRET, out + err)
            self.assertTrue(json.loads(out)["webhook_matches"])

    def test_prepared_token_mismatch_and_deprecated_register_fail_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            prepared(path, token=OTHER_TOKEN)
            with patch.object(bootstrap, "_call") as call:
                code, out, err = run_main(["--register-prepared", "--url", WEBHOOK,
                                           "--expected-bot", BOT, "--env-file", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("do not match", err)
            call.assert_not_called()
            self.assertNotIn(OTHER_TOKEN, out + err)
            with patch.object(bootstrap, "_call") as call:
                code, _out, err = run_main(["--register", "--url", WEBHOOK])
            self.assertEqual(code, 2)
            self.assertIn("unsafe before Worker secret installation", err)
            call.assert_not_called()

    def test_explicit_verified_chat_is_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.env"
            chat_id = "893456712345"
            code, out, err = run_main(["--chat-id", chat_id, "--env-file", str(path), "--url", ORIGIN])
            self.assertEqual(code, 0, err)
            self.assertEqual(bootstrap.read_prepared_env(path)["TELEGRAM_ALLOWED_CHAT_IDS"], chat_id)
            self.assertNotIn(chat_id, out + err)
            self.assertNotIn(VALID_TOKEN, out + err)
            code, out, err = run_main(["--chat-id", chat_id + "oops", "--env-file", str(path)])
            self.assertEqual(code, 2)
            self.assertNotIn(chat_id, out + err)


class ProbeTests(unittest.TestCase):
    def test_unsigned_probe_requires_the_exact_worker_401_gate_without_secret(self):
        requests = []
        def fake(request, timeout=None):
            requests.append(request)
            raise bootstrap.HTTPError(request.full_url, 401, "unauthorized", {},
                                      io.BytesIO(b'{"error":"unauthorized"}'))
        with patch.object(bootstrap, "urlopen", side_effect=fake):
            bootstrap.probe_unsigned_readiness(WEBHOOK)
        self.assertEqual(json.loads(requests[0].data), {})
        self.assertEqual(requests[0].get_header("User-agent"), bootstrap.USER_AGENT)
        self.assertIsNone(requests[0].get_header("X-telegram-bot-api-secret-token"))
        for status, body in ((503, b'{"error":"webhook_disabled"}'), (403, b'{}'),
                             (401, b'{"error":"something_else"}')):
            error = bootstrap.HTTPError(WEBHOOK, status, "failure", {}, io.BytesIO(body))
            with self.subTest(status=status, body=body), patch.object(bootstrap, "urlopen", side_effect=error):
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.probe_unsigned_readiness(WEBHOOK)

    def test_cli_rejects_command_line_secrets_without_echoing_them(self):
        for flag, value in (("--token", VALID_TOKEN), ("--secret", SECRET)):
            with self.subTest(flag=flag):
                code, out, err = run_main([flag, value, "--rearm-only", "--url", WEBHOOK])
                self.assertEqual(code, 2)
                self.assertNotIn(value, out + err)
                self.assertIn("disabled", err)

    def test_probe_has_no_sender_and_requires_exact_boolean_response(self):
        requests = []
        def fake(request, timeout=None):
            requests.append(request)
            return Response({"ok": True, "ignored": True})
        with patch.object(bootstrap, "urlopen", side_effect=fake):
            self.assertEqual(bootstrap.probe_signed_ignored(ORIGIN, SECRET), 200)
        payload = json.loads(requests[0].data)
        self.assertNotIn("from", payload["message"])
        self.assertEqual(requests[0].full_url, WEBHOOK)
        self.assertEqual(requests[0].get_header("User-agent"), bootstrap.USER_AGENT)
        self.assertEqual(requests[0].get_header("X-telegram-bot-api-secret-token"), SECRET)
        for value in ({"ok": 1, "ignored": 1}, {"ok": True}, {"ok": True, "ignored": True, "extra": True}):
            with self.subTest(value=value), patch.object(bootstrap, "urlopen", return_value=Response(value)):
                with self.assertRaisesRegex(bootstrap.BootstrapError, "unexpected response"):
                    bootstrap.probe_signed_ignored(WEBHOOK, SECRET)

    def test_transient_failure_is_retried_and_failure_does_not_leak_secret(self):
        responses = [bootstrap.URLError("secret " + SECRET), Response({"ok": True, "ignored": True})]
        with patch.object(bootstrap, "urlopen", side_effect=responses), patch("time.sleep") as sleep:
            self.assertEqual(bootstrap.probe_signed_ignored(WEBHOOK, SECRET), 200)
            sleep.assert_called_once()
        with patch.object(bootstrap, "urlopen", side_effect=bootstrap.URLError("secret " + SECRET)):
            with self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap.probe_signed_ignored(WEBHOOK, SECRET, attempts=1)
        self.assertNotIn(SECRET, str(caught.exception))


class WorkflowSafetyTests(unittest.TestCase):
    def test_workflow_keeps_read_only_diagnostics_and_orders_repair_steps(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/telegram-register.yml").read_text()
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("inputs.confirm == 'REGISTER_TELEGRAM_WEBHOOK'", workflow)
        self.assertIn("default: diagnose", workflow)
        self.assertIn("VERIFIED_EXISTING_ALLOWLIST", workflow)
        self.assertLess(workflow.index("Require independent allowlist verification"), workflow.index("--diagnose"))
        self.assertIn("::add-mask::", workflow)
        self.assertNotIn("${{ inputs.chat_id }}", workflow)
        self.assertNotIn("deleteWebhook", workflow)
        self.assertNotIn("SELECT chat_id", workflow)
        self.assertLess(workflow.index("--diagnose"), workflow.index("--rearm-only"))
        self.assertLess(workflow.index("--rearm-only"), workflow.index("wrangler@4.156.1 secret put"))
        self.assertLess(workflow.index("wrangler@4.156.1 secret put"), workflow.index("--register-prepared"))
        self.assertLess(workflow.index("--probe-prepared"), workflow.index("--register-prepared"))
        self.assertLess(workflow.index("--register-prepared"), workflow.rindex("--probe-prepared"))
        for name in ("Configure and verify read-only command menu", "Prepare a new webhook secret without changing Telegram",
                     "Install bot, owner allowlist, and staging webhook secrets", "Register and verify the exact webhook URL",
                     "Verify a signed ignored update without sending a message"):
            block = workflow.split("- name: " + name, 1)[1].split("\n      - name:", 1)[0]
            self.assertIn("if: inputs.mode == 'repair'", block)
        self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS)", workflow)
        self.assertIn('[ -z "$value" ]', workflow)


if __name__ == "__main__":
    unittest.main()
