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
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.telegram import alert_for_event
from shadow_runner.orphan_recovery import check_for_orphaned_positions
from shadow_runner.persistence import (
    get_open_real_trades,
    update_trade_final_close,
    update_trade_partial_close,
    write_event,
)

log = logging.getLogger("shadow_runner.position_tracker")

NY_TZ = ZoneInfo("America/New_York")
PARTIAL_CLOSE_HOUR = 17  # 5pm NY, matches day_end everywhere else in this project

# Continuous orphan-check, added 2026-09-04 after a real incident: a
# genuinely orphaned position (see check_for_orphans() below) sat
# undetected for two days because the only existing orphan check ran
# solely at startup, after a detected cross-day gap -- not something
# that happens on an ordinary day with no restart. Throttled rather
# than run every poll (default 60s) -- a full "list every broker
# position, compare against the database" check is real, ongoing
# bridge/DB load for a condition that should now be rare (the ONE
# confirmed cause, a sibling-fill race, was fixed the same night this
# was added) -- 5 minutes balances catching a real orphan quickly
# against not hammering the bridge forever for no reason.
ORPHAN_CHECK_INTERVAL = timedelta(minutes=5)


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
        # Continuous orphan-check throttle -- None until the first check
        # actually runs, so the very first poll after startup/construction
        # always checks immediately rather than waiting a full interval.
        self._last_orphan_check: datetime | None = None

    def _emit_check_failure(self, check_name: str, error: Exception, **extra) -> None:
        """Same reliability fix as OrderManager._emit_check_failure() --
        see its docstring for the full reasoning. PositionTracker has no
        shared event_sink to append to (unlike OrderManager, which
        piggybacks on DayOrchestrator's day-scoped one), so this opens
        its own short DB session directly, matching how every other
        write in this class already works.

        2026-09-04 write-path audit fix: previously journaled to the DB
        only, unlike OrderManager's own _emit_check_failure() (which
        DOES reach a live Telegram alert -- its event_sink ultimately
        flushes through runner.py's _write_events_now(), the one place
        that used to send alerts). Confirmed by tracing the actual
        code, not assumed: PositionTracker writes its own events
        directly, via its own session, bypassing _write_events_now()
        entirely -- so every one of THIS class's safety checks (check_
        positions, the continuous orphan check, partial-close,
        final-close, and everything else routed through this method)
        was silently DB-only, journaled but never paging anyone,
        despite this project's Telegram alerting having gone live the
        same night specifically to close that exact gap for
        OrderManager's failures. Fixed by alerting here too, via the
        now-centralized app.core.telegram.alert_for_event() (see its
        own docstring), right after a successful commit -- matching
        _write_events_now()'s own "only alert once actually journaled"
        discipline (using try/except/else, not just appending after the
        try block, so a failure IN the commit itself still correctly
        skips the alert)."""
        log.error("%s failed: %s", check_name, error)
        event = {
            "event_type": "safety_check_failed",
            "timestamp": now_ny(),
            "check_name": check_name,
            "error": str(error),
            **extra,
        }
        db = self.session_factory()
        try:
            write_event(db, event, self.user_id, self.model_config["model_name"])
            db.commit()
        except Exception as inner_e:
            # If even journaling the failure fails, don't let THAT crash
            # anything either -- the log.error() call above already
            # happened, so the failure isn't completely invisible even
            # in this worst case.
            log.error("Additionally failed to journal the above failure: %s", inner_e)
        else:
            alert_for_event(event, self.user_id, self.model_config["model_name"])
        finally:
            db.close()

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
            self._emit_check_failure("position_tracker_check_positions", e)
            return
        open_by_ticket = {p["ticket"]: p for p in open_positions}

        for ticket, info in list(self._tracked.items()):
            position = open_by_ticket.get(ticket)

            if position is None:
                self._handle_vanished(ticket, info)
                continue

            if not info["partial_closed"] and now >= self._partial_close_threshold(info["entry_time_ny"]):
                self._do_partial_close(ticket, info, position)

    def check_for_orphans(self, symbol: str) -> None:
        """Call every poll cycle, unconditionally -- see
        ORPHAN_CHECK_INTERVAL's comment for why this is throttled rather
        than a no-op skip. Deliberately does NOT gate on self._tracked
        being non-empty the way check_positions() does above -- an
        orphan is, by definition, not yet in self._tracked; gating on
        that would make this permanently blind to the exact case it
        exists to catch. Reuses check_for_orphaned_positions() (the same
        function recover_on_startup() already calls after a detected
        cross-day gap) -- this is that same safety net, just running on
        a regular cadence instead of only at a rare startup."""
        now = now_ny()
        if self._last_orphan_check is not None and (now - self._last_orphan_check) < ORPHAN_CHECK_INTERVAL:
            return
        self._last_orphan_check = now

        db = self.session_factory()
        try:
            collected_events = []
            results = check_for_orphaned_positions(
                self.bridge, symbol, self.model_config["magic_number"],
                db, self.user_id, self.model_config["model_name"], now,
                event_sink=collected_events.append, risk_pct=self.model_config["risk_pct"],
            )
            for e in collected_events:
                write_event(db, e, self.user_id, self.model_config["model_name"])
            db.commit()
            # 2026-09-04 write-path audit fix: this is the single most
            # important alert-wiring gap found in this whole audit --
            # this IS the continuous, every-5-minutes safety net built
            # earlier tonight specifically because the two real orphaned
            # positions that started this whole session's work sat
            # unnoticed for two days. Every event it finds
            # (orphan_position_recovered, orphan_trade_recorded, and any
            # embedded safety_check_failed from a heal/record failure)
            # was, until this fix, journaled and then silently invisible
            # -- exactly the same failure mode the incident itself was,
            # just moved one level down the stack. Only fires once the
            # commit above actually succeeded.
            for e in collected_events:
                alert_for_event(e, self.user_id, self.model_config["model_name"])
            if results:
                log.warning(
                    "Continuous orphan check for user_id=%s found %d position(s): %s",
                    self.user_id, len(results), results,
                )
                # 2026-09-04 fix: hand off every orphan that got a real
                # trade record (see check_for_orphaned_positions()'s own
                # docstring) to THIS SAME tracker's ongoing management --
                # otherwise the record exists but nothing ever watches
                # for its natural close, defeating half the point.
                for r in results:
                    if r["trade_id"] is not None:
                        self.register_new_position(r["ticket"], r["trade_id"], r["entry_time_ny"])
        except Exception as e:
            # Belt-and-suspenders on top of check_for_orphaned_positions()'s
            # own internal bridge-call handling -- get_open_real_trades()
            # (a DB call) is NOT wrapped there, so a DB error here would
            # otherwise propagate up and could crash this subscriber's
            # whole poll cycle.
            self._emit_check_failure("continuous_orphan_check", e)
        finally:
            db.close()

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
            self._emit_check_failure(
                "partial_close_volume_validation",
                ValueError(f"half_volume={half_volume} invalid for current_volume={current_volume}"),
                ticket=ticket,
            )
            return

        try:
            result = self.bridge.close_position_partial(ticket, half_volume)
        except Exception as e:
            self._emit_check_failure("close_position_partial", e, ticket=ticket, half_volume=half_volume)
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
        except Exception as e:
            # 2026-09-04 fix: unlike every other risky operation in this
            # file (bridge calls above), this DB write previously had no
            # except clause at all -- a failure here propagated straight
            # out of check_positions()'s for-loop, silently skipping
            # every OTHER tracked ticket for this subscriber for the rest
            # of this poll (only caught, generically, at the outer
            # poll_once() level, and never journaled). Also more
            # dangerous than a normal write failure: the broker-side
            # partial close above ALREADY happened -- real money already
            # moved -- before this write was even attempted. If left
            # retryable, the next poll would call
            # bridge.close_position_partial() a SECOND time against the
            # now-smaller remaining volume, over-closing the position.
            # So: roll back, journal loudly (needs manual reconciliation
            # of this ticket's partial-close numbers), and mark it
            # partial_closed anyway -- the broker action is real and
            # irreversible; retrying it would compound the problem
            # instead of fixing the missing record.
            db.rollback()
            self._emit_check_failure(
                "partial_close_db_write_failed", e, ticket=ticket,
                note="broker-side partial close already executed -- DB record failed, needs manual reconciliation",
            )
            info["partial_closed"] = True
            return
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
            self._emit_check_failure("position_tracker_get_history", e, ticket=ticket)
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
        except Exception as e:
            # 2026-09-04 fix: same class of gap as _do_partial_close()'s
            # own comment -- previously no except clause at all here, so
            # a write failure propagated out of check_positions()'s
            # for-loop, silently skipping every OTHER tracked ticket for
            # this subscriber for the rest of this poll, never journaled.
            # Simpler than the partial-close case, though: this function
            # only ever READS broker history (get_position_history()
            # above), never triggers a broker-side action -- so it's safe
            # to just roll back, journal, and leave the ticket in
            # self._tracked for an ordinary retry next poll, same as the
            # "history cache lag" branch above already does.
            db.rollback()
            self._emit_check_failure("final_close_db_write_failed", e, ticket=ticket)
            return
        finally:
            db.close()

        del self._tracked[ticket]
        log.info(
            "Ticket %s: fully resolved -- %s at %s, profit=%s",
            ticket, history["close_reason"], history["close_price"], history["profit"],
        )