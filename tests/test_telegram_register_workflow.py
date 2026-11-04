"""Execute the real registration workflow shell steps against fully offline fakes.

This is not evidence about GitHub Actions, Telegram or Cloudflare production.
The fakes reject any network request outside the expected simulated surfaces.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/telegram-register.yml"
TOKEN = "123456789:" + "A" * 40
WEBHOOK_SECRET = "synthetic_webhook_secret_0123456789ABCDEF"
WEBHOOK = "https://worker.test/v1/telegram/webhook"
BOT = "Raawaa_control_bot"

SITE_CUSTOMIZE = r'''
import hashlib, io, json, os, secrets, time
from pathlib import Path
from urllib.error import HTTPError
import urllib.request

time.sleep = lambda _seconds: None
secrets.token_urlsafe = lambda _bytes: os.environ["MOCK_WEBHOOK_SECRET"]
path = Path(os.environ["MOCK_STATE"])
def read():
    return json.loads(path.read_text())
def save(state):
    path.write_text(json.dumps(state))
class Response:
    status = 200
    def __init__(self, result):
        self.result = result
    def read(self):
        return json.dumps(self.result).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
class FakeOpener:
    def open(self, request, timeout=None):
        state = read()
        url = request.full_url
        if url.startswith("https://api.telegram.org/bot"):
            method = url.rsplit("/", 1)[-1]
            state["events"].append("telegram:" + method)
            payload = json.loads(request.data) if request.data else {}
            if method == "getMe":
                result = {"is_bot": True, "username": os.environ.get("MOCK_BOT", "Raawaa_control_bot")}
            elif method == "getWebhookInfo":
                result = {"url": state["webhook"], "pending_update_count": int(os.environ.get("MOCK_PENDING", "0")),
                          "last_error_message": "Wrong response from webhook: 404"}
            elif method == "getMyCommands":
                result = state["menu"]
                if os.environ.get("MOCK_MENU_MISMATCH") == "1" and len(result) == 4:
                    result = []
            elif method == "setMyCommands":
                result = True
                state["menu"] = payload["commands"]
            elif method == "setWebhook":
                assert payload["url"] == os.environ["WEBHOOK_URL"]
                assert payload["drop_pending_updates"] is False
                assert payload["allowed_updates"] == ["message"]
                assert hashlib.sha256(payload["secret_token"].encode()).hexdigest() == state["worker_webhook_hash"]
                assert hashlib.sha256(os.environ["TELEGRAM_BOT_TOKEN"].encode()).hexdigest() == state["worker_token_hash"]
                state["webhook"] = "https://worker.test" if os.environ.get("MOCK_BAD_READBACK") == "1" else payload["url"]
                result = True
            else:
                raise AssertionError("Unexpected Telegram API method: " + method)
            save(state)
            return Response({"ok": True, "result": result})
        if url == os.environ["WEBHOOK_URL"]:
            secret = request.get_header("X-telegram-bot-api-secret-token")
            if not secret:
                state["events"].append("worker:unsigned")
                save(state)
                assert json.loads(request.data) == {}
                if os.environ.get("MOCK_UNSIGNED_DISABLED") == "1":
                    raise HTTPError(url, 503, "disabled", {}, io.BytesIO(b'{"error":"webhook_disabled"}'))
                raise HTTPError(url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"unauthorized"}'))
            state["events"].append("worker:probe")
            save(state)
            assert "from" not in json.loads(request.data)["message"]
            assert hashlib.sha256(secret.encode()).hexdigest() in (state.get("worker_primary_hash"), state.get("worker_staging_hash"))
            if os.environ.get("MOCK_PROBE_FAILURE") == "1" or (os.environ.get("MOCK_POST_PROBE_FAILURE") == "1" and "telegram:setWebhook" in state["events"]):
                raise HTTPError(url, 503, "unavailable", {}, None)
            return Response({"ok": True, "ignored": True})
        raise AssertionError("Unexpected network destination")
urllib.request.build_opener = lambda *_handlers: FakeOpener()
'''

FAKE_NPX = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import Path
path = Path(os.environ["MOCK_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if "secret" in args and "list" in args:
    state["events"].append("worker:secret-list")
    names = [{"name": "TELEGRAM_ALLOWED_CHAT_IDS"}] if os.environ.get("MOCK_NO_ALLOWLIST") != "1" else []
    if state.get("staging_present"):
        names.append({"name": "TELEGRAM_WEBHOOK_SECRET_STAGING"})
    print(json.dumps(names))
elif "d1" in args and "execute" in args:
    state["events"].append("d1:aggregate-read")
    assert "SELECT COUNT(*)" in args[args.index("--command") + 1]
    print(json.dumps([{"results": [{"total": 1, "sent": 1}]}]))
elif "secret" in args and "put" in args:
    key = args[args.index("put") + 1]
    assert key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET_STAGING", "TELEGRAM_ALLOWED_CHAT_IDS")
    value = sys.stdin.buffer.read()
    assert value
    state["events"].append("worker:secret-put:" + key)
    if key == "TELEGRAM_WEBHOOK_SECRET_STAGING":
        state["staging_present"] = True
    if key in ("TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_WEBHOOK_SECRET_STAGING"):
        state["worker_webhook_hash"] = hashlib.sha256(value).hexdigest()
        state["worker_staging_hash" if key.endswith("_STAGING") else "worker_primary_hash"] = hashlib.sha256(value).hexdigest()
    elif key == "TELEGRAM_BOT_TOKEN":
        state["worker_token_hash"] = hashlib.sha256(value).hexdigest()
    if os.environ.get("MOCK_WRANGLER_ECHO") == "1":
        print("untrusted stdout: " + value.decode())
        print("untrusted stderr: " + value.decode(), file=sys.stderr)
    if os.environ.get("MOCK_WRANGLER_FAIL_KEY") == key:
        path.write_text(json.dumps(state))
        sys.exit(1)
elif "secret" in args and "delete" in args:
    key = args[args.index("delete") + 1]
    state["events"].append("worker:secret-delete:" + key)
    if os.environ.get("MOCK_DELETE_FAILURE"):
        if os.environ["MOCK_DELETE_FAILURE"] == "absent":
            state["staging_present"] = False
        path.write_text(json.dumps(state))
        sys.exit(1)
    state["staging_present"] = False
else:
    raise AssertionError("Unexpected npx invocation")
path.write_text(json.dumps(state))
'''


def shell_steps():
    """Extract simple block/inline run steps; YAML syntax is linted separately."""
    text = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^      - name: ", text)[1:]
    steps = []
    for block in blocks:
        name = block.splitlines()[0]
        match = re.search(r"(?m)^        run: \|\n((?:(?:          .*|\s*)\n)*)", block)
        inline = re.search(r"(?m)^        run: (?!\|)(.+)$", block)
        if match:
            script = textwrap.dedent(match.group(1))
        elif inline:
            script = inline.group(1)
        else:
            continue
        steps.append((name, block, script))
    return steps


def simulate(mode, **extra):
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        bins = temp / "bin"
        bins.mkdir()
        (bins / "npx").write_text(FAKE_NPX)
        (bins / "npx").chmod(0o700)
        (temp / "sitecustomize.py").write_text(SITE_CUSTOMIZE)
        state = temp / "state.json"
        state.write_text(json.dumps({"webhook": "https://worker.test", "menu": [], "events": []}))
        env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ.get("PATH", ""),
                   PYTHONPATH=str(temp), MOCK_STATE=str(state), TELEGRAM_BOT_TOKEN=TOKEN,
                   CLOUDFLARE_API_TOKEN="synthetic-cf-token", CLOUDFLARE_ACCOUNT_ID="synthetic-account",
                   WEBHOOK_URL=WEBHOOK, EXPECTED_BOT=BOT, MODE=mode,
                   OWNER_BINDING="VERIFIED_EXISTING_ALLOWLIST", OWNER_CHAT_ID="123456789", MOCK_WEBHOOK_SECRET=WEBHOOK_SECRET,
                   GITHUB_STEP_SUMMARY=str(temp / "summary.txt"))
        env.update(extra)
        failed = False
        outputs = []
        executed = []
        masks = []
        for name, block, script in shell_steps():
            always = "if: always()" in block
            repair_only = "if: inputs.mode == 'repair'" in block or "if: inputs.mode != 'diagnose'" in block
            if failed and not always or repair_only and mode == "diagnose" or "if: inputs.mode == 'diagnose'" in block and mode != "diagnose":
                continue
            for filename in ("telegram.env", "telegram-diagnostic.json", "telegram-worker-secrets.json", "telegram-receipt-counts.json", "telegram-cleanup-status", "telegram-cleanup-inventory.json"):
                script = script.replace("/tmp/" + filename, str(temp / filename))
            syntax = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            if syntax.returncode:
                raise AssertionError(f"{name}: bash syntax error: {syntax.stderr}")
            result = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, text=True, capture_output=True, timeout=20)
            # Emulate the Actions runner consuming ::add-mask:: commands and
            # redacting later output; the mock value is synthetic, never a secret.
            visible = []
            for line in (result.stdout + result.stderr).splitlines():
                if line.startswith("::add-mask::"):
                    masks.append(line[len("::add-mask::"):])
                    visible.append("::add-mask::[MASKED]")
                else:
                    for mask in masks:
                        line = line.replace(mask, "[MASKED]")
                    visible.append(line)
            outputs.append("\n".join(visible))
            executed.append((name, result.returncode))
            if result.returncode:
                failed = True
        outputs.append((temp / "summary.txt").read_text() if (temp / "summary.txt").exists() else "")
        return failed, json.loads(state.read_text()), "\n".join(outputs), executed, (temp / "telegram.env").exists()


class TelegramWorkflowOfflineIntegrationTests(unittest.TestCase):
    def test_diagnose_is_read_only_and_reports_origin_mismatch(self):
        failed, state, output, executed, private_file_left = simulate("diagnose", MOCK_PENDING="3", OWNER_BINDING="")
        self.assertFalse(failed, executed)
        self.assertIn("webhook_matches=False", output)
        self.assertIn("pending_updates=3", output)
        self.assertEqual(state["webhook"], "https://worker.test")
        self.assertEqual(state["events"], ["telegram:getMe", "telegram:getWebhookInfo", "worker:unsigned", "worker:secret-list", "d1:aggregate-read"])
        self.assertNotIn(TOKEN, output)
        self.assertFalse(private_file_left)

    def test_repair_installs_secrets_before_registering_and_leaves_allowlist_alone(self):
        failed, state, output, executed, private_file_left = simulate("repair")
        self.assertFalse(failed, (executed, output))
        events = state["events"]
        self.assertLess(events.index("telegram:getWebhookInfo"), events.index("telegram:setMyCommands"))
        self.assertLess(events.index("worker:secret-put:TELEGRAM_BOT_TOKEN"), events.index("telegram:setWebhook"))
        self.assertLess(events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET_STAGING"), events.index("telegram:setWebhook"))
        probes = [i for i, event in enumerate(events) if event == "worker:probe"]
        self.assertEqual(len(probes), 2)
        self.assertLess(events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET_STAGING"), probes[0])
        self.assertLess(probes[0], events.index("telegram:setWebhook"))
        self.assertLess(events.index("telegram:setWebhook"), probes[1])
        self.assertLess(probes[1], events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET"))
        self.assertLess(events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET"), events.index("worker:secret-delete:TELEGRAM_WEBHOOK_SECRET_STAGING"))
        self.assertIn("worker:secret-put:TELEGRAM_ALLOWED_CHAT_IDS", events)
        self.assertEqual(state["webhook"], WEBHOOK)
        self.assertEqual([entry["command"] for entry in state["menu"]], ["start", "help", "status", "report"])
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(WEBHOOK_SECRET, output)
        self.assertIn("::add-mask::[MASKED]", output)
        self.assertFalse(private_file_left)

    def test_owner_chat_id_secret_provisions_allowlist(self):
        failed, state, output, executed, private_file_left = simulate("repair", OWNER_CHAT_ID="123456789")
        self.assertFalse(failed, (executed, output))
        events = state["events"]
        self.assertIn("worker:secret-put:TELEGRAM_ALLOWED_CHAT_IDS", events)
        self.assertIn("Repository secret OWNER_TELEGRAM_CHAT_ID is configured", output)
        self.assertFalse(private_file_left)

    def test_all_mutating_modes_use_staging_and_require_owner(self):
        for mode in ("repair", "rotation", "wedged-recovery"):
            with self.subTest(mode=mode):
                failed, state, output, executed, _ = simulate(mode, OWNER_CHAT_ID="")
                self.assertTrue(failed)
                self.assertEqual(state["events"], [])
                self.assertIn("Owner authorization unverified", output)
                failed, state, output, executed, _ = simulate(mode)
                self.assertFalse(failed, (executed, output))
                events = state["events"]
                probes = [i for i, event in enumerate(events) if event == "worker:probe"]
                self.assertEqual(len(probes), 2)
                self.assertLess(events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET_STAGING"), probes[0])
                self.assertLess(probes[0], events.index("telegram:setWebhook"))
                self.assertLess(events.index("telegram:setWebhook"), probes[1])
                self.assertLess(probes[1], events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET"))
                self.assertLess(events.index("worker:secret-put:TELEGRAM_WEBHOOK_SECRET"), events.index("worker:secret-delete:TELEGRAM_WEBHOOK_SECRET_STAGING"))

    def test_chat_id_boundaries(self):
        for value, valid in [("0", False), ("-1", False), ("9007199254740992", False), ("9007199254740991", True), ("99999999999999999999", False), ("1", True)]:
            with self.subTest(value=value):
                failed, state, output, executed, _ = simulate("repair", OWNER_CHAT_ID=value)
                self.assertEqual(failed, not valid, (executed, output))
                if not valid:
                    self.assertEqual(state["events"], [])

    def test_cleanup_outcomes(self):
        for result, expected, failed_expected in [("", "retired", False), ("absent", "already-absent", False), ("present", "failed", True)]:
            with self.subTest(result=result):
                failed, state, output, executed, _ = simulate("repair", MOCK_DELETE_FAILURE=result)
                self.assertEqual(failed, failed_expected, (executed, output))
                self.assertIn("Staging cleanup: " + expected, output)
                self.assertIn("worker:secret-delete:TELEGRAM_WEBHOOK_SECRET_STAGING", state["events"])

    def test_wrangler_secret_output_is_suppressed_on_success_and_failure(self):
        for fail_key in ("", "TELEGRAM_WEBHOOK_SECRET_STAGING", "TELEGRAM_WEBHOOK_SECRET"):
            with self.subTest(fail_key=fail_key):
                failed, state, output, _executed, private_file_left = simulate(
                    "repair", MOCK_WRANGLER_ECHO="1", MOCK_WRANGLER_FAIL_KEY=fail_key,
                )
                self.assertEqual(failed, bool(fail_key))
                self.assertNotIn("untrusted stdout", output)
                self.assertNotIn("untrusted stderr", output)
                self.assertNotIn(TOKEN, output)
                self.assertNotIn(WEBHOOK_SECRET, output)
                self.assertIn("::add-mask::[MASKED]", output)
                if fail_key == "TELEGRAM_WEBHOOK_SECRET_STAGING":
                    self.assertIn("provider output withheld", output)
                    self.assertNotIn("telegram:setWebhook", state["events"])
                elif fail_key == "TELEGRAM_WEBHOOK_SECRET":
                    self.assertIn("provider output withheld", output)
                    self.assertIn("telegram:setWebhook", state["events"])
                self.assertFalse(private_file_left)

    def test_missing_allowlist_or_wrong_bot_stops_before_mutations(self):
        for extra in ({"MOCK_BOT": "another_bot"},):
            with self.subTest(extra=extra):
                failed, state, output, _executed, private_file_left = simulate("repair", **extra)
                self.assertTrue(failed)
                self.assertNotIn("telegram:setWebhook", state["events"])
                self.assertNotIn("telegram:setMyCommands", state["events"])
                self.assertFalse(any(event.startswith("worker:secret-put:") for event in state["events"]))
                self.assertNotIn(TOKEN, output)
                self.assertFalse(private_file_left)

    def test_known_pending_queue_blocks_repair_before_any_mutation(self):
        failed, state, output, _executed, private_file_left = simulate("repair", MOCK_PENDING="3")
        self.assertTrue(failed)
        self.assertIn("Repair refuses to redirect a known non-empty queue", output)
        self.assertEqual(state["events"], ["telegram:getMe", "telegram:getWebhookInfo"])
        self.assertFalse(private_file_left)

    def test_unsigned_disabled_worker_blocks_repair_before_mutations(self):
        failed, state, output, _executed, private_file_left = simulate("repair", MOCK_UNSIGNED_DISABLED="1")
        self.assertTrue(failed)
        self.assertIn("HTTP 503 webhook_disabled", output)
        self.assertIn("worker:unsigned", state["events"])
        self.assertNotIn("telegram:setMyCommands", state["events"])
        self.assertNotIn("telegram:setWebhook", state["events"])
        self.assertFalse(any(event.startswith("worker:secret-put:") for event in state["events"]))
        self.assertFalse(private_file_left)

    def test_bad_command_readback_restores_menu_without_touching_secrets(self):
        failed, state, output, _executed, private_file_left = simulate("repair", MOCK_MENU_MISMATCH="1")
        self.assertTrue(failed)
        self.assertEqual(state["menu"], [])
        self.assertIn("previous menu read back after rollback", output)
        self.assertEqual(state["events"].count("telegram:setMyCommands"), 2)
        self.assertNotIn("telegram:setWebhook", state["events"])
        self.assertFalse(any(event.startswith("worker:secret-put:") for event in state["events"]))
        self.assertFalse(private_file_left)

    def test_wrong_webhook_readback_fails_before_probe_and_cleans_file(self):
        failed, state, output, _executed, private_file_left = simulate("repair", MOCK_BAD_READBACK="1")
        self.assertTrue(failed)
        self.assertIn("exact requested URL", output)
        self.assertIn("telegram:setWebhook", state["events"])
        self.assertEqual(state["events"].count("worker:probe"), 1)
        self.assertFalse(private_file_left)

    def test_post_switch_probe_failure_blocks_promotion_and_reports_residual_staging(self):
        failed, state, output, _executed, _ = simulate("rotation", MOCK_POST_PROBE_FAILURE="1")
        self.assertTrue(failed)
        self.assertIn("telegram:setWebhook", state["events"])
        self.assertNotIn("worker:secret-put:TELEGRAM_WEBHOOK_SECRET", state["events"])
        self.assertNotIn("worker:secret-delete:TELEGRAM_WEBHOOK_SECRET_STAGING", state["events"])
        self.assertIn("Staging cleanup: not attempted; staging may remain active", output)

    def test_unavailable_worker_probe_is_failure_not_delivery_proof(self):
        failed, state, output, _executed, private_file_left = simulate("repair", MOCK_PROBE_FAILURE="1")
        self.assertTrue(failed)
        self.assertIn("signed Worker probe failed: HTTP 503", output)
        self.assertEqual(state["events"].count("worker:probe"), 6)
        self.assertNotIn("telegram:setWebhook", state["events"])
        self.assertNotIn("worker:secret-put:TELEGRAM_WEBHOOK_SECRET", state["events"])
        self.assertFalse(private_file_left)


if __name__ == "__main__":
    unittest.main()
