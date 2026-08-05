"""
Phase 4 step 3 (overnight-position handling).

WHY THIS FILE EXISTS, SEPARATELY FROM OrderManager
------------------------------------------------------
OrderManager is deliberately day-scoped -- one fresh instance per
trading day, discarded at day rollover, matching DayOrchestrator's own
lifecycle. That was a reasonable design as long as every trade resolved
same-day (the simulation's own force-close-at-day_end behavior).

Confirmed design change (this phase's chat history): a real trade no
longer force-closes at day_end. Instead: it runs to NATURAL resolution
(hits its real stop-loss or take-profit), however many days that takes.
The only day_end-triggered action left is a RISK REDUCTION, not a
close: the first time a position is still open past 5pm NY, HALF its
volume gets closed, and the remaining half keeps running with its
existing stop/target untouched.

This means a real position can now legitimately outlive the CurrentDay
(and therefore the OrderManager) that opened it -- and even outlive a
runner restart. Nothing day-scoped can track something that spans
multiple days. This file is the answer: a tracker that lives on
ShadowRunner itself (constructed once, never rebuilt daily), and whose
state is rebuilt from the DATABASE at startup (not held only in memory)
so a restart mid-multi-day-trade doesn't lose track of it either --
same resilience philosophy as PHASE3_RESTART_RECOVERY.md's trend-history
seeding, applied here to open real positions instead.

WHAT THIS DOES NOT DO
-------------------------
Does not decide WHICH candidates become real trades (OrderManager's
job) or compute targets (also OrderManager). Purely: once a real
position exists, watch it until it's completely closed, handling the
one 5pm partial-close event along the way.

ASSUMPTIONS LOCKED IN (confirmed, this phase's chat history) -- not
re-litigated here, just enforced:
  1. "Half" means half the position's VOLUME, not a stop-distance
     adjustment.
  2. The partial close happens ONCE per trade -- if the remaining half
     is STILL open at a later day's 5pm, it is not halved again.
  3. Applies uniformly regardless of which weekday the 5pm crossing
     falls on, including into a weekend -- no special weekend handling.
"""
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from shadow_runner.persistence import (
    get_open_real_trades,
    update_trade_final_close,
    update_trade_partial_close,
    write_event,
)

log = logging.getLogger("shadow_runner.position_tracker")

NY_TZ = ZoneInfo("America/New_York")
PARTIAL_CLOSE_HOUR = 17  # 5pm NY, matches day_end everywhere else in this project


def now_ny() -> datetime:
    return datetime.now(NY_TZ).replace(tzinfo=None)


class PositionTracker:
    def __init__(self, bridge, session_factory, user_id: str, model_config: dict):
        self.bridge = bridge
        self.session_factory = session_factory
        self.user_id = user_id
        self.model_config = model_config  # for magic_number and model_name
        # ticket -> {"trade_id", "entry_time_ny", "partial_closed": bool}
        self._tracked: dict[int, dict] = {}

    def load_from_db(self) -> None:
        """Call once, at startup. Rebuilds tracking state for every
        trade this (user, model) still has open real exposure on --
        the actual mechanism that makes this resilient across restarts."""
        db = self.session_factory()
        try:
            rows = get_open_real_trades(db, self.user_id, self.model_config["model_name"])
        finally:
            db.close()
        for row in rows:
            self._tracked[row["real_position_ticket"]] = {
                "trade_id": row["trade_id"],
                "entry_time_ny": row["entry_time_ny"],
                "partial_closed": row["real_status"] == "partial_closed",
            }
        if self._tracked:
            log.info(
                "PositionTracker: resumed tracking %d still-open real position(s) from prior session: %s",
                len(self._tracked), list(self._tracked.keys()),
            )

    def register_new_position(self, ticket: int, trade_id, entry_time_ny: datetime) -> None:
        """Call the moment a trade row is written with a real, still-open
        fill (see runner.py's _write_trade()) -- hands off ongoing
        tracking from OrderManager (day-scoped, about to be discarded)
        to this tracker (persists across days)."""
        self._tracked[ticket] = {
            "trade_id": trade_id, "entry_time_ny": entry_time_ny, "partial_closed": False,
        }

    def _partial_close_threshold(self, entry_time_ny: datetime) -> datetime:
        """The entry day's 5pm NY -- assumption 3's uniform rule, no
        weekday/weekend special-casing. entry_time_ny is always < 17:00
        in practice (DayOrchestrator only creates candidates within
        session bounds, which end at day_end=17:00), so "the entry day's
        5pm" is always still in the future relative to entry itself."""
        return datetime.combine(entry_time_ny.date(), time(PARTIAL_CLOSE_HOUR, 0))

    def check_positions(self) -> None:
        """Call every poll cycle, unconditionally -- independent of
        whatever CurrentDay/OrderManager currently exists, since a
        tracked position may belong to a day that's long since rolled
        over."""
        if not self._tracked:
            return

        now = now_ny()
        magic = self.model_config["magic_number"]

        try:
            open_positions = self.bridge.get_positions(magic)
        except Exception as e:
            log.error("check_positions: bridge get_positions failed, will retry next poll: %s", e)
            return
        open_by_ticket = {p["ticket"]: p for p in open_positions}

        for ticket, info in list(self._tracked.items()):
            position = open_by_ticket.get(ticket)

            if position is None:
                self._handle_vanished(ticket, info)
                continue

            if not info["partial_closed"] and now >= self._partial_close_threshold(info["entry_time_ny"]):
                self._do_partial_close(ticket, info, position)

    def _do_partial_close(self, ticket: int, info: dict, position: dict) -> None:
        current_volume = position["volume"]
        # Simple half-rounding to 2 decimals (the most common MT5
        # volume_step). Does not fetch the symbol's real volume_step the
        # way order_manager.compute_lot_size() does for entries -- a
        # known simplification, not full precision, flagged here rather
        # than silently assumed correct for every possible broker
        # configuration.
        half_volume = round(current_volume / 2, 2)
        if half_volume <= 0 or half_volume >= current_volume:
            log.error(
                "Ticket %s: computed half_volume=%s is invalid for current_volume=%s -- "
                "skipping partial close this cycle, will retry next poll",
                ticket, half_volume, current_volume,
            )
            return

        try:
            result = self.bridge.close_position_partial(ticket, half_volume)
        except Exception as e:
            log.error("Ticket %s: partial close failed, will retry next poll: %s", ticket, e)
            return

        db = self.session_factory()
        try:
            update_trade_partial_close(
                db, info["trade_id"],
                partial_close_price=result["close_price"],
                partial_close_time_utc=result["time_utc"],
                partial_close_time_ny=result["time_ny"],
                partial_close_volume=result["closed_volume"],
                # MT5's order_send result doesn't include realized profit
                # directly for a partial close the way history_deals_get
                # does for a final close -- left None here rather than
                # guessing; the FINAL close's profit (from real close
                # history) remains accurate regardless of this gap.
                partial_close_profit=None,
            )
            write_event(
                db,
                {
                    "event_type": "partial_close_executed",
                    "timestamp": now_ny(),
                    "ticket": ticket,
                    "closed_volume": result["closed_volume"],
                    "close_price": result["close_price"],
                    "remaining_volume": result["remaining_volume"],
                },
                self.user_id, self.model_config["model_name"],
            )
            db.commit()
        finally:
            db.close()

        info["partial_closed"] = True
        log.info(
            "Ticket %s: partial-closed %s lots at %s (5pm NY crossing), %s lots remain open",
            ticket, result["closed_volume"], result["close_price"], result["remaining_volume"],
        )

    def _handle_vanished(self, ticket: int, info: dict) -> None:
        try:
            history = self.bridge.get_position_history(ticket)
        except Exception as e:
            log.error(
                "Ticket %s: vanished, but could not fetch close history: %s -- will retry next poll",
                ticket, e,
            )
            return
        if not history.get("is_closed"):
            log.warning(
                "Ticket %s vanished from open positions but history shows not yet "
                "closed -- likely broker-side history-cache lag, will retry next poll",
                ticket,
            )
            return

        db = self.session_factory()
        try:
            update_trade_final_close(
                db, info["trade_id"],
                close_price=history["close_price"],
                close_time_utc=history["close_time_utc"],
                close_time_ny=history["close_time_ny"],
                profit=history["profit"],
                close_reason=history["close_reason"],
            )
            write_event(
                db,
                {
                    "event_type": "real_trade_closed",
                    "timestamp": now_ny(),
                    "ticket": ticket,
                    "close_price": history["close_price"],
                    "profit": history["profit"],
                    "close_reason": history["close_reason"],
                },
                self.user_id, self.model_config["model_name"],
            )
            db.commit()
        finally:
            db.close()

        del self._tracked[ticket]
        log.info(
            "Ticket %s: fully resolved -- %s at %s, profit=%s",
            ticket, history["close_reason"], history["close_price"], history["profit"],
        )