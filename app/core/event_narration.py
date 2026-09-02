"""
Turns a raw (event_type, details) pair into a plain-English sentence a
real trader can read, with no knowledge of this codebase's internals.
Built for the trader-facing trade story (app/core/trade_story.py,
GET /trades/{trade_id}/event-chain) but used by EventOut.from_model()
itself, so every existing consumer of events (GET /events, GET
/admin/events) gets real sentences instead of raw event-type strings
for free.

One template per event type that can actually appear in a trade's
chain or a general activity feed. Deliberately reads directly off each
event's own `details` dict (the same shape the emitting code in
phase1/streaming/ and shadow_runner/ already produces) rather than a
parallel/duplicated field list -- if a detail field this reads from
ever gets renamed, this needs updating too; there's no schema
enforcement tying the two together beyond this comment.

Never raises: a missing/malformed field falls back to the generic
"prettified event_type" sentence rather than a 500 -- a slightly less
informative sentence is a much better failure mode here than crashing
a trader's trade-detail page.
"""


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.5f}"
    return str(value)


def _generic_fallback(event_type: str) -> str:
    return event_type.replace("_", " ").capitalize() + "."


def narrate_event(event_type: str, details: dict) -> str:
    try:
        return _narrate(event_type, details)
    except (KeyError, TypeError, ValueError):
        return _generic_fallback(event_type)


def _narrate(event_type: str, details: dict) -> str:
    if event_type == "raid_detected":
        direction = "long" if details["direction"] == "bull" else "short"
        raid_price = details.get("raid_bar_low", details.get("raid_bar_high"))
        return (
            f"Price swept past the prior swing level at {_fmt(details['raid_level'])} "
            f"(reaching {_fmt(raid_price)}) -- a liquidity raid, setting up a possible {direction}."
        )

    if event_type == "mss_confirmed":
        direction = "bullish" if details["direction"] == "bull" else "bearish"
        return (
            f"Price closed at {_fmt(details['close'])}, past the {_fmt(details['level'])} structure "
            f"level -- confirming a {direction} shift in market structure."
        )

    if event_type == "fvg_found":
        direction = "long" if details["direction"] == "bull" else "short"
        return (
            f"A fair value gap formed between {_fmt(details['bottom'])} and {_fmt(details['top'])} "
            f"-- the entry zone for this {direction} setup."
        )

    if event_type == "fvg_rejected_min_stop":
        return (
            f"A setup formed but was rejected: the stop distance ({_fmt(details['risk_pips'])} pips) "
            f"was too tight to trade."
        )

    if event_type == "trade_candidate_ready":
        direction = "long" if details["direction"] in ("bull", "long") else "short"
        return (
            f"Candidate ready: {direction} at {_fmt(details['entry'])}, stop at {_fmt(details['stop'])}."
        )

    if event_type == "pending_order_placed":
        return f"A pending order was placed with the broker: entry {_fmt(details['entry'])}, stop {_fmt(details['stop'])}."

    if event_type == "order_filled" or event_type == "candidate_filled":
        price = details.get("entry", details.get("fill_price"))
        return f"Filled at {_fmt(price)}."

    if event_type == "target_attached":
        return f"Take-profit target set at {_fmt(details['target'])}."

    if event_type == "trade_closed":
        outcome = details["outcome"]
        return f"Closed ({outcome}) at {_fmt(details['exit_price'])}."

    if event_type == "real_trade_closed":
        profit = details["profit"]
        sign = "+" if profit >= 0 else ""
        return (
            f"Real position closed at {_fmt(details['close_price'])} "
            f"({details['close_reason']}), P&L {sign}{_fmt(profit)}."
        )

    if event_type == "partial_close_executed":
        return (
            f"Half the position was closed at 5pm NY (risk reduction): "
            f"{_fmt(details['closed_volume'])} lots at {_fmt(details['close_price'])}, "
            f"{_fmt(details['remaining_volume'])} lots still running."
        )

    if event_type == "order_placement_failed":
        return f"The broker rejected this order: {details['error']}"

    if event_type == "order_skipped_paused":
        reason = "the account is paused" if details.get("reason") == "account_paused" else "this model is paused"
        return f"A signal fired but no order was placed, because {reason}."

    if event_type == "safety_check_failed":
        return f"A safety check ({details['check_name']}) failed: {details['error']}"

    if event_type == "duplicate_fill_closed":
        return (
            "Two competing setups filled at nearly the same instant -- one was the "
            "real trade, the other was closed immediately as a duplicate rather than "
            "left running unmanaged."
        )

    if event_type == "daily_loss_threshold_crossed":
        return (
            f"Today's realized loss ({_fmt(details['realized_loss_pct'])}%) crossed the "
            f"configured max ({_fmt(details['max_daily_loss_pct'])}%)."
        )

    return _generic_fallback(event_type)
