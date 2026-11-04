"""Register the Telegram webhook for the D1 control Worker, in one command.

The Worker needs three secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
`TELEGRAM_ALLOWED_CHAT_IDS`), and Telegram needs the same secret in its setWebhook
call plus a public HTTPS URL. This script discovers only one private human sender,
never silently pauses an existing webhook, and preserves queued updates when
registering or re-registering. A webhook pause requires explicit operator opt-in.

The token is read from the environment or prompted for, never echoed, never
written to stdout, and never put on a command line. Output that the owner does need
(the generated webhook secret, their chat id) goes to a `0600` file that the
repository already ignores, and the printed instructions read from that file so
nothing secret appears in a terminal scrollback, a chat window, or a shell history.

    # 1. create a bot with @BotFather, copy the token
    # 2. send the bot /start (discovery requires a private human sender)
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
CHAT_ID_RE = re.compile(r"^[1-9]\d{0,19}$")


class BootstrapError(RuntimeError):
    """A step failed in a way the owner can act on."""


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

    ``expect`` exists because Bot API results are not all objects: ``getUpdates``
    returns a **list**. Coercing every non-dict result to ``{}`` made getUpdates
    look like "no pending updates" while Telegram was reporting seven of them — the
    bug that cost several go-live runs. Each caller now states the shape it wants,
    and a mismatch is an empty value of that shape rather than a silent success.
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
    result = body.get("result")
    if expect == "list":
        return result if isinstance(result, list) else []
    if expect == "bool":
        return result is True
    return result if isinstance(result, dict) else {}


def find_private_chat_ids(
    token: str, *, replace_webhook: bool = False
) -> tuple[list[int], str | None, int]:
    """Discover one private human chat without silently interrupting a webhook."""
    me = _call(token, "getMe", expect="dict")
    username = me.get("username") if isinstance(me.get("username"), str) else None
    try:
        updates = _call(
            token, "getUpdates",
            {"timeout": 0, "limit": 100, "allowed_updates": ["message"]},
            expect="list",
        )
    except BootstrapError as error:
        if "HTTP 409" not in str(error):
            raise
        if not replace_webhook:
            raise BootstrapError(
                "a webhook is already registered; refusing to pause it automatically. "
                "Pass --replace-webhook-to-discover to pause delivery while preserving "
                "pending updates, or pass --chat-id to avoid discovery.",
            ) from None
        _call(token, "deleteWebhook", {"drop_pending_updates": False}, expect="bool")
        updates = _call(
            token, "getUpdates",
            {"timeout": 0, "limit": 100, "allowed_updates": ["message"]},
            expect="list",
        )
    seen = len(updates)
    found: list[int] = []
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        sender = message.get("from") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or chat.get("type") != "private":
            continue
        if not isinstance(sender, dict) or sender.get("is_bot") is not False:
            continue
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        # In a private chat, the human sender id must equal the private chat id.
        if (
            isinstance(chat_id, int) and chat_id > 0 and
            isinstance(sender_id, int) and sender_id == chat_id and
            chat_id not in found
        ):
            found.append(chat_id)
    if len(found) > 1:
        raise BootstrapError(
            "multiple private human chats were found; refusing to allowlist multiple users. "
            "Pass --chat-id with the owner's verified numeric id.",
        )
    return found, username, seen


def webhook_state(token: str) -> dict:
    info = _call(token, "getWebhookInfo", expect="dict")
    return {
        "url": info.get("url") or "",
        "pending_updates": info.get("pending_update_count"),
        "last_error": (info.get("last_error_message") or "")[:160],
        # Telegram's getWebhookInfo does not expose whether secret_token is set.
        # has_custom_certificate refers to a custom TLS certificate, not the
        # webhook secret. Keep this explicitly unknown instead of inferring it.
        "secret_token_set": None,
    }


def register(
    token: str,
    url: str,
    secret: str,
    chat_ids: list[int],
    *,
    require_allowlist: bool = True,
) -> dict:
    if not url.startswith("https://"):
        raise BootstrapError("the webhook URL must be https")
    if not SECRET_RE.fullmatch(secret):
        raise BootstrapError("the webhook secret must be 16-256 characters of [A-Za-z0-9_-]")
    if require_allowlist and (
        not chat_ids or not all(CHAT_ID_RE.fullmatch(str(chat_id)) for chat_id in chat_ids)
    ):
        raise BootstrapError("at least one numeric chat id is required")
    _call(token, "setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message"],
        # Preserve queued owner messages across webhook registration/re-registration.
        "drop_pending_updates": False,
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
    parser.add_argument(
        "--replace-webhook-to-discover",
        action="store_true",
        help="Explicitly pause an existing webhook (without dropping updates) to use getUpdates",
    )
    parser.add_argument(
        "--rearm-only",
        action="store_true",
        help=(
            "Rotate the webhook secret and re-register without touching the chat "
            "allowlist. Use when the Worker already knows its owner but Telegram "
            "has nothing queued to rediscover them from."
        ),
    )
    args = parser.parse_args(argv)

    try:
        token = read_token(args.token)
        if args.check:
            state = webhook_state(token)
            print(json.dumps({"webhook": state}, indent=2))
            return 0 if state["url"] else 1

        if args.rearm_only:
            # The allowlist is already correct on the Worker; only the secret and
            # the registration are being renewed. Requiring a discoverable chat id
            # here would block exactly the case this exists for.
            secret = args.secret or generate_webhook_secret()
            if not args.url:
                raise BootstrapError("--rearm-only needs --url https://<worker>.workers.dev")
            env_file = Path(args.env_file)
            write_env_file(env_file, {
                "TELEGRAM_BOT_TOKEN": token,
                "TELEGRAM_WEBHOOK_SECRET": secret,
                "TELEGRAM_ALLOWED_CHAT_IDS": "",
            })
            summary = {
                "mode": "rearm_only",
                "url": args.url,
                "secret_generated": args.secret is None,
                "env_file": str(env_file),
                "allowlist": "left untouched on the Worker",
            }
            summary["webhook"] = register(token, args.url, secret, [], require_allowlist=False)
            summary["registered"] = True
            print(json.dumps(summary, indent=2))
            return 0

        if args.chat_id:
            if args.replace_webhook_to_discover:
                raise BootstrapError("--chat-id and --replace-webhook-to-discover cannot be used together")
            chat_ids = _parse_chat_ids(args.chat_id)
            username, updates_seen = None, 0
        else:
            chat_ids, username, updates_seen = find_private_chat_ids(
                token, replace_webhook=args.replace_webhook_to_discover
            )
        if not chat_ids:
            # "No private chat found" has three causes that look identical from the
            # outside, so the failure reports the two facts that separate them:
            # whether Telegram handed us any update at all, and whether something
            # else is already consuming them (a webhook, or another poller).
            where = f"https://t.me/{username}" if username else "your bot"
            try:
                state = webhook_state(token)
                webhook = state.get("url") or ""
                pending = state.get("pending_updates")
            except BootstrapError:
                webhook, pending = "", None
            if args.replace_webhook_to_discover:
                guidance = (
                    "The webhook is paused and pending updates were preserved. Send /start "
                    "to the correct bot, then rerun with --replace-webhook-to-discover."
                )
            else:
                guidance = (
                    f"Open {where}, press Start and send a message. If a webhook is already "
                    "set, pass --replace-webhook-to-discover only when you intentionally "
                    "accept a brief delivery pause, or pass --chat-id with the verified id."
                )
            raise BootstrapError(
                f"no private chat found (human sender required) for @{username or 'the supplied bot'} "
                f"(updates_seen={updates_seen}, webhook_set={'yes' if webhook else 'no'}, "
                f"pending_update_count={pending}). {guidance}",
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
