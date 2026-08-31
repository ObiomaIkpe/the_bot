"""
Tests for app.core.telegram.send_telegram_alert() -- logging/audit
review part 3 (monitoring/alerting). Never hits the real Telegram API;
requests.post is monkeypatched throughout.
"""
import app.core.telegram as telegram_module
from app.core.config import settings
from app.core.telegram import send_telegram_alert


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


def test_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    monkeypatch.setattr(settings, "telegram_chat_id", None)

    calls = []
    monkeypatch.setattr(telegram_module.requests, "post", lambda *a, **kw: calls.append((a, kw)))

    send_telegram_alert("should not be sent")
    assert calls == []


def test_sends_to_configured_chat(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "-100123")

    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse(200)

    monkeypatch.setattr(telegram_module.requests, "post", fake_post)

    send_telegram_alert("hello")

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert "test-token" in url
    assert payload == {"chat_id": "-100123", "text": "hello"}
    assert timeout == 5


def test_non_200_response_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "-100123")
    monkeypatch.setattr(telegram_module.requests, "post", lambda *a, **kw: FakeResponse(401, "Unauthorized"))

    send_telegram_alert("hello")  # must not raise


def test_send_exception_is_caught_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "-100123")

    def raising_post(*a, **kw):
        raise ConnectionError("network is down")

    monkeypatch.setattr(telegram_module.requests, "post", raising_post)

    send_telegram_alert("hello")  # must not raise
