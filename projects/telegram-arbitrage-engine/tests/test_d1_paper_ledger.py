import io
import urllib.error

import pytest

from arb_engine.d1_paper_ledger import (
    D1PaperLedgerError,
    D1PaperTradingLedger,
    _RejectRedirects,
)


def test_authenticated_d1_client_rejects_redirects_without_following(monkeypatch):
    captured = {"requests": [], "handlers": []}

    class FakeOpener:
        def open(self, request, timeout):
            captured["requests"].append((request, timeout))
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://attacker.example/collect"},
                io.BytesIO(b""),
            )

    def build_opener(*handlers):
        captured["handlers"].extend(handlers)
        return FakeOpener()

    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    client = D1PaperTradingLedger(
        "https://engine.example.workers.dev",
        "sensitive-test-token",
    )

    with pytest.raises(D1PaperLedgerError, match="HTTP 302"):
        client.events("candidate-1")

    assert len(captured["requests"]) == 1
    request, timeout = captured["requests"][0]
    assert request.full_url.startswith("https://engine.example.workers.dev/")
    assert request.get_header("Authorization") == "Bearer sensitive-test-token"
    assert timeout == client.timeout
    assert len(captured["handlers"]) == 1
    handler = captured["handlers"][0]
    assert isinstance(handler, _RejectRedirects)
    assert handler.redirect_request(
        request, io.BytesIO(), 302, "Found",
        {"Location": "https://attacker.example/collect"},
        "https://attacker.example/collect",
    ) is None
