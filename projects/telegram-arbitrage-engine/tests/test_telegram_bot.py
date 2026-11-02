import pytest

from arb_engine.telegram_bot import TelegramController


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
    first = controller.response_for_update(message_update(text="/help"))
    second = controller.response_for_update(message_update(update_id=2, text="/status"))
    assert first is not None and "read-only research mode" in first[1]
    assert second is not None and "Rate limit" in second[1]
