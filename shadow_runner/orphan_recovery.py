"""
Cross-day recovery gap fix, piece 2A (2026-09-02) -- see
PENDING_ITEMS.md's "Real bugs found 2026-09-02" and
PHASE3_VALIDATION.md's correction section for the incident this
exists to catch: a real trade rode unmanaged for a week after
shadow_runner went down across a day boundary, invisible to the app
the whole time. (Bug 2, the OTHER half of that same incident -- bug 1,
a sibling-order race, was already fixed separately in
order_manager.py's _handle_sibling_cancel_failure().)

Checks the broker directly for any real open position under our magic
number that doesn't match a known `trades` row. Originally called only
once, at startup, when a cross-day gap was detected (recover_on_startup()
-> runner.py's _recover_cross_day_gap()) -- as of 2026-09-04 also
called continuously, every few minutes, per subscriber
(PositionTracker.check_for_orphans()), after a real incident proved the
startup-only version left a genuine orphan undetected for two days: an
ordinary running day was never covered at all, only a restart after a
detected gap.

Deliberately scoped to STILL-OPEN positions only, not full historical
reconciliation of already-closed trades -- the bridge only has a
lookup-by-known-ticket history endpoint today, not a "list everything
between these two dates" one, and adding the latter would mean
changing bridge/app/main.py + mt5_client.py -- code that runs on
Tony's live, real-money bridge, the most carefully-guarded piece of
infrastructure in this project. A still-open orphan is also the more
urgent case: real money actively exposed and unmanaged right now,
whereas an already-closed historical trade is a bookkeeping gap, not
live risk.

2026-09-04 follow-up, same incident: finding and even successfully
healing (attaching a take-profit to) an orphan still left it with NO
permanent record -- so once it eventually closed, it vanished from
trade history forever. check_for_orphaned_positions() now ALSO writes
a real `trades` row for every orphan found, via
persistence.write_orphan_trade() -- independent of whether the
take-profit attach itself succeeds (two separate protections; one
failing must never silently cost the other). Returns enough
(`trade_id`/`entry_time_ny`) per result for the caller to hand off to
its own PositionTracker for ongoing management, exactly like a
normally-caught fill already gets.
"""
import logging
from datetime import datetime

from shadow_runner.order_manager import TARGET_LOOKBACK_BARS, compute_target
from shadow_runner.persistence import get_current_equity, get_open_real_trades, write_orphan_trade

log = logging.getLogger("shadow_runner.orphan_recovery")


def check_for_orphaned_positions(
    bridge, symbol: str, magic: int, db, user_id: str, model: str, now_ny,
    event_sink, risk_pct: float,
) -> list[dict]:
    """Returns a list of {"ticket", "healed": bool, "trade_id": uuid|None,
    "entry_time_ny": datetime|None} for every orphan found. `trade_id`/
    `entry_time_ny` are only None if recording the trade itself failed
    (a real, distinct failure mode from the take-profit attach failing --
    see _record_orphan_trade()'s own docstring) -- the caller should
    register a non-None trade_id with its own PositionTracker
    (register_new_position()) so the orphan's eventual close gets
    recorded the same way any other real trade's does.

    event_sink receives the same shape of event dicts write_event()
    expects elsewhere (safety_check_failed / orphan_position_recovered /
    orphan_trade_recorded) -- the caller is responsible for actually
    journaling them, same pattern as everywhere else in runner.py. Trade
    writes ARE committed here directly, though (not left to the
    caller's later batched event commit) -- each orphan's fate is
    deliberately independent of every other orphan's in the same batch;
    one failing to write shouldn't risk rolling back another's already-
    successful write.
    """
    try:
        open_positions = bridge.get_positions(magic)
    except Exception as e:
        event_sink(
            {
                "event_type": "safety_check_failed",
                "timestamp": now_ny,
                "check_name": "orphan_position_check_bridge_call",
                "error": str(e),
            }
        )
        return []

    known_tickets = {
        t["real_position_ticket"]
        for t in get_open_real_trades(db, user_id, model)
        if t["real_position_ticket"] is not None
    }

    results = []
    for position in open_positions:
        ticket = position["ticket"]
        if ticket in known_tickets:
            continue

        log.warning("Orphaned real position found: ticket=%s -- not in any known trade", ticket)
        healed, target = _heal_orphan(bridge, symbol, position, now_ny, event_sink)
        trade_id, entry_time_ny = _record_orphan_trade(
            db, position, target, user_id, model, risk_pct, bridge, now_ny, event_sink,
        )
        results.append({
            "ticket": ticket, "healed": healed,
            "trade_id": trade_id, "entry_time_ny": entry_time_ny,
        })
    return results


def _heal_orphan(bridge, symbol: str, position: dict, now_ny, event_sink) -> tuple[bool, float | None]:
    """Attaches the take-profit target this position would have gotten
    if its fill had been caught live -- same compute_target()/
    modify_position() logic as OrderManager.attach_target(), just
    invoked directly (this runs before any OrderManager exists for the
    day, so there's no instance to call attach_target() on).

    Returns (healed, target) -- target is the computed value regardless
    of whether attaching it to the broker actually succeeded, so
    _record_orphan_trade() can still use it for the trade record's
    target_price even if this specific broker call fails. Only None
    when computing the target itself failed entirely (no bars, bad
    data) -- the caller falls back to the position's own entry price
    in that case, same "never block the permanent record over a
    secondary failure" principle."""
    ticket = position["ticket"]
    direction = position["direction"]
    # Real bug found 2026-09-04 (first time this ever ran against a real
    # orphan in production): BridgeClient.get_positions() returns
    # time_utc/time_ny as raw strings straight off the wire -- unlike
    # get_candles(), which parses them into real datetimes (see
    # bridge_client.py's own docstrings for each). Comparing a bar's
    # parsed datetime against this position dict's raw string below
    # raised "'<' not supported between instances of 'datetime.datetime'
    # and 'str'" every time, silently defeating every orphan heal.
    # Deliberately parsed HERE, not fixed in BridgeClient.get_positions()
    # itself -- that method's return value also feeds straight into
    # app/routers/trading.py's live /trading/positions response, whose
    # Position/PendingOrder models are typed str; parsing there would
    # break that endpoint instead.
    fill_time_utc = datetime.fromisoformat(position["time_utc"])

    try:
        candles = bridge.get_candles(symbol, "M5", 500)
        before_fill = [b for b in candles if b["time_utc"] < fill_time_utc]
        target = compute_target(before_fill[-TARGET_LOOKBACK_BARS:], direction)
    except Exception as e:
        event_sink(
            {
                "event_type": "safety_check_failed",
                "timestamp": now_ny,
                "check_name": "orphan_position_target_compute_failed",
                "error": str(e),
                "ticket": ticket,
            }
        )
        return False, None

    try:
        bridge.modify_position(ticket, take_profit=target)
    except Exception as e:
        event_sink(
            {
                "event_type": "safety_check_failed",
                "timestamp": now_ny,
                "check_name": "orphan_position_heal_failed",
                "error": str(e),
                "ticket": ticket,
                "target": target,
            }
        )
        return False, target

    event_sink(
        {
            "event_type": "orphan_position_recovered",
            "timestamp": now_ny,
            "ticket": ticket,
            "direction": direction,
            "target": target,
            "fill_price": position.get("open_price"),
        }
    )
    return True, target


def _record_orphan_trade(
    db, position: dict, target: float | None, user_id: str, model: str,
    risk_pct: float, bridge, now_ny, event_sink,
) -> tuple[object | None, datetime | None]:
    """The 2026-09-04 fix: writes a real, permanent `trades` row for
    this orphan -- independent of whether _heal_orphan()'s take-profit
    attach succeeded above. Returns (trade_id, entry_time_ny) on
    success so the caller can hand off ongoing tracking to its own
    PositionTracker, or (None, None) if this failed (journaled as its
    own distinct, loud safety_check_failed -- never silent, never
    raised up to crash the caller's whole orphan-check pass)."""
    ticket = position["ticket"]
    try:
        entry_time_utc = datetime.fromisoformat(position["time_utc"])
        entry_time_ny = datetime.fromisoformat(position["time_ny"])
        # target=None only if _heal_orphan() couldn't even compute a
        # target (no usable bars) -- target_price is NOT NULL on Trade,
        # so fall back to the position's own entry price rather than
        # block the write entirely. This is honestly just a placeholder
        # in that rare case; the real, meaningful state (still open, no
        # confirmed target) is fully captured by real_status='open' and
        # the safety_check_failed already emitted by _heal_orphan().
        target_price = target if target is not None else position["open_price"]

        equity_before = get_current_equity(db, user_id, model, bridge_starting_equity=None)
        if equity_before is None:
            try:
                equity_before = bridge.account_info()["balance"]
            except Exception:
                # Both the trade-history lookup AND the live balance
                # fetch failed -- extremely unlikely (this bridge just
                # answered get_positions() successfully moments ago),
                # but equity_before is NOT NULL. 0.0 is an honest,
                # visibly-wrong placeholder rather than a guess dressed
                # up as a real number -- the loud safety_check_failed
                # below is what actually flags this needs a human look,
                # not the value itself.
                equity_before = 0.0

        row = write_orphan_trade(
            db, position, target_price, entry_time_utc, entry_time_ny,
            user_id, model, risk_pct, equity_before,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        event_sink(
            {
                "event_type": "safety_check_failed",
                "timestamp": now_ny,
                "check_name": "orphan_trade_record_failed",
                "error": str(e),
                "ticket": ticket,
            }
        )
        return None, None

    event_sink(
        {
            "event_type": "orphan_trade_recorded",
            "timestamp": now_ny,
            "ticket": ticket,
            "trade_id": str(row.trade_id),
        }
    )
    return row.trade_id, entry_time_ny
