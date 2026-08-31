"""
Telegram alert sender (logging/audit review part 3 -- monitoring/
alerting). Posts to a group chat via the Bot API's sendMessage.

Dormant by default: no-ops (with one warning log, not per-call) if
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID aren't set, so every call site can
call this unconditionally without checking configuration itself. Any
send failure (network error, bad token, rate limit) is caught and
logged, never raised -- alerting must never be able to crash the real
thing it's alerting about (same "fail loud on the real action, fail
quiet on the side-channel" split app.core.audit's commit_audit_or_log()
already uses for audit logging).
"""
import logging

import requests

from app.core.config import settings

log = logging.getLogger("app.core.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_warned_unconfigured = False


def send_telegram_alert(text: str) -> None:
    global _warned_unconfigured
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        if not _warned_unconfigured:
            log.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set -- alerts disabled")
            _warned_unconfigured = True
        return

    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        resp = requests.post(
            url, json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=5,
        )
        if resp.status_code != 200:
            log.warning("Telegram alert send failed: %s %s", resp.status_code, resp.text)
    except Exception:
        log.warning("Telegram alert send raised an exception", exc_info=True)
