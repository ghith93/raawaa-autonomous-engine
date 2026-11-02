from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .cli import scan
from .dexscreener import MarketDataError
from .ledger import OpportunityLedger

TELEGRAM_API = "https://api.telegram.org"
MAX_TELEGRAM_RESPONSE_BYTES = 500_000
MAX_MESSAGE_LENGTH = 4000


class TelegramApiError(RuntimeError):
    """A sanitized Telegram Bot API failure; never contains the bot token or URL."""


class TelegramBotAPI:
    def __init__(self, token: str, timeout_seconds: float = 35.0):
        if not isinstance(token, str) or not token.strip() or any(ch.isspace() for ch in token):
            raise ValueError("A valid Telegram bot token is required via environment")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._token = token
        self.timeout_seconds = timeout_seconds

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        if method not in {"getUpdates", "sendMessage", "getMe"}:
            raise ValueError("Telegram method is not allowlisted")
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{TELEGRAM_API}/bot{self._token}/{method}",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_TELEGRAM_RESPONSE_BYTES + 1)
                if len(raw) > MAX_TELEGRAM_RESPONSE_BYTES:
                    raise TelegramApiError("Telegram response exceeded 500 KB safety limit")
                result = json.loads(raw)
        except HTTPError as exc:
            raise TelegramApiError(f"Telegram HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramApiError(f"Telegram request failed: {type(exc).__name__}") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error_code") if isinstance(result, dict) else None
            raise TelegramApiError(f"Telegram API rejected the request (code={code})")
        return result.get("result")

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        result = self.call("getUpdates", {
            "offset": offset, "timeout": timeout, "limit": 50,
            "allowed_updates": ["message"],
        })
        if not isinstance(result, list):
            raise TelegramApiError("Telegram getUpdates returned an unexpected result")
        return [item for item in result if isinstance(item, dict)]

    def send_message(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_MESSAGE_LENGTH]})


@dataclass(slots=True)
class TelegramController:
    api: TelegramBotAPI
    allowed_chat_ids: frozenset[int]
    ledger_path: str = "data/opportunities.sqlite3"
    min_request_interval_seconds: float = 3.0
    _last_request_by_chat: dict[int, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.allowed_chat_ids:
            raise ValueError("At least one allowed private chat ID is required")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in self.allowed_chat_ids):
            raise ValueError("Allowed chat IDs must be integers")
        if self.min_request_interval_seconds < 0:
            raise ValueError("Rate-limit interval must be non-negative")

    def response_for_update(self, update: dict[str, Any]) -> tuple[int, str] | None:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return None
        chat_id = chat.get("id")
        if (
            chat.get("type") != "private"
            or not isinstance(chat_id, int) or isinstance(chat_id, bool)
            or chat_id not in self.allowed_chat_ids
            or sender.get("id") != chat_id
            or sender.get("is_bot") is True
        ):
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return None

        now = time.monotonic()
        previous = self._last_request_by_chat.get(chat_id, 0.0)
        if now - previous < self.min_request_interval_seconds:
            return chat_id, "Rate limit: wait a few seconds before the next command."
        self._last_request_by_chat[chat_id] = now

        command, _, argument = text.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        if command in {"/start", "/help"}:
            return chat_id, (
                "Telegram Arbitrage Engine — read-only research mode.\n"
                "/status — safety and runtime status\n"
                "/scan TOKEN_OR_PAIR — indicative DEX discovery\n"
                "/report — repeated observation summary\n"
                "No wallet, orders, swaps, or live trading are available."
            )
        if command == "/status":
            return chat_id, (
                "Mode: read-only discovery + simulation\n"
                "Live execution: disabled\n"
                "Wallet/signing: not implemented\n"
                "AI trade authority: none"
            )
        if command == "/report":
            rows = OpportunityLedger(self.ledger_path).summarize(100)
            if not rows:
                return chat_id, "No recorded observations yet."
            lines = ["Repeated indicative observations (not PnL):"]
            for row in rows[:5]:
                lines.append(
                    f"{row['pair']} | {row['buy_venue']} → {row['sell_venue']} | "
                    f"{row['observation_count']} observations | mean {row['mean_gross_spread_bps']} bps"
                )
            return chat_id, "\n".join(lines)
        if command == "/scan":
            query = argument.strip()
            if not query:
                return chat_id, "Usage: /scan TOKEN_OR_PAIR"
            if len(query) > 120:
                return chat_id, "Search query is too long (maximum 120 characters)."
            try:
                result = scan(query, Decimal("10000"), Decimal("1000"), self.ledger_path)
            except (MarketDataError, ValueError):
                return chat_id, "Market scan failed safely. Retry later; no trade was placed."
            opportunities = result["opportunities"][:5]
            if not opportunities:
                return chat_id, (
                    f"No cross-DEX indicative spread passed the liquidity/volume filters for {query}.\n"
                    "This is not proof that no arbitrage exists."
                )
            lines = [f"Indicative scan: {query}", "Not executable quotes; no profit guarantee."]
            for item in opportunities:
                lines.append(
                    f"{item['chain']} {item['pair']}: {item['buy_venue']} → {item['sell_venue']} "
                    f"{item['gross_spread_bps']} bps gross"
                )
            return chat_id, "\n".join(lines)
        return chat_id, "Unknown command. Send /help."

    def poll_once(self, offset: int = 0) -> int:
        next_offset = offset
        for update in self.api.get_updates(offset=offset, timeout=25):
            update_id = update.get("update_id")
            response = self.response_for_update(update)
            if response is not None:
                chat_id, text = response
                self.api.send_message(chat_id, text)
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                next_offset = max(next_offset, update_id + 1)
        return next_offset

    def run_forever(self) -> None:
        offset = 0
        while True:
            try:
                offset = self.poll_once(offset)
            except TelegramApiError:
                time.sleep(3)


def controller_from_environment() -> TelegramController:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    raw_ids = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    try:
        allowed = frozenset(int(value.strip()) for value in raw_ids.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("TELEGRAM_ALLOWED_CHAT_IDS must be comma-separated integer IDs") from exc
    return TelegramController(TelegramBotAPI(token), allowed)


def main() -> None:
    controller_from_environment().run_forever()


if __name__ == "__main__":
    main()
