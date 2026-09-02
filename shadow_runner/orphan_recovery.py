"""
Cross-day recovery gap fix, piece 2A (2026-09-02) -- see
PENDING_ITEMS.md's "Real bugs found 2026-09-02" and
PHASE3_VALIDATION.md's correction section for the incident this
exists to catch: a real trade rode unmanaged for a week after
shadow_runner went down across a day boundary, invisible to the app
the whole time. (Bug 2, the OTHER half of that same incident -- bug 1,
a sibling-order race, was already fixed separately in
order_manager.py's _handle_sibling_cancel_failure().)

Called once from runner.py's recover_on_startup(), ONLY when a
cross-day gap was actually detected -- an ordinary same-day restart
has no orphan risk, since OrderManager's state gets rebuilt fresh from
the DB the same way it always has. Checks the broker directly for any
real open position under our magic number that doesn't match a known
`trades` row.

Deliberately scoped to STILL-OPEN positions only, not full historical
reconciliation of already-closed trades -- the bridge only has a
lookup-by-known-ticket history endpoint today, not a "list everything
between these two dates" one, and adding the latter would mean
changing bridge/app/main.py + mt5_client.py -- code that runs on
Tony's live, real-money bridge, the most carefully-guarded piece of
infrastructure in this project. A still-open orphan is also the more
urgent case: real money actively exposed and unmanaged right now,
whereas an already-closed historical trade is a bookkeeping gap, not
live risk. See the plan doc (misty-seeking-crescent.md in this
session's history) for the full reasoning.
"""
import logging

from shadow_runner.order_manager import TARGET_LOOKBACK_BARS, compute_target
from shadow_runner.persistence import get_open_real_trades

log = logging.getLogger("shadow_runner.orphan_recovery")


def check_for_orphaned_positions(
    bridge, symbol: str, magic: int, db, user_id: str, model: str, now_ny, event_sink
) -> list[dict]:
    """Returns a list of {"ticket", "healed": bool} for every orphan
    found. event_sink receives the same shape of event dicts
    write_event() expects elsewhere (safety_check_failed /
    orphan_position_recovered) -- the caller (recover_on_startup) is
    responsible for actually journaling them, same pattern as
    everywhere else in runner.py (this module never touches the DB for
    writes itself, only get_open_real_trades() for a read).
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
        healed = _heal_orphan(bridge, symbol, position, now_ny, event_sink)
        results.append({"ticket": ticket, "healed": healed})
    return results


def _heal_orphan(bridge, symbol: str, position: dict, now_ny, event_sink) -> bool:
    """Attaches the take-profit target this position would have gotten
    if its fill had been caught live -- same compute_target()/
    modify_position() logic as OrderManager.attach_target(), just
    invoked directly (this runs before any OrderManager exists for the
    day, so there's no instance to call attach_target() on)."""
    ticket = position["ticket"]
    direction = position["direction"]
    fill_time_utc = position["time_utc"]

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
        return False

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
        return False

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
    return True
