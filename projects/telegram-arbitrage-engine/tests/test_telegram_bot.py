import pytest

from arb_engine.telegram_bot import TelegramBotAPI, TelegramApiError, TelegramController


class FakeAPI:
    def __init__(self, updates):
        self.updates = updates
        self.sent = []

    def get_updates(self, offset, timeout):
        return self.updates

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def message_update(update_id=1, chat_id=123, text="/status", chat_type="private", sender_id=None):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id if sender_id is None else sender_id, "is_bot": False},
            "text": text,
        },
    }


def test_private_allowlisted_chat_can_read_status():
    controller = TelegramController(FakeAPI([]), frozenset({123}), min_request_interval_seconds=0)
    response = controller.response_for_update(message_update())
    assert response is not None
    assert response[0] == 123
    assert "Live execution: disabled" in response[1]


def test_unlisted_or_group_chats_are_ignored():
    controller = TelegramController(FakeAPI([]), frozenset({123}), min_request_interval_seconds=0)
    assert controller.response_for_update(message_update(chat_id=456)) is None
    assert controller.response_for_update(message_update(chat_type="group")) is None
    assert controller.response_for_update(message_update(sender_id=456)) is None


def test_poll_advances_offset_after_authorized_reply_and_ignores_others():
    api = FakeAPI([
        message_update(update_id=10, chat_id=999),
        message_update(update_id=11, chat_id=123),
    ])
    controller = TelegramController(api, frozenset({123}), min_request_interval_seconds=0)
    assert controller.poll_once(offset=0) == 12
    assert len(api.sent) == 1
    assert api.sent[0][0] == 123
    assert "Live execution: disabled" in api.sent[0][1]


def test_empty_allowlist_fails_closed():
    with pytest.raises(ValueError, match="allowed private chat ID"):
        TelegramController(FakeAPI([]), frozenset())


def test_rate_limit_is_per_chat():
    controller = TelegramController(FakeAPI([]), frozenset({123}), min_request_interval_seconds=60)
    from unittest.mock import patch
    with patch("arb_engine.telegram_bot.time.monotonic", side_effect=[0.0, 1.0]):
        first = controller.response_for_update(message_update(text="/help"))
        second = controller.response_for_update(message_update(update_id=2, text="/status"))
    assert first is not None and "read-only research mode" in first[1]
    assert second is not None and "Rate limit" in second[1]


def test_bot_rejects_boolean_sender_id():
    controller = TelegramController(FakeAPI([]), frozenset({1}), min_request_interval_seconds=0)
    update = message_update(chat_id=1)
    update["message"]["from"]["id"] = True
    assert controller.response_for_update(update) is None


def test_telegram_api_errors_never_expose_bot_token_or_request_url(monkeypatch):
    from urllib.error import HTTPError

    token = "123456:secret-token-value"
    def reject(request, timeout):
        raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("arb_engine.telegram_bot.urlopen", reject)
    api = TelegramBotAPI(token)
    with pytest.raises(TelegramApiError) as captured:
        api.call("getMe", {})
    assert token not in str(captured.value)
    assert captured.value.__cause__ is None



def test_failed_reply_consumes_update_without_repeating_scan(monkeypatch):
    from arb_engine import telegram_bot

    updates = [message_update(update_id=11, text="/scan WETH/USDC")]

    class FailingSendAPI(FakeAPI):
        def get_updates(self, offset, timeout):
            return [item for item in self.updates if item["update_id"] >= offset]

        def send_message(self, chat_id, text):
            raise TelegramApiError("Telegram request failed: URLError")

    calls = []
    monkeypatch.setattr(
        telegram_bot,
        "scan",
        lambda *args: calls.append(args) or {"opportunities": []},
    )
    controller = TelegramController(
        FailingSendAPI(updates), frozenset({123}), min_request_interval_seconds=0,
    )

    next_offset = controller.poll_once(offset=0)
    assert next_offset == 12
    assert controller.poll_once(offset=next_offset) == 12
    assert len(calls) == 1
