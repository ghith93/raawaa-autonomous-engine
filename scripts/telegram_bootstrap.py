"""Safely inspect and prepare the Telegram webhook for the D1 control Worker.

A private update does not establish that its sender is the owner. Never enroll
chat ids discovered from pending updates automatically. Re-arm the existing
allowlist without changing it, or provide a separately verified chat id.

The token comes from the environment or a hidden prompt. Preparation writes a
mode-0600 file; install the Worker secrets first, then use --register-prepared.
Registration preserves pending updates and checks the exact webhook URL. The
separate command-menu operation never sends a message to a Telegram user.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ROOT = "https://api.telegram.org"
DEFAULT_ENV_FILE = ".env.telegram-bootstrap"
WEBHOOK_PATH = "/v1/telegram/webhook"
USER_AGENT = "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,64}$")
CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
BOT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
BOT_COMMANDS = (
    {"command": "start", "description": "Show available read-only commands"},
    {"command": "help", "description": "Show available commands"},
    {"command": "status", "description": "Show task queue status"},
    {"command": "report", "description": "Show paper research counts"},
)
_ENV_KEYS = frozenset({"TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_ALLOWED_CHAT_IDS"})


class BootstrapError(RuntimeError):
    """A step failed in a way the owner can act on."""


class _RejectRedirects(HTTPRedirectHandler):
    """Do not forward token-bearing URLs or webhook-secret headers to redirects."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def urlopen(request: Request, *, timeout: float):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def generate_webhook_secret() -> str:
    """32 bytes of urlsafe randomness: inside Telegram's allowed alphabet, comfortably long."""
    return secrets.token_urlsafe(32)


def _call(
    token: str,
    method: str,
    payload: dict | None = None,
    *,
    timeout: float = 15.0,
    expect: str = "dict",
) -> object:
    """Call one Bot API method and return `result`.

    Token-bearing URLs are never included in error text: an exception that echoes
    the URL would leak the token into logs and backtraces.

    Bot API results have different shapes. A mismatched result is an error, not
    an empty list/object or a successful registration.
    """
    url = f"{API_ROOT}/bot{token}/{method}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="POST" if data else "GET")
    request.add_header("User-Agent", USER_AGENT)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise BootstrapError(f"{method} failed: HTTP {error.code} (token not shown)") from None
    except (URLError, TimeoutError, OSError):
        # An arbitrary exception can contain the token-bearing request URL.
        raise BootstrapError(f"{method} failed: network error (token not shown)") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BootstrapError(f"{method} returned a non-JSON response") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        # Provider text is untrusted and may echo submitted secrets. Report only
        # a bounded numeric API error code, not free text or the request URL.
        code = body.get("error_code") if isinstance(body, dict) else None
        detail = f"Telegram API error {code}" if type(code) is int and 100 <= code <= 999 else "request rejected"
        raise BootstrapError(f"{method} failed: {detail}")
    result = body.get("result")
    valid = (expect == "dict" and isinstance(result, dict) or
             expect == "list" and isinstance(result, list) or
             expect == "bool" and isinstance(result, bool))
    if not valid:
        raise BootstrapError(f"{method} returned an unexpected result type (expected {expect})")
    return result


def find_private_chat_ids(token: str) -> tuple[list[int], str | None, int]:
    """Return *candidates* from pending updates, never proof of ownership.

    This diagnostic does not delete a registered webhook or confirm any update.
    Callers must independently authenticate the owner before authorizing a chat.
    """
    me = _call(token, "getMe", expect="dict")
    username = me.get("username") if isinstance(me.get("username"), str) else None
    try:
        updates = _call(
            token, "getUpdates",
            {"timeout": 0, "limit": 100},
            expect="list",
        )
    except BootstrapError as error:
        if "HTTP 409" in str(error):
            raise BootstrapError(
                "getUpdates is unavailable while a webhook is registered; "
                "the webhook was not changed"
            ) from None
        raise
    found: list[int] = []
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = message.get("from") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(sender, dict):
            continue
        chat_id = chat.get("id")
        if (isinstance(chat_id, int) and not isinstance(chat_id, bool) and chat_id > 0 and
                sender.get("id") == chat_id and sender.get("is_bot") is False and chat_id not in found):
            found.append(chat_id)
    return found, username, len(updates)


def webhook_url(url: str) -> str:
    """Accept only an HTTPS origin or the exact Worker webhook path."""
    if not isinstance(url, str) or not url or re.search(r"[\x00-\x20\x7f\\]", url):
        raise BootstrapError("webhook URL must be an HTTPS origin or the exact webhook path")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port  # reject malformed ports
    except ValueError:
        raise BootstrapError("webhook URL has an invalid host or port") from None
    if (parsed.scheme != "https" or not hostname or parsed.username is not None or
            parsed.password is not None or parsed.query or parsed.fragment or
            parsed.path not in ("", "/", WEBHOOK_PATH)):
        raise BootstrapError("webhook URL must be an HTTPS origin or the exact webhook path")
    return urlunsplit(("https", parsed.netloc, WEBHOOK_PATH, "", ""))


def verify_bot(token: str, expected_username: str) -> str:
    if not BOT_NAME_RE.fullmatch(expected_username or ""):
        raise BootstrapError("an expected bot username is required")
    me = _call(token, "getMe", expect="dict")
    username = me.get("username")
    if me.get("is_bot") is not True or not isinstance(username, str):
        raise BootstrapError("getMe did not return a valid bot identity")
    if username.casefold() != expected_username.casefold():
        raise BootstrapError("configured token belongs to a different bot")
    return username


def webhook_state(token: str) -> dict:
    info = _call(token, "getWebhookInfo", expect="dict")
    url = info.get("url")
    pending = info.get("pending_update_count")
    error = info.get("last_error_message")
    if not isinstance(url, str) or type(pending) is not int or pending < 0:
        raise BootstrapError("getWebhookInfo returned invalid URL or pending update count")
    return {
        "url": url,
        "pending_updates": pending,
        "last_error": error[:160] if isinstance(error, str) else "",
        # Telegram does not expose secret_token presence through getWebhookInfo.
        "secret_token_set": None,
    }


def diagnose(token: str, url: str, expected_username: str) -> dict:
    """Read-only, redacted state; does not disclose the actual webhook URL."""
    destination = webhook_url(url)
    username = verify_bot(token, expected_username)
    state = webhook_state(token)
    # A provider-controlled URL/error might contain sensitive data. Publish only a
    # recognized HTTP code, not the raw provider message or actual destination.
    match = re.search(r"\b([45]\d\d)\b", state["last_error"])
    last_error = f"HTTP {match.group(1)}" if match else ("present" if state["last_error"] else "none")
    return {
        "bot": f"@{username}",
        "webhook_matches": state["url"] == destination,
        "pending_updates": state["pending_updates"],
        "last_delivery_error": last_error,
        "webhook_secret": "unknown",
    }


def configure_commands(token: str) -> None:
    """Snapshot the default menu, then set/read back only read-only commands.

    If the change or readback fails, attempt to restore the in-memory snapshot
    and verify it. An ambiguous transport failure can still require a fresh read.
    """
    previous = _call(token, "getMyCommands", expect="list")
    if len(previous) > 100 or any(
        not isinstance(item, dict) or set(item) != {"command", "description"} or
        not isinstance(item["command"], str) or not re.fullmatch(r"[a-z0-9_]{1,32}", item["command"]) or
        not isinstance(item["description"], str) or not 1 <= len(item["description"]) <= 256
        for item in previous
    ):
        raise BootstrapError("existing Telegram command menu has an unexpected shape; no changes made")
    if previous == list(BOT_COMMANDS):
        return
    try:
        if _call(token, "setMyCommands", {"commands": list(BOT_COMMANDS)}, expect="bool") is not True:
            raise BootstrapError("setMyCommands did not report success")
        actual = _call(token, "getMyCommands", expect="list")
        if actual != list(BOT_COMMANDS):
            raise BootstrapError("Telegram command-menu readback did not match the requested commands")
    except BootstrapError as error:
        try:
            restored = _call(token, "setMyCommands", {"commands": previous}, expect="bool") is True
            verified = restored and _call(token, "getMyCommands", expect="list") == previous
        except BootstrapError:
            verified = False
        outcome = "previous menu read back after rollback" if verified else "previous menu rollback unverified; recheck Telegram"
        raise BootstrapError(f"{error}; {outcome}") from None


def register(
    token: str,
    url: str,
    secret: str,
    chat_ids: list[int],
    *,
    require_allowlist: bool = True,
) -> dict:
    destination = webhook_url(url)
    if not isinstance(secret, str) or not SECRET_RE.fullmatch(secret):
        raise BootstrapError("the webhook secret must be 16-256 characters of [A-Za-z0-9_-]")
    if (require_allowlist and not chat_ids) or not all(
        type(chat_id) is int and 0 < chat_id <= 2**53 - 1 for chat_id in chat_ids
    ):
        raise BootstrapError("a verified positive safe-integer private chat id is required")
    # The Worker acknowledges unauthorized updates. Refuse to deliberately
    # redirect a queue we already know is non-empty; this does not eliminate a
    # concurrent-arrival race between this read and setWebhook.
    if webhook_state(token)["pending_updates"]:
        raise BootstrapError("pending updates detected; setWebhook was not called")
    accepted = _call(token, "setWebhook", {
        "url": destination,
        "secret_token": secret,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
        "max_connections": 10,
    }, expect="bool")
    if accepted is not True:
        raise BootstrapError("setWebhook did not report success")
    state = webhook_state(token)
    if state["url"] != destination:
        raise BootstrapError("Telegram webhook readback did not match the exact requested URL")
    return state


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Atomically replace a private regular file without following links.

    Never open an existing destination with O_TRUNC: even a hard link or a FIFO
    must not let preparation truncate an unrelated file or hang the runner.
    """
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or
                                     existing.st_uid != os.geteuid() or existing.st_nlink != 1):
            raise BootstrapError("the prepared env destination must be an owned, singly-linked regular file")
        descriptor, temporary = tempfile.mkstemp(prefix=".telegram-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Replacing the directory entry never follows a destination symlink, even
        # if one appears after lstat. The temporary file is already mode 0600.
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise BootstrapError("could not write a secure prepared env file") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_prepared_env(path: Path) -> dict[str, str]:
    """Read only an owner-private regular file with exactly the expected keys."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or
                    info.st_uid != os.geteuid() or info.st_nlink != 1):
                raise BootstrapError("the prepared env file must be a private, owned, singly-linked regular file")
            content = handle.read(4097)
    except (OSError, UnicodeDecodeError):
        raise BootstrapError("could not read a secure prepared env file") from None
    if len(content) > 4096:
        raise BootstrapError("the prepared env file is too large")
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in _ENV_KEYS or key in values:
            raise BootstrapError("the prepared env file has unexpected or duplicate keys")
        values[key] = value
    if set(values) != _ENV_KEYS:
        raise BootstrapError("the prepared env file is missing required keys")
    return values


def _parse_chat_ids(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in values:
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not CHAT_ID_RE.fullmatch(piece):
                raise BootstrapError("chat ids must be positive safe integers")
            number = int(piece)
            if not 0 < number <= 2**53 - 1:
                raise BootstrapError("chat ids must be positive safe integers")
            if number not in parsed:
                parsed.append(number)
    return parsed


def read_token(explicit: str | None) -> str:
    """Take the token from the argument, the environment, or a hidden prompt.

    An explicitly supplied value is never replaced by a prompt: if it is wrong the
    caller should hear that, not be asked to type it again in a non-interactive
    process.
    """
    if explicit is not None:
        token = explicit
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
        if not token:
            token = getpass.getpass("Telegram bot token (input hidden): ")
    token = token.strip()
    if not TOKEN_RE.fullmatch(token):
        raise BootstrapError("that does not look like a bot token (expected <digits>:<35+ chars>)")
    return token


def probe_unsigned_readiness(url: str) -> None:
    """Check the Worker's disabled/auth gate with an ineligible empty update.

    A 401 unauthorized contract proves only that this edge rejected an unsigned
    request while its configuration was structurally acceptable. It does not
    authenticate an owner, validate the bot token, or prove message delivery.
    """
    request = Request(webhook_url(url), data=b"{}", method="POST",
                      headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            raw = response.read(4097)
    except HTTPError as error:
        status = error.code
        try:
            raw = error.read(4097)
        except (OSError, ValueError):
            raw = b""
    except (URLError, TimeoutError, OSError):
        raise BootstrapError("unsigned Worker readiness probe failed: network error") from None
    try:
        payload = json.loads(raw.decode("utf-8")) if len(raw) <= 4096 else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    if status == 401 and payload == {"error": "unauthorized"}:
        return
    if status == 503 and payload == {"error": "webhook_disabled"}:
        raise BootstrapError("unsigned Worker readiness probe failed: HTTP 503 webhook_disabled")
    raise BootstrapError(f"unsigned Worker readiness probe returned unexpected response (HTTP {status})")


def probe_signed_ignored(url: str, secret: str, *, attempts: int = 6) -> int:
    """Probe header acceptance without an eligible sender, DB write, or reply.

    This proves neither allowlist ownership nor delivery to a real human. Each
    update is unique, but deliberately lacks `from`, so it cannot be processed.
    """
    import time

    destination = webhook_url(url)
    if not SECRET_RE.fullmatch(secret):
        raise BootstrapError("invalid prepared webhook secret")
    body = json.dumps({
        "update_id": secrets.randbelow(2**52),
        "message": {"message_id": 1, "date": 0,
                    "chat": {"id": 0, "type": "private"}, "text": "/help"},
    }).encode("utf-8")
    status = 0
    for attempt in range(1, attempts + 1):
        request = Request(destination, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Telegram-Bot-Api-Secret-Token": secret,
        })
        try:
            with urlopen(request, timeout=20) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))
            if (status == 200 and isinstance(payload, dict) and set(payload) == {"ok", "ignored"} and
                    payload["ok"] is True and payload["ignored"] is True):
                return status
            raise BootstrapError(f"signed Worker probe returned unexpected response (HTTP {status})")
        except HTTPError as error:
            status = error.code
        except (URLError, TimeoutError):
            status = 0
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BootstrapError("signed Worker probe returned invalid JSON") from None
        if status not in (0, 401, 503) or attempt == attempts:
            label = f"HTTP {status}" if status else "network error"
            raise BootstrapError(f"signed Worker probe failed: {label}") from None
        time.sleep(attempt * 5)
    raise BootstrapError("signed Worker probe failed")


def instructions(env_file: Path, worker_url: str) -> str:
    path = shlex.quote(str(env_file))
    url = shlex.quote(worker_url)
    return f"""\
Prepared secrets are in {path} (mode 0600); do not copy them into chat or logs.
Install non-empty values as Worker secrets, then register from this file:

  while IFS='=' read -r key value; do
    [ -z "$value" ] || printf '%s' "$value" | npx wrangler secret put "$key"
  done < {path}
  python3 scripts/telegram_bootstrap.py --register-prepared --env-file {path} \\
    --url {url} --expected-bot <bot_username>

Registration does not explicitly drop pending updates. After delivery is enabled,
Telegram may still deliver and the Worker may ignore unauthorized updates.
This does not prove that a real user's chat is authorized.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and safely prepare the Telegram webhook")
    parser.add_argument("--url", help="HTTPS Worker origin or exact webhook URL")
    parser.add_argument("--token", help="Disabled: use TELEGRAM_BOT_TOKEN or a hidden prompt")
    parser.add_argument("--expected-bot", help="Expected bot username, without @")
    parser.add_argument("--chat-id", action="append", default=None,
                        help="Independently verified private chat id (repeatable or comma-separated)")
    parser.add_argument("--secret", help="Disabled: use a protected prepared file")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Protected prepared file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--register", action="store_true", help="Deprecated; use --register-prepared")
    mode.add_argument("--check", action="store_true", help="Read webhook state; verify --url if supplied")
    mode.add_argument("--diagnose", action="store_true", help="Read redacted bot and webhook readiness")
    mode.add_argument("--rearm-only", action="store_true", help="Prepare a secret without registering or altering the allowlist")
    mode.add_argument("--register-prepared", action="store_true", help="Register using the already-installed prepared secrets")
    mode.add_argument("--configure-commands", action="store_true", help="Set and read back the read-only command menu")
    mode.add_argument("--probe-unsigned", action="store_true", help="Check the unsigned Worker readiness gate")
    mode.add_argument("--probe-prepared", action="store_true", help="Check the signed, ignored-update contract")
    args = parser.parse_args(argv)

    try:
        if args.token is not None or args.secret is not None:
            raise BootstrapError("--token and --secret are disabled; use an environment variable or protected prepared file")
        if args.probe_unsigned:
            if not args.url:
                raise BootstrapError("--probe-unsigned needs --url")
            probe_unsigned_readiness(args.url)
            print(json.dumps({"unsigned_rejected": True, "configuration_valid_at_probe": True,
                              "owner_delivery_verified": False}))
            return 0
        token = read_token(None)
        if args.register:
            raise BootstrapError("--register is unsafe before Worker secret installation; use --register-prepared")
        if args.check:
            state = webhook_state(token)
            destination_matches = state["url"] == webhook_url(args.url) if args.url else bool(state["url"])
            print(json.dumps({
                "webhook_set": bool(state["url"]),
                "webhook_matches": destination_matches,
                "pending_updates": state["pending_updates"],
                "secret_token_set": None,
            }, indent=2))
            return 0 if destination_matches else 1
        if args.diagnose:
            if not args.url:
                raise BootstrapError("--diagnose needs --url")
            print(json.dumps(diagnose(token, args.url, args.expected_bot or ""), indent=2))
            return 0
        if args.configure_commands:
            verify_bot(token, args.expected_bot or "")
            configure_commands(token)
            print(json.dumps({"commands_verified": [item["command"] for item in BOT_COMMANDS]}))
            return 0

        env_file = Path(args.env_file)
        if args.rearm_only:
            if not args.url:
                raise BootstrapError("--rearm-only needs --url")
            destination = webhook_url(args.url)
            if args.expected_bot:
                verify_bot(token, args.expected_bot)
            secret = generate_webhook_secret()
            if not SECRET_RE.fullmatch(secret):
                raise BootstrapError("invalid webhook secret")
            write_env_file(env_file, {
                "TELEGRAM_BOT_TOKEN": token,
                "TELEGRAM_WEBHOOK_SECRET": secret,
                "TELEGRAM_ALLOWED_CHAT_IDS": "",
            })
            print(json.dumps({"mode": "prepare_rearm", "url": destination, "registered": False,
                              "allowlist": "unchanged", "env_file": str(env_file)}, indent=2))
            return 0

        if args.register_prepared or args.probe_prepared:
            if not args.url:
                raise BootstrapError("the prepared operation needs --url")
            values = read_prepared_env(env_file)
            if not secrets.compare_digest(values["TELEGRAM_BOT_TOKEN"], token):
                raise BootstrapError("prepared file and configured bot token do not match")
            if args.register_prepared:
                verify_bot(token, args.expected_bot or "")
                chats = _parse_chat_ids([values["TELEGRAM_ALLOWED_CHAT_IDS"]]) if values["TELEGRAM_ALLOWED_CHAT_IDS"] else []
                state = register(token, args.url, values["TELEGRAM_WEBHOOK_SECRET"], chats,
                                 require_allowlist=bool(chats))
                print(json.dumps({"registered": True, "webhook_matches": True,
                                  "pending_updates": state["pending_updates"]}, indent=2))
            else:
                status = probe_signed_ignored(args.url, values["TELEGRAM_WEBHOOK_SECRET"])
                print(json.dumps({"http_status": status, "ignored": True,
                                  "owner_delivery_verified": False}))
            return 0

        # No private update authenticates ownership. Require a separately
        # verified identity instead of automatically allowlisting every sender.
        if not args.chat_id:
            raise BootstrapError("no verified chat id supplied; private updates alone do not authenticate the owner")
        chat_ids = _parse_chat_ids(args.chat_id)
        secret = generate_webhook_secret()
        if not SECRET_RE.fullmatch(secret):
            raise BootstrapError("invalid webhook secret")
        write_env_file(env_file, {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_WEBHOOK_SECRET": secret,
            "TELEGRAM_ALLOWED_CHAT_IDS": ",".join(str(chat_id) for chat_id in chat_ids),
        })
        print(json.dumps({"registered": False, "verified_chat_count": len(chat_ids),
                          "env_file": str(env_file)}, indent=2))
        print(instructions(env_file, webhook_url(args.url) if args.url else "https://<worker>.workers.dev"))
        return 0
    except BootstrapError as error:
        print(f"telegram_bootstrap: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
