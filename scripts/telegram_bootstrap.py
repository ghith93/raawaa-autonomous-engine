"""Register the Telegram webhook for the D1 control Worker, in one command.

The Worker needs three secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_ALLOWED_CHAT_IDS`), and Telegram needs the same secret in its setWebhook
call plus a public HTTPS URL. Five things to get right by hand is four too many, so
this script does the fiddly parts and leaves the owner two actions that only they
can take: create the bot in BotFather, and send it one message.

The token is read from the environment or prompted for, never echoed, never
written to stdout, and never put on a command line. Output that the owner does need
(the generated webhook secret, their chat id) goes to a `0600` file that the
repository already ignores, and the printed instructions read from that file so
nothing secret appears in a terminal scrollback, a chat window, or a shell history.

    # 1. create a bot with @BotFather, copy the token
    # 2. send the bot any message (e.g. /start)
    read -rs TELEGRAM_BOT_TOKEN && export TELEGRAM_BOT_TOKEN
    python3 scripts/telegram_bootstrap.py --url https://<worker>.workers.dev

--check inspects the current state and changes nothing. Without --register the
script is a dry run: it discovers the chat id, generates a secret, writes the env
file, and prints the exact commands to run.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.telegram.org"
DEFAULT_ENV_FILE = ".env.telegram-bootstrap"
USER_AGENT = "RaawaaAutonomousEngine/1.0 (+https://github.com/ghith93/raawaa-autonomous-engine)"
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,64}$")
CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")


class BootstrapError(RuntimeError):
    """A step failed in a way the owner can act on."""


def generate_webhook_secret() -> str:
    """32 bytes of urlsafe randomness: inside Telegram's allowed alphabet, comfortably long."""
    return secrets.token_urlsafe(32)


def _call(token: str, method: str, payload: dict | None = None, *, timeout: float = 15.0) -> dict:
    """Call one Bot API method and return `result`.

    Token-bearing URLs are never included in error text: an exception that echoes
    the URL would leak the token into logs and backtraces.
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
    except URLError as error:
        raise BootstrapError(f"{method} failed: {error.reason} (token not shown)") from None
    except json.JSONDecodeError:
        raise BootstrapError(f"{method} returned a non-JSON response") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        description = body.get("description") if isinstance(body, dict) else None
        safe = description if isinstance(description, str) and len(description) <= 120 else "request rejected"
        raise BootstrapError(f"{method} failed: {safe}")
    return body.get("result") if isinstance(body.get("result"), dict) else {}


def find_private_chat_ids(token: str) -> tuple[list[int], str | None]:
    """Chat ids from pending updates, plus the bot username.

    Telegram only returns updates while no webhook is registered, which is exactly
    the state this script runs in before registration. A webhook already set means
    the owner must send a fresh message after deleting it, or supply the id.
    """
    me = _call(token, "getMe")
    username = me.get("username") if isinstance(me.get("username"), str) else None
    updates = _call(token, "getUpdates", {"timeout": 0, "limit": 100, "allowed_updates": ["message"]})
    found: list[int] = []
    for update in updates.get("updates") or []:
        message = update.get("message") if isinstance(update, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private":
            continue
        chat_id = chat.get("id")
        # A private chat id equals the user id; anything else here is not a person.
        if isinstance(chat_id, int) and chat_id > 0 and chat_id not in found:
            found.append(chat_id)
    return found, username


def webhook_state(token: str) -> dict:
    info = _call(token, "getWebhookInfo")
    return {
        "url": info.get("url") or "",
        "pending_updates": info.get("pending_update_count"),
        "last_error": (info.get("last_error_message") or "")[:160],
        "secret_token_set": bool(info.get("has_custom_certificate")) is False and bool(info.get("url")),
    }


def register(token: str, url: str, secret: str, chat_ids: list[int]) -> dict:
    if not url.startswith("https://"):
        raise BootstrapError("the webhook URL must be https")
    if not SECRET_RE.fullmatch(secret):
        raise BootstrapError("the webhook secret must be 16-256 characters of [A-Za-z0-9_-]")
    if not chat_ids or not all(CHAT_ID_RE.fullmatch(str(chat_id)) for chat_id in chat_ids):
        raise BootstrapError("at least one numeric chat id is required")
    _call(token, "setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message"],
        # Drop whatever queued before registration: an old /start is not a command.
        "drop_pending_updates": True,
        "max_connections": 10,
    })
    return webhook_state(token)


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write secrets to a 0600 file the repository already ignores. Never to argv."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _parse_chat_ids(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in values:
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not CHAT_ID_RE.fullmatch(piece):
                raise BootstrapError(f"not a chat id: {piece[:20]}")
            number = int(piece)
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


def instructions(env_file: Path, worker_url: str) -> str:
    return f"""\
Secrets are in {env_file} (mode 0600, already git-ignored). Nothing secret was printed.

Next, from the repository root:

  # 1. install the three Worker secrets (values read from the file, never from argv)
  while IFS='=' read -r key value; do
    printf '%s' "$value" | npx wrangler secret put "$key"
  done < {env_file}

  # 2. deploy the Worker if this surface is not deployed yet
  npx wrangler deploy

  # 3. confirm Telegram is pointed at it
  python3 scripts/telegram_bootstrap.py --check --url {worker_url}

Then send /status to the bot. The Worker answers it; nothing on your phone has to run.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register the Telegram webhook for the control Worker")
    parser.add_argument("--url", help="Public https URL of the Worker (required to register)")
    parser.add_argument("--token", help="Bot token (prefer the TELEGRAM_BOT_TOKEN environment variable)")
    parser.add_argument("--chat-id", action="append", default=None,
                        help="Allowed private chat id (repeatable, or comma-separated); "
                             "discovered from pending updates if omitted")
    parser.add_argument("--secret", help="Existing webhook secret (generated when omitted)")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Where to write the three values")
    parser.add_argument("--register", action="store_true", help="Actually call setWebhook (default: dry run)")
    parser.add_argument("--check", action="store_true", help="Report current webhook state and exit")
    args = parser.parse_args(argv)

    try:
        token = read_token(args.token)
        if args.check:
            state = webhook_state(token)
            print(json.dumps({"webhook": state}, indent=2))
            return 0 if state["url"] else 1

        discovered, username = find_private_chat_ids(token)
        chat_ids = _parse_chat_ids(args.chat_id) if args.chat_id else discovered
        if not chat_ids:
            raise BootstrapError(
                "no private chat found. Send your bot any message, then run this again. "
                "If a webhook is already registered, changes are not queued — use --chat-id.",
            )
        secret = args.secret or generate_webhook_secret()
        env_file = Path(args.env_file)
        write_env_file(env_file, {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_WEBHOOK_SECRET": secret,
            "TELEGRAM_ALLOWED_CHAT_IDS": ",".join(str(chat_id) for chat_id in chat_ids),
        })

        summary = {
            "bot": f"@{username}" if username else "unknown",
            "allowed_chat_ids": chat_ids,
            "secret_generated": args.secret is None,
            "env_file": str(env_file),
            "registered": False,
        }
        if args.register:
            if not args.url:
                raise BootstrapError("--register needs --url https://<worker>.workers.dev")
            summary["webhook"] = register(token, args.url, secret, chat_ids)
            summary["registered"] = True
        print(json.dumps(summary, indent=2))
        if not args.register:
            print(instructions(env_file, args.url or "https://<worker>.workers.dev"))
        return 0
    except BootstrapError as error:
        print(f"telegram_bootstrap: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
