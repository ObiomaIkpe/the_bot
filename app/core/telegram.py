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


# 2026-09-04 write-path audit: previously, "which event types page a
# human and how the message reads" was decided inline, separately, at
# runner.py's _write_events_now() -- the ONE place events got flushed
# from DayOrchestrator/OrderManager's shared event_sink. That was fine
# as long as it was the only place anything committed an event. It
# stopped being fine once PositionTracker (its own DB session, never
# routed through that event_sink) started writing real safety-check
# failures AND orphan-discovery events directly -- confirmed by tracing
# the actual code that every one of those was journaled (visible in
# /events) but silently never reached this module at all, un-alerted,
# for the entire time Telegram alerting has been live. Centralizing the
# decision here, callable from every place that commits an event
# (currently four: _write_events_now(), PositionTracker.
# _emit_check_failure(), PositionTracker.check_for_orphans(), and
# runner.py's two direct real-trade-write failure writes), means a
# future alert-worthy event type only needs adding once, here -- not
# re-discovered missing at each call site the way this one was.
def alert_for_event(event: dict, user_id, model: str) -> None:
    event_type = event.get("event_type")
    if event_type == "safety_check_failed":
        send_telegram_alert(
            f"⚠️ safety_check_failed: {event.get('check_name')} "
            f"(user={user_id}, model={model})\n{event.get('error')}"
        )
    elif event_type == "order_placement_failed":
        send_telegram_alert(
            f"🔴 order_placement_failed "
            f"(user={user_id}, model={model}, candidate={event.get('candidate_key')})\n"
            f"{event.get('error')}"
        )
    elif event_type == "orphan_position_recovered":
        # 2026-09-04: added alongside centralizing this function --
        # finding a real position with no record of it is exactly the
        # kind of thing tonight's whole incident was about, whether or
        # not the self-heal (target attach) itself also succeeded.
        # Worth paging on regardless, not just on an outright failure.
        send_telegram_alert(
            f"🚨 orphan_position_recovered: a real position (ticket {event.get('ticket')}) "
            f"was found with no record of it (user={user_id}, model={model}), "
            f"target={event.get('target')}"
        )
    elif event_type == "orphan_trade_recorded":
        send_telegram_alert(
            f"🚨 orphan_trade_recorded: permanent trade record created for ticket "
            f"{event.get('ticket')} (user={user_id}, model={model})"
        )
