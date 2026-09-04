"""
The shadow runner: polls the Phase 2 bridge for newly-closed bars, feeds
them through DaySelectionGate + DayOrchestrator exactly as designed, and
journals every event and trade to Postgres. Places zero real orders --
nothing in this file, or anything it imports, has an order-placement
call available to make even if it wanted to.

HOW A DAY UNFOLDS, IN ORDER
----------------------------
1. First bar of a new NY calendar day arrives -> CurrentDay created.
2. Every bar updates the running full-day high/low (for tomorrow's
   trend calc) AND, if it falls in the 5am-5pm NY window, gets appended
   to the day's session-bar buffer.
3. Once the buffer reaches 10am NY (CurrentDay.ready_to_decide()),
   DaySelectionGate.gate_for_day() runs ONCE for the day:
     - Not tradeable (FOMC/no-trend/insufficient-bars/no-session-start)
       -> journal a day_skipped_* event, done for today.
     - Tradeable -> journal day_trend_determined, construct a fresh
       DayOrchestrator with the now-final session_start_idx/
       session_end_idx, and backfill EVERY session bar seen so far
       (5am through now) into it in one pass.
4. Every session bar arriving AFTER that point feeds the orchestrator
   one at a time, live, as normal.
5. Day rollover (a bar from a new NY date arrives) triggers finalize:
   the orchestrator's finalize() runs, any resulting trade gets written
   to `trades`, and the day's accumulated high/low feeds
   DaySelectionGate.on_day_closed() (unconditionally -- daily swing
   detection runs on every day regardless of whether it was tradeable,
   matching the batch script).

WHY STEP 3 WAITS UNTIL 10AM, NOT 7AM (SESSION START)
-------------------------------------------------------
DayOrchestrator needs session_end_idx to be a fixed, correct bar index
at construction time -- it's how it knows to stop spawning new raids
after the Kill Zone closes. That number literally cannot be known until
bars reaching 10am actually exist. Deciding earlier would hand it a
wrong, "still growing" value. The cost: raid/MSS/FVG activity between
7am-10am is journaled a few hours late (in the step-3 backfill), not
live minute-by-minute. Every event still carries its correct original
timestamp, so the journal itself is accurate -- only the *time it
appears in the database* lags. Harmless for Phase 3 (no real orders);
revisit if Phase 4's timing requirements ever need it tighter.
"""
import logging
import time as time_module
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from phase1.streaming.day_orchestrator import DayOrchestrator
from phase1.streaming.day_selection_gate import DaySelectionGate
from app.core.healthchecks import HeartbeatPinger
from app.core.telegram import alert_for_event, send_telegram_alert
from shadow_runner.bridge_client import BridgeClient, BridgeError
from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.day_state import CurrentDay
from shadow_runner.order_manager import OrderManager
from shadow_runner.position_tracker import PositionTracker
from shadow_runner.persistence import (
    event_type_exists,
    get_active_subscribers,
    get_current_equity,
    get_last_event_timestamp,
    get_last_event_timestamp_for_date,
    get_recent_swing_history,
    link_events_to_trade,
    write_event,
    write_trade,
)
from shadow_runner.orphan_recovery import check_for_orphaned_positions

log = logging.getLogger("shadow_runner.runner")

NY_TZ = ZoneInfo("America/New_York")
BAR_DURATION = timedelta(minutes=5)  # M5 bars, matches config.symbol's timeframe

# Multi-user fan-out, piece 2 (MULTI_USER_FANOUT_PLAN.md section 5): the
# model's own simulated day (the "ownerless" shadow Trade row, written
# regardless of how many real subscribers exist -- see _write_trade())
# tracks a purely notional equity curve, decoupled from any real
# account's balance or risk setting. 0.01 matches the pre-fan-out
# fallback this replaces (the old "no model_config loaded" case);
# 10000.0 is an arbitrary round starting balance, deliberately NOT
# derived from the reference bridge's real account (same "no coupling
# to that account's identity" principle already used for the reference
# price feed itself).
SHADOW_NOTIONAL_RISK_PCT = 0.01
SHADOW_NOTIONAL_STARTING_EQUITY = 10000.0


class ShadowRunner:
    def __init__(
        self, config: ShadowRunnerConfig, bridge: BridgeClient, session_factory, gate=None,
        bridge_factory=BridgeClient,
    ):
        self.config = config
        self.bridge = bridge  # the REFERENCE bridge -- detection's price feed only,
                                # see MULTI_USER_FANOUT_PLAN.md's "reference price feed"
                                # decision. Never used for a subscriber's own orders.
        self.session_factory = session_factory  # e.g. app.core.database.SessionLocal
        self.gate = gate or DaySelectionGate()
        self.current_day: CurrentDay | None = None
        self.last_processed_bar_time_ny: datetime | None = None
        # Multi-user fan-out, piece 2: constructs a per-subscriber
        # BridgeClient from their own bridge_url (get_active_subscribers()'s
        # result) -- overridable in tests to return a fake regardless of
        # URL, same dependency-injection shape as `bridge`/`session_factory`/
        # `gate` above. Defaults to the real BridgeClient class itself.
        self.bridge_factory = bridge_factory
        # Multi-user fan-out, piece 2: one PositionTracker per subscriber,
        # keyed by user_id -- NOT day-scoped like CurrentDay.order_managers
        # (a real position can outlive the day it opened on, see
        # position_tracker.py's module docstring), so this dict only ever
        # GROWS (see _ensure_position_tracker()): built for every current
        # subscriber once at startup (_load_initial_position_trackers(),
        # so a restart doesn't lose track of an already-open multi-day
        # position -- matches the old single-user behavior exactly), then
        # topped up for any newly-added subscriber at each _decide_day()
        # call so they don't need a full runner restart to get overnight
        # risk coverage. An entry is never removed just because a
        # subscriber later unsubscribes -- any position it's still
        # tracking keeps being managed to natural resolution regardless
        # (see MULTI_USER_FANOUT_PLAN.md's "Open questions, resolved" #2).
        self.position_trackers: dict = {}
        # Logging/audit review, part 3 (monitoring/alerting): proves the
        # main loop itself is alive and cycling, not just that the process
        # exists -- see app.core.healthchecks' module docstring for why
        # this pings an external service rather than something on the
        # same VPS. Dormant (no-ops) until HEALTHCHECKS_PING_URL is set.
        self.heartbeat = HeartbeatPinger()

    def _ensure_position_tracker(self, subscriber: dict) -> None:
        """Multi-user fan-out, piece 2. Constructs + load_from_db()'s a
        PositionTracker for this subscriber if one doesn't already exist
        -- a no-op otherwise, so calling this repeatedly (every
        _decide_day()) never disturbs an existing entry's in-flight
        multi-day tracking state. `subscriber` is one row from
        get_active_subscribers()."""
        user_id = subscriber["user_id"]
        if user_id in self.position_trackers:
            return
        model_config = {
            "model_name": self.config.model, "status": "active",
            "risk_pct": subscriber["risk_pct"], "magic_number": subscriber["magic_number"],
        }
        bridge = self.bridge_factory(subscriber["bridge_url"])
        pt = PositionTracker(bridge, self.session_factory, user_id, model_config)
        pt.load_from_db()
        self.position_trackers[user_id] = pt

    def _load_initial_position_trackers(self) -> None:
        """Multi-user fan-out, piece 2. Call once, before run_forever() --
        replaces the old single-user _load_model_config()/PositionTracker
        construction. Queries today's subscribers once and builds a
        PositionTracker for each, so a runner restart doesn't lose track
        of anyone's already-open multi-day position (same immediacy the
        old single-user startup path already had) -- _decide_day() then
        keeps this dict current for any subscriber added later, without
        needing another restart."""
        db = self.session_factory()
        try:
            subscribers = get_active_subscribers(db, self.config.model)
        finally:
            db.close()
        for subscriber in subscribers:
            self._ensure_position_tracker(subscriber)
        log.info(
            "Loaded %d initial subscriber(s) for model=%s", len(subscribers), self.config.model,
        )

    @staticmethod
    def _make_tagging_sink(append_to: list, user_id):
        """Multi-user fan-out, piece 2. Every event a specific
        subscriber's OrderManager/orphan-check emits needs to carry WHICH
        subscriber it belongs to, since all subscribers' events flow
        through the same shared list (cd.todays_events, or the orphan-
        recovery collector) before _write_events_now() persists them.
        Tags with `_origin_user_id`, which _write_events_now() pops back
        off before the row is ever written -- never reaches `details`.
        Narrative events never get this tag (nothing reads it for them --
        write_event() nulls user_id for NARRATIVE_EVENT_TYPES regardless
        of what's passed, see app/models/event.py)."""
        def sink(e: dict) -> None:
            tagged = dict(e)
            tagged["_origin_user_id"] = user_id
            append_to.append(tagged)
        return sink

    # ---------- polling ----------

    def poll_once(self) -> None:
        now_ny = datetime.now(NY_TZ).replace(tzinfo=None)
        candles = self.bridge.get_candles(
            self.config.symbol, "M5", self.config.candles_fetch_count
        )
        new_bars = self._filter_new_closed_bars(candles, now_ny)
        for bar in new_bars:
            self._process_bar(bar)
            self.last_processed_bar_time_ny = bar["time_ny"]

        self._check_order_manager_fills()
        self._check_order_manager_close()
        self._check_daily_loss_threshold()
        for user_id, pt in self.position_trackers.items():
            try:
                pt.check_positions()
            except Exception:
                log.exception(
                    "PositionTracker.check_positions() failed for user_id=%s -- "
                    "continuing to the next subscriber", user_id,
                )
            # Continuous orphan-check (added 2026-09-04 after a real
            # incident -- see PositionTracker.check_for_orphans()'s own
            # docstring): self-throttles to ORPHAN_CHECK_INTERVAL, so
            # this call is cheap on every poll it doesn't actually act on.
            try:
                pt.check_for_orphans(self.config.symbol)
            except Exception:
                log.exception(
                    "PositionTracker.check_for_orphans() failed for user_id=%s -- "
                    "continuing to the next subscriber", user_id,
                )

    def _check_daily_loss_threshold(self) -> None:
        """Phase 4 step 4 Part 2. Visibility only -- see
        OrderManager.check_daily_loss_threshold()'s own docstring for
        the full reasoning (does not block trades, does not force-close
        anything). Multi-user fan-out, piece 2: loops over every
        subscriber's own OrderManager, each isolated in its own
        try/except -- one subscriber's failure here must never block the
        loss-visibility check for anyone else."""
        cd = self.current_day
        if cd is None or not cd.order_managers:
            return
        for user_id, om in cd.order_managers.items():
            try:
                om.check_daily_loss_threshold()
            except Exception:
                log.exception(
                    "check_daily_loss_threshold() failed for user_id=%s -- "
                    "continuing to the next subscriber", user_id,
                )
        self._flush_new_events(cd)

    def _check_order_manager_close(self) -> None:
        """Phase 4 step 3. Checked once per poll cycle, same cadence and
        reasoning as _check_order_manager_fills() -- real broker state
        (whether the winning position has closed) can change between
        bar closes, not just at them. OrderManager itself already
        journals the real_trade_closed event via its own event_sink --
        no additional action needed here beyond calling it. Multi-user
        fan-out, piece 2: same per-subscriber isolation as the loss
        check above."""
        cd = self.current_day
        if cd is None or not cd.order_managers:
            return
        for user_id, om in cd.order_managers.items():
            try:
                om.check_for_close()
            except Exception:
                log.exception(
                    "check_for_close() failed for user_id=%s -- continuing to the "
                    "next subscriber", user_id,
                )
        self._flush_new_events(cd)

    def _check_order_manager_fills(self) -> None:
        """Checked once per poll cycle, independent of whether any new
        bars arrived -- fills happen against REAL broker state, which
        can change between bar closes, not just at them.

        Multi-user fan-out, piece 2: loops over every subscriber's
        OrderManager (isolated try/except each), tracking WHICH ones
        actually got a fill THIS poll -- only those get attach_target()
        called, not every subscriber. The bars used for target
        computation are fetched ONCE from the shared REFERENCE bridge
        (self.bridge), reused for every subscriber who filled this poll
        -- same "detection's price feed is shared" principle already
        applied to the narrative itself (see MULTI_USER_FANOUT_PLAN.md),
        extended here since target computation is fundamentally a
        price-action question, not an execution one; fetching each
        subscriber's own bars separately would mean N redundant bridge
        calls per poll for no meaningful precision gain.
        """
        cd = self.current_day
        if cd is None or not cd.order_managers:
            return
        newly_filled_user_ids = []
        for user_id, om in cd.order_managers.items():
            try:
                if om.check_for_fills():
                    newly_filled_user_ids.append(user_id)
            except Exception:
                log.exception(
                    "check_for_fills() failed for user_id=%s -- continuing to the "
                    "next subscriber", user_id,
                )
        self._flush_new_events(cd)  # flush regardless of outcome below --
                                      # placements/cancellations already
                                      # emitted must not wait for a bar
        if not newly_filled_user_ids:
            return
        try:
            candles = self.bridge.get_candles(self.config.symbol, "M5", 20)
        except BridgeError as e:
            log.error(
                "Fill detected but could not fetch bars to compute the target (%s) -- "
                "take_profit will remain unset until a future poll retries this.", e,
            )
            return
        # compute_target() needs bars STRICTLY BEFORE the fill (see its
        # docstring) -- a live fill can happen at any moment, not just a
        # bar close, so "before the fill" here means "the most recently
        # CLOSED bars right now." Filter out any still-forming trailing
        # bar the bridge may have included (same rule _filter_new_closed_bars
        # already applies elsewhere) before handing bars to attach_target.
        now_ny = datetime.now(NY_TZ).replace(tzinfo=None)
        closed_bars = [
            c for c in candles
            if (c["time_ny"].replace(tzinfo=None) if c["time_ny"].tzinfo else c["time_ny"]) + BAR_DURATION <= now_ny
        ]
        for user_id in newly_filled_user_ids:
            try:
                cd.order_managers[user_id].attach_target(closed_bars)
            except Exception:
                log.exception(
                    "attach_target() failed for user_id=%s -- continuing to the "
                    "next subscriber", user_id,
                )
        self._flush_new_events(cd)

    def _filter_new_closed_bars(self, candles: list[dict], now_ny: datetime) -> list[dict]:
        out = []
        for c in candles:
            bar_time = c["time_ny"].replace(tzinfo=None) if c["time_ny"].tzinfo else c["time_ny"]
            if bar_time + BAR_DURATION > now_ny:
                continue  # still forming -- see module docstring's Q2 discussion
            if self.last_processed_bar_time_ny is not None and bar_time <= self.last_processed_bar_time_ny:
                continue  # already processed
            normalized = dict(c)
            normalized["time_ny"] = bar_time
            out.append(normalized)
        out.sort(key=lambda b: b["time_ny"])
        return out

    def run_forever(self) -> None:
        log.info(
            "Shadow runner starting: symbol=%s model=%s poll_interval=%ds",
            self.config.symbol, self.config.model, self.config.poll_interval_seconds,
        )
        while True:
            try:
                self.poll_once()
            except BridgeError as e:
                log.warning("Bridge unavailable this poll, will retry: %s", e)
            except Exception:
                log.exception("Unexpected error in poll_once -- continuing to next poll")
            # Pinged every iteration regardless of poll_once()'s outcome --
            # a caught BridgeError still means this loop is alive and
            # cycling, which is the thing being proven here. An
            # uncaught/unexpected crash of run_forever() itself (the
            # actual "process is down" case) is the only way this ever
            # stops -- see HeartbeatPinger for why that's caught
            # externally, by healthchecks.io, not detected locally.
            self.heartbeat.maybe_ping()
            time_module.sleep(self.config.poll_interval_seconds)

    # ---------- per-bar processing ----------

    def _process_bar(self, bar: dict) -> None:
        bar_date = bar["time_ny"].date()

        if self.current_day is None:
            now_ny_date = datetime.now(NY_TZ).replace(tzinfo=None).date()
            if bar_date < now_ny_date:
                # Cold start when the most recent available bars belong
                # to a day that's already fully over (e.g. starting up
                # on a weekend, when the bridge's "most recent" bars are
                # just Friday's tail end -- a small fragment, not the
                # whole day). Trying to build a CurrentDay and judge that
                # fragment produces a misleading "insufficient_bars"
                # verdict on a day nobody was ever going to journal
                # properly anyway. Matches the documented limitation in
                # PHASE3_RESTART_RECOVERY.md: fully-missed/already-over
                # days are never reconstructed, only today's in-progress
                # one. Just wait for bars that are genuinely dated today
                # (or later, once the market reopens).
                log.info(
                    "Ignoring stale bar at %s (date %s, before today %s) -- "
                    "waiting for genuinely current data",
                    bar["time_ny"], bar_date, now_ny_date,
                )
                return
            self.current_day = CurrentDay(bar_date)
        elif bar_date != self.current_day.date:
            self._finalize_day(self.current_day)
            self.current_day = CurrentDay(bar_date)

        cd = self.current_day
        cd.update_daily_range(bar)

        if not cd.is_session_bar(bar):
            return  # outside 5am-5pm NY -- only relevant for the daily
                     # high/low just updated above

        cd.bars.append(bar)

        if not cd.decided:
            if cd.ready_to_decide():
                self._decide_day(cd)
            return  # this bar was either just backfilled (if decided
                     # tradeable) or belongs to a now-skipped day --
                     # either way, nothing more to do with it here

        if cd.tradeable:
            idx = len(cd.bars) - 1
            cd.orchestrator.on_new_bar(bar["time_ny"], idx, bar["high"], bar["low"], bar["close"])
            self._flush_new_events(cd)

    def _decide_day(self, cd: CurrentDay, historical: bool = False) -> None:
        """historical=True is the cross-day recovery gap fix (2026-09-02,
        see PENDING_ITEMS.md's "Real bugs found 2026-09-02"): reconstructs
        a fully-missed PAST day's raid/MSS/FVG/candidate narrative for
        the journal, without ever placing a real order for it. NO
        OrderManagers get constructed at all in this mode -- combined_sink's
        `cd.order_managers` loop below stays empty by construction, the
        same guard that already existed for every other day (just widened
        from a single object to a dict, see MULTI_USER_FANOUT_PLAN.md
        piece 2); historical replay relies on that same guard structurally
        never being satisfied, rather than adding a new conditional that
        could be gotten wrong. See _replay_historical_day()."""
        result = self.gate.gate_for_day(cd.date, cd.bars)
        cd.decided = True
        cd.tradeable = result.tradeable

        if not result.tradeable:
            cd.skip_reason = result.skip_reason
            self._write_events_now(
                [{"event_type": f"day_skipped_{result.skip_reason}", "timestamp": cd.bars[-1]["time_ny"]}]
            )
            log.info("%s: skipped (%s)", cd.date, result.skip_reason)
            return

        cd.trend = result.trend
        cd.todays_events.append(
            {"event_type": "day_trend_determined", "timestamp": cd.bars[-1]["time_ny"], "trend": result.trend}
        )

        # Multi-user fan-out, piece 2: construct BEFORE DayOrchestrator,
        # same reasoning as the old single-OrderManager code -- combined
        # sink below references cd.order_managers, and DayOrchestrator's
        # constructor immediately backfills bars (which can emit
        # trade_candidate_ready synchronously). Queries subscribers fresh
        # every day (never cached -- see get_active_subscribers()'s own
        # docstring), so this is the ONE place the subscriber list gets
        # snapshotted for the day, matching how detection already only
        # decides once per day, not per-poll.
        if not historical:
            db = self.session_factory()
            try:
                subscribers = get_active_subscribers(db, self.config.model)
            finally:
                db.close()
            for subscriber in subscribers:
                self._ensure_position_tracker(subscriber)
                model_config = {
                    "model_name": self.config.model, "status": "active",
                    "risk_pct": subscriber["risk_pct"], "magic_number": subscriber["magic_number"],
                }
                bridge = self.bridge_factory(subscriber["bridge_url"])
                cd.order_managers[subscriber["user_id"]] = OrderManager(
                    model_config, self.config.symbol, bridge, self.session_factory,
                    subscriber["user_id"],
                    event_sink=self._make_tagging_sink(cd.todays_events, subscriber["user_id"]),
                )

        def combined_sink(e: dict) -> None:
            cd.todays_events.append(e)
            if e.get("event_type") != "trade_candidate_ready":
                return
            for user_id, om in cd.order_managers.items():
                try:
                    om.on_trade_candidate_ready(e)
                except Exception:
                    log.exception(
                        "on_trade_candidate_ready() failed for user_id=%s -- "
                        "continuing to the next subscriber", user_id,
                    )

        cd.orchestrator = DayOrchestrator(
            result.trend, result.session_start_idx, result.session_end_idx,
            event_sink=combined_sink,
        )
        for idx, b in enumerate(cd.bars):
            cd.orchestrator.on_new_bar(b["time_ny"], idx, b["high"], b["low"], b["close"])
        self._flush_new_events(cd)
        log.info(
            "%s: tradeable (trend=%s), backfilled %d bars, %d events so far",
            cd.date, result.trend, len(cd.bars), len(cd.todays_events),
        )

    def _flush_new_events(self, cd: CurrentDay) -> None:
        flushed_count = getattr(cd, "_flushed_count", 0)
        new_events = cd.todays_events[flushed_count:]
        if new_events:
            self._write_events_now(new_events)
        cd._flushed_count = len(cd.todays_events)

    def _replay_historical_day(self, date, session_bars: list[dict]) -> CurrentDay:
        """Cross-day recovery gap fix (2026-09-02) -- reconstructs one
        fully-missed PAST calendar day's raid/MSS/FVG/candidate journal,
        for a day that's already completely over by the time this runs.

        Deliberately does NOT reuse _process_bar()/self.current_day:
        _process_bar() has its own stale-bar guard (refuses to start a
        NEW CurrentDay for a bar dated before today -- see its own
        comment, added for a different bug: a small stale FRAGMENT of an
        already-finished day showing up on cold start) which would
        immediately reject every bar here. More importantly, threading a
        real historical day through self.current_day would corrupt the
        REAL in-progress state that ordinary live polling depends on.
        This operates entirely on a local, throwaway CurrentDay instead
        -- never touches self.current_day.

        Deliberately does NOT call _finalize_day() -- that would write a
        `trades` row, but this only ever reconstructs the SIMULATED
        detection narrative, never confirmed against what the broker
        actually did that day (see check_for_orphaned_positions() for
        the real-position half of cross-day recovery, and the plan
        doc's explicit note that the two are not cross-referenced this
        pass). The events written here (via _decide_day's own internal
        flushing) are exactly what a trader would see in that day's
        story -- day-skip, or raid/MSS/FVG/candidate -- nothing more.
        """
        cd = CurrentDay(date)
        for bar in session_bars:
            cd.update_daily_range(bar)
            if not cd.is_session_bar(bar):
                continue
            cd.bars.append(bar)
            if not cd.decided:
                if cd.ready_to_decide():
                    self._decide_day(cd, historical=True)
                continue
            if cd.tradeable:
                idx = len(cd.bars) - 1
                cd.orchestrator.on_new_bar(bar["time_ny"], idx, bar["high"], bar["low"], bar["close"])
                self._flush_new_events(cd)
        return cd

    def _write_events_now(self, events: list[dict]) -> None:
        """Multi-user fan-out, piece 2: each event dict may carry an
        `_origin_user_id` tag (see _make_tagging_sink()) identifying
        WHICH subscriber it belongs to -- popped off here and used as
        the real user_id passed to write_event(), instead of the old
        single hardcoded self.config.user_id. A narrative event has no
        tag (defaults to None), which is correct either way: write_event()
        nulls user_id for NARRATIVE_EVENT_TYPES regardless of what's
        passed (see app/models/event.py)."""
        db = self.session_factory()
        try:
            for e in events:
                write_payload = dict(e)
                origin_user_id = write_payload.pop("_origin_user_id", None)
                row = write_event(db, write_payload, origin_user_id, self.config.model)
                # Logging/audit review, part 3: stash the real DB event_id
                # back onto the source dict (write_event() itself works off
                # a copy, so this never mutates its own input) -- lets
                # _write_trade() later link a trade to the exact events
                # that made it, once the trade row exists, without
                # re-deriving the match from scratch.
                e["_event_id"] = row.event_id
            db.commit()
        finally:
            db.close()

        # Monitoring/alerting (logging/audit review part 3): fires only
        # AFTER the commit above succeeds -- an alert about something
        # that failed to even get journaled would be worse than no alert
        # (nothing to look at when someone checks). alert_for_event()
        # itself no-ops safely for any event type it doesn't recognize
        # (and send_telegram_alert() beneath it no-ops if unconfigured),
        # so this is safe to leave unconditional here. order_skipped_paused
        # is deliberately NOT alerted on -- that's an intentional, expected
        # skip (the model is paused), not a failure.
        #
        # 2026-09-04: which event types alert, and how the message
        # reads, now lives in ONE place (app.core.telegram.alert_for_event())
        # instead of being duplicated inline here -- see that function's
        # own docstring for why (this was the only alerting call site
        # for a while, which is exactly how PositionTracker's own
        # writes ended up silently bypassing it entirely).
        #
        # Multi-user fan-out, piece 2: identifies the real subscriber via
        # `_origin_user_id` (always present for every currently-alerted
        # event type -- all are REAL_ACTION_EVENT_TYPES, always emitted
        # by a specific subscriber's OrderManager). self.config.user_id
        # is kept only as a harmless fallback for the case that tag is
        # somehow missing -- should never actually happen for these
        # types, but a fallback costs nothing and keeps the alert
        # readable either way.
        for e in events:
            alert_for_event(e, e.get("_origin_user_id", self.config.user_id), self.config.model)

    # ---------- Phase 3 step 6: restart recovery ----------

    BOOTSTRAP_FETCH_COUNT = 5000  # bridge's documented max per /candles call

    def _bootstrap_trend_history_if_needed(self) -> None:
        """
        Phase 3 step 7. Runs at most ONCE, ever, per (user, model) --
        see the three-way check below. Fetches up to BOOTSTRAP_FETCH_COUNT
        M5 bars (the bridge's max per call, ~17 trading days), aggregates
        them into NY-calendar-day highs/lows ourselves (deliberately NOT
        using MT5's D1 candle directly -- that's bucketed by broker
        server time (UTC), not NY midnight-to-midnight, which would
        silently compute the wrong daily high/low; see this phase's
        earlier Q&A on daily-range timing), and feeds the result through
        DaySelectionGate.on_day_closed() in chronological order.
        """
        db = self.session_factory()
        try:
            already_bootstrapped = event_type_exists(
                db, self.config.model, "trend_history_bootstrapped"
            )
            if already_bootstrapped:
                log.info("Bootstrap: already done previously, skipping")
                return

            has_real_swing_history = event_type_exists(
                db, self.config.model, "daily_swing_high_confirmed"
            ) or event_type_exists(
                db, self.config.model, "daily_swing_low_confirmed"
            )
        finally:
            db.close()

        if has_real_swing_history:
            # This system has real accumulated history already (e.g.
            # bootstrap code deployed after weeks of normal operation) --
            # do NOT inject historical data on top of it (would create
            # duplicate/overlapping calendar days). Just mark it done so
            # this check is skipped instantly on all future restarts.
            log.info(
                "Bootstrap: real swing history already exists (this system has been "
                "running before this feature was added) -- marking done without "
                "injecting anything, to avoid duplicating real data."
            )
            self._write_events_now(
                [{"event_type": "trend_history_bootstrapped", "timestamp": datetime.now(NY_TZ).replace(tzinfo=None), "days_seeded": 0, "note": "skipped, real history already present"}]
            )
            return

        try:
            candles = self.bridge.get_candles(self.config.symbol, "M5", self.BOOTSTRAP_FETCH_COUNT)
        except BridgeError as e:
            log.error(
                "Bootstrap: bridge unavailable, could not fetch historical data (%s). "
                "Trend detection will start cold and take ~9 real trading days to warm "
                "up naturally. Will retry bootstrap on next restart (marker not written).",
                e,
            )
            return

        daily_ranges: dict = {}
        for c in candles:
            d = c["time_ny"].date()
            if d not in daily_ranges:
                daily_ranges[d] = {"high": c["high"], "low": c["low"]}
            else:
                daily_ranges[d]["high"] = max(daily_ranges[d]["high"], c["high"])
                daily_ranges[d]["low"] = min(daily_ranges[d]["low"], c["low"])

        today = datetime.now(NY_TZ).replace(tzinfo=None).date()
        sorted_days = sorted(d for d in daily_ranges if d < today)  # exclude today -- not closed yet

        all_events = []
        for d in sorted_days:
            events = self.gate.on_day_closed(d, daily_ranges[d]["high"], daily_ranges[d]["low"])
            all_events.extend(events)

        if all_events:
            self._write_events_now(all_events)

        self._write_events_now(
            [
                {
                    "event_type": "trend_history_bootstrapped",
                    "timestamp": datetime.now(NY_TZ).replace(tzinfo=None),
                    "days_seeded": len(sorted_days),
                    "swing_events_confirmed": len(all_events),
                }
            ]
        )
        log.info(
            "Bootstrap: fed %d historical days, confirmed %d swing events",
            len(sorted_days), len(all_events),
        )

    def recover_on_startup(self) -> None:
        """
        Call once, before run_forever(). Three things happen here, in
        order -- see PHASE3_RESTART_RECOVERY.md for the full writeup.

        0. Cold-start trend bootstrap (step 7 addition): if this is
           truly the first time this (user, model) has ever run, seed
           DaySelectionGate with ~17 trading days of REAL historical
           swing data instead of starting from zero and waiting ~9 real
           trading days to accumulate enough on its own. Idempotent --
           writes a one-time marker event so this never re-runs and
           re-injects duplicate data on a future restart.
        1. Trend history: restore from whatever's in the DB now
           (includes anything step 0 just wrote, if it ran).
        2. Today's in-progress session: replay if safe, skip with a
           logged gap if not -- see docstring further down.
        """
        self._load_initial_position_trackers()
        self._bootstrap_trend_history_if_needed()

        db = self.session_factory()
        try:
            highs, lows = get_recent_swing_history(db, self.config.model)
        finally:
            db.close()
        self.gate.seed_trend_history(highs, lows)
        log.info(
            "Recovery: seeded trend history (%d confirmed highs, %d confirmed lows) from prior events",
            len(highs), len(lows),
        )

        now_ny = datetime.now(NY_TZ).replace(tzinfo=None)
        today = now_ny.date()

        # Cross-day recovery gap fix (2026-09-02) -- see PENDING_ITEMS.md's
        # "Real bugs found 2026-09-02" and PHASE3_VALIDATION.md's
        # correction section for the incident this exists to catch.
        # Checked BEFORE the existing "today" logic below, which is
        # unchanged -- this only concerns days strictly before today.
        db = self.session_factory()
        try:
            last_overall_ts = get_last_event_timestamp(db, self.config.model)
        finally:
            db.close()
        if last_overall_ts is not None and last_overall_ts.date() < today:
            self._recover_cross_day_gap(last_overall_ts.date(), today, now_ny)

        db = self.session_factory()
        try:
            last_ts = get_last_event_timestamp_for_date(db, self.config.model, today)
        finally:
            db.close()

        if last_ts is not None:
            log.warning(
                "Recovery: %s already has journaled events up to %s -- a prior run "
                "must have stopped partway through today. NOT replaying (would "
                "duplicate everything before that point). Everything between %s "
                "and now (%s) will be a documented gap in today's journal -- see "
                "PHASE3_RESTART_RECOVERY.md. Resuming normal live polling from here.",
                today, last_ts, last_ts, now_ny,
            )
            return

        log.info("Recovery: no events journaled yet for %s -- replaying from 5am NY through now", today)
        try:
            candles = self.bridge.get_candles(self.config.symbol, "M5", 200)
        except BridgeError as e:
            log.error(
                "Recovery: bridge unavailable, could not replay today's bars (%s). "
                "Starting fresh from the next live poll instead -- today's morning "
                "activity before now may be missed.", e,
            )
            return

        replay_bars = self._filter_new_closed_bars(candles, now_ny)
        replay_bars = [b for b in replay_bars if b["time_ny"].date() == today]
        log.info("Recovery: replaying %d bars for %s", len(replay_bars), today)
        for bar in replay_bars:
            self._process_bar(bar)
            self.last_processed_bar_time_ny = bar["time_ny"]

    def _recover_cross_day_gap(self, last_known_date, today, now_ny) -> None:
        """Cross-day recovery gap fix (2026-09-02) -- see
        recover_on_startup()'s call site for when this runs. Three
        things, in order: alert immediately (cheapest, most time-
        sensitive), check for a real orphaned open position (piece 2A,
        shadow_runner/orphan_recovery.py), then reconstruct the
        detection narrative for each missed day (piece 3,
        _replay_historical_day()) -- see the plan doc
        (misty-seeking-crescent.md in this session's history) for the
        full design and why each piece is scoped the way it is.

        Multi-user fan-out, piece 2: the orphan check now runs once PER
        SUBSCRIBER (self.position_trackers, already built at startup --
        reused rather than a fresh get_active_subscribers() query, since
        each PositionTracker already carries exactly what's needed: its
        own bridge, magic_number, user_id) -- a gap can leave ANY
        subscriber's account with an unmanaged real position, not just
        one. Each isolated in its own try/except, same reasoning as
        every other per-subscriber loop in this file.
        """
        missed_dates = []
        d = last_known_date + timedelta(days=1)
        while d < today:
            missed_dates.append(d)
            d += timedelta(days=1)
        if not missed_dates:
            # last_known_date was literally yesterday and today just
            # hasn't started yet -- nothing actually missed in between.
            return

        missed_dates_str = ", ".join(d.isoformat() for d in missed_dates)
        log.warning(
            "Recovery: cross-day gap detected -- last journaled activity was %s, "
            "today is %s. %d calendar day(s) in between may have been missed: %s",
            last_known_date, today, len(missed_dates), missed_dates_str,
        )
        send_telegram_alert(
            f"⚠️ shadow_runner restarted after a gap. Last journaled activity: "
            f"{last_known_date.isoformat()}. Today: {today.isoformat()}. "
            f"{len(missed_dates)} calendar day(s) may have been missed: {missed_dates_str}. "
            f"Checking for orphaned positions and reconstructing the journal now."
        )

        if self.position_trackers:
            collected_events = []
            db = self.session_factory()
            try:
                for user_id, pt in self.position_trackers.items():
                    try:
                        orphan_results = check_for_orphaned_positions(
                            pt.bridge, self.config.symbol, pt.model_config["magic_number"],
                            db, user_id, self.config.model, now_ny,
                            event_sink=self._make_tagging_sink(collected_events, user_id),
                            risk_pct=pt.model_config["risk_pct"],
                        )
                        if orphan_results:
                            log.warning(
                                "Recovery: orphan check for user_id=%s found %d position(s): %s",
                                user_id, len(orphan_results), orphan_results,
                            )
                            # 2026-09-04 fix: hand off every orphan that got
                            # a real trade record to THIS subscriber's own
                            # tracker, same reasoning as
                            # PositionTracker.check_for_orphans().
                            for r in orphan_results:
                                if r["trade_id"] is not None:
                                    pt.register_new_position(r["ticket"], r["trade_id"], r["entry_time_ny"])
                    except Exception:
                        log.exception(
                            "Orphan check failed for user_id=%s -- continuing to the "
                            "next subscriber", user_id,
                        )
            finally:
                db.close()
            if collected_events:
                self._write_events_now(collected_events)

        try:
            candles = self.bridge.get_candles(self.config.symbol, "M5", 5000)
        except BridgeError as e:
            log.error(
                "Recovery: bridge unavailable, could not replay missed days (%s) -- "
                "the journal for %s will stay an honest gap.", e, missed_dates,
            )
            return

        for missed_date in missed_dates:
            day_bars = [b for b in candles if b["time_ny"].date() == missed_date]
            if not day_bars:
                log.info(
                    "Recovery: no bars available for %s (weekend/holiday, or beyond "
                    "this fetch's %d-bar lookback reach) -- leaving this date's "
                    "journal as an honest gap rather than guessing.",
                    missed_date, len(candles),
                )
                continue
            log.info("Recovery: replaying %d bars for missed day %s", len(day_bars), missed_date)
            self._replay_historical_day(missed_date, day_bars)

    # ---------- day finalization ----------

    def _finalize_day(self, cd: CurrentDay) -> None:
        trade = None
        if cd.orchestrator is not None and cd.bars:
            last_bar = cd.bars[-1]
            trade = cd.orchestrator.finalize(last_bar["time_ny"], last_bar["close"])
            self._flush_new_events(cd)  # picks up the scratch-close event, if any

        if trade:
            self._write_trade(cd, trade)

        # Phase 4 step 2c: cancel anything each subscriber's order-manager
        # still has genuinely pending (never filled today) -- independent
        # of the simulation's own finalize() above, since it operates on
        # real broker state, not the simulated attempts. Multi-user
        # fan-out, piece 2: one subscriber's cancel failure must never
        # block cleanup for the others.
        for user_id, om in cd.order_managers.items():
            try:
                om.cancel_all_at_day_end()
            except Exception:
                log.exception(
                    "cancel_all_at_day_end() failed for user_id=%s -- continuing "
                    "to the next subscriber", user_id,
                )
        if cd.order_managers:
            self._flush_new_events(cd)

        # Daily swing detection runs on EVERY closed day, tradeable or
        # not -- matches the batch script's unconditional upfront swing
        # computation over the whole `daily` array.
        if cd.day_high is not None and cd.day_low is not None:
            swing_events = self.gate.on_day_closed(cd.date, cd.day_high, cd.day_low)
            if swing_events:
                self._write_events_now(swing_events)

    def _write_trade(self, cd: CurrentDay, trade: dict) -> None:
        """Multi-user fan-out, piece 2 (MULTI_USER_FANOUT_PLAN.md section
        5's Trade.user_id decision -- see "Open questions, resolved").
        Writes TWO kinds of row now, not one:

        1. The "shadow" row -- ALWAYS written, user_id=None, is_shadow=True,
           real_outcome=None. This is the model's own simulated outcome
           for the day, independent of how many (if any) real subscribers
           exist -- what shadow-mode model evaluation has always been,
           now genuinely ownerless instead of piggybacking on one
           hardcoded account. Uses a fixed notional risk_pct/starting
           equity (SHADOW_NOTIONAL_RISK_PCT/_STARTING_EQUITY above),
           deliberately decoupled from any real account's balance.
        2. One row PER SUBSCRIBER whose OrderManager actually has a real
           outcome today (most subscribers most days will have none --
           their candidate never filled) -- user_id=that subscriber,
           is_shadow=False (every cd.order_managers entry is, by
           construction, someone whose model status was 'active' today),
           real_outcome=their own OrderManager.get_real_outcome().

        The shared fill_event/close_event (found once below, from the
        narrative -- order_filled/trade_closed are always
        NARRATIVE_EVENT_TYPES) link to the SHADOW row's trade_id, not any
        subscriber's -- they describe the shared simulated narrative, not
        any one subscriber's real fill/close.
        """
        fill_event = next(
            (
                e for e in cd.todays_events
                if e.get("event_type") == "order_filled"
                and e["direction"] == trade["direction"]
                and abs(e["entry"] - trade["entry"]) < 1e-9
            ),
            None,
        )
        if fill_event is None:
            log.error(
                "%s: could not find matching order_filled event for winning trade %r -- "
                "using day's first bar as a fallback entry timestamp. This should not "
                "happen; investigate if it does.",
                cd.date, trade,
            )
            entry_bar = cd.bars[0]
        else:
            entry_bar = cd.bars[fill_event["fill_bar_index"]]

        if trade["outcome"] == "scratch":
            exit_bar = cd.bars[-1]
            # A scratch trade never has a trade_closed event to match --
            # see this function's docstring/callers. Bound explicitly so
            # the trade<->event linking below can rely on this always
            # existing, scratch or not.
            close_event = None
        else:
            close_event = next(
                (
                    e for e in reversed(cd.todays_events)
                    if e.get("event_type") == "trade_closed"
                    and e["outcome"] == trade["outcome"]
                    and abs(e["exit_price"] - trade["exit_price"]) < 1e-9
                ),
                None,
            )
            exit_bar = cd.find_bar_by_time_ny(close_event["timestamp"]) if close_event else cd.bars[-1]

        db = self.session_factory()
        try:
            try:
                shadow_equity_before = get_current_equity(
                    db, None, self.config.model,
                    bridge_starting_equity=SHADOW_NOTIONAL_STARTING_EQUITY,
                )
                shadow_row = write_trade(
                    db, trade,
                    entry_time_utc=entry_bar["time_utc"],
                    entry_time_ny=entry_bar["time_ny"],
                    exit_time_utc=exit_bar["time_utc"],
                    user_id=None,
                    model=self.config.model,
                    risk_pct=SHADOW_NOTIONAL_RISK_PCT,
                    equity_before=shadow_equity_before,
                    setup_context={"trend": cd.trend, "risk_pips": trade["risk_pips"]},
                    is_shadow=True,
                    real_outcome=None,
                )

                # Logging/audit review, part 3: persist the trade<->event
                # link this function already computed above
                # (fill_event/close_event) -- see
                # persistence.link_events_to_trade()'s docstring. Guarded
                # on non-empty so shadow_row.trade_id is only ever
                # touched when there's actually something to link (row
                # is a plain None in a couple of existing tests that
                # stub out write_trade() entirely).
                linked_event_ids = [
                    ev["_event_id"] for ev in (fill_event, close_event)
                    if ev is not None and ev.get("_event_id") is not None
                ]
                if linked_event_ids:
                    link_events_to_trade(db, linked_event_ids, shadow_row.trade_id)

                # 2026-09-04 fix: ONE commit for both the shadow row and
                # its event-linking, not two separate ones. Confirmed
                # empirically (see
                # test_multi_subscriber_write_trade.py's
                # ...duplicate_on_retry test) that two commits let a
                # failure IN link_events_to_trade() (after the shadow
                # row's own commit already succeeded) propagate
                # uncaught out of this function -- which, upstream in
                # _finalize_day()/_process_bar(), causes exactly this
                # same day to be retried on the next poll (self.current_day
                # never advances past a day whose finalize raised). A
                # retry would then call write_trade() AGAIN for the
                # shadow row, which was already committed the first
                # time -- a genuine DUPLICATE row for the same day,
                # nothing preventing it (no uniqueness constraint on
                # this table by design). One commit means a failure
                # anywhere in this block rolls back the WHOLE thing,
                # so a retry cleanly redoes both steps from a truly
                # clean slate instead of half-repeating them.
                db.commit()
                log.info(
                    "%s: shadow trade journaled -- %s %s, outcome=%s, entry=%.5f exit=%.5f",
                    cd.date, trade["direction"], self.config.model, trade["outcome"],
                    trade["entry"], trade["exit_price"],
                )
            except Exception as e:
                # This row is the prerequisite for the per-subscriber
                # loop below (real subscriber rows describe THEIR
                # response to this shared day -- meaningless without
                # it), so unlike that loop's own per-subscriber
                # isolation, this failure can't be "skip and continue"
                # -- re-raised so the existing retry-next-poll behavior
                # (via _finalize_day()'s uncaught-exception path,
                # unchanged by this fix) still applies. What's new here
                # is visibility: previously this only ever reached a
                # raw log.exception() at run_forever()'s outer catch-all
                # -- now also journaled as a real safety_check_failed
                # event (own try/except: if even journaling this
                # failure fails, don't let that suppress the original
                # exception either), AND alerted directly (see this
                # edit's own note further down for why "journaled" alone
                # doesn't already mean "alerted" here -- this write
                # doesn't go through _write_events_now(), the only place
                # that currently sends Telegram alerts).
                db.rollback()
                log.exception(
                    "%s: writing/linking the shadow trade failed -- this day will be "
                    "retried on the next poll", cd.date,
                )
                try:
                    write_event(
                        db,
                        {
                            "event_type": "safety_check_failed",
                            "timestamp": datetime.now(NY_TZ).replace(tzinfo=None),
                            "check_name": "write_shadow_trade_failed",
                            "error": str(e),
                        },
                        None, self.config.model,
                    )
                    db.commit()
                except Exception:
                    log.exception(
                        "%s: additionally failed to journal the above shadow-trade write failure",
                        cd.date,
                    )
                    db.rollback()
                else:
                    # 2026-09-04 write-path audit fix: this write goes
                    # through this function's own db.commit(), not
                    # _write_events_now() -- the only OTHER place that
                    # calls alert_for_event(). Without this, the failure
                    # was journaled (visible in /events) but never
                    # actually paged anyone, the same gap found and
                    # fixed in PositionTracker._emit_check_failure() the
                    # same pass. Only fires once the commit above
                    # actually succeeded (try/except/else), matching
                    # _write_events_now()'s own discipline.
                    alert_for_event(
                        {"event_type": "safety_check_failed", "check_name": "write_shadow_trade_failed", "error": str(e)},
                        None, self.config.model,
                    )
                raise

            for user_id, om in cd.order_managers.items():
                try:
                    real_outcome = om.get_real_outcome()
                    if real_outcome is None:
                        continue

                    sub_equity_before = get_current_equity(
                        db, user_id, self.config.model, bridge_starting_equity=None,
                    )
                    if sub_equity_before is None:  # no prior trade -- seed from THEIR real account
                        acct = om.bridge.account_info()
                        sub_equity_before = acct["balance"]

                    sub_row = write_trade(
                        db, trade,
                        entry_time_utc=entry_bar["time_utc"],
                        entry_time_ny=entry_bar["time_ny"],
                        exit_time_utc=exit_bar["time_utc"],
                        user_id=user_id,
                        model=self.config.model,
                        risk_pct=om.model_config["risk_pct"],
                        equity_before=sub_equity_before,
                        setup_context={"trend": cd.trend, "risk_pips": trade["risk_pips"]},
                        is_shadow=False,
                        real_outcome=real_outcome,
                    )
                    db.commit()
                    log.info(
                        "%s: real-outcome trade journaled for user_id=%s -- real_outcome=%s",
                        cd.date, user_id, real_outcome,
                    )

                    # Phase 4 overnight-position handling: if a real order
                    # filled but hadn't ALREADY closed by the time this
                    # trade row was written (real_status == 'open', not
                    # 'closed'), hand off ongoing tracking to THIS
                    # subscriber's own PositionTracker -- OrderManager and
                    # its CurrentDay are about to be discarded at the next
                    # bar's day rollover, but this position may still be
                    # open for days.
                    if real_outcome["close_price"] is None and user_id in self.position_trackers:
                        self.position_trackers[user_id].register_new_position(
                            real_outcome["position_ticket"], sub_row.trade_id, entry_bar["time_ny"],
                        )
                except Exception as e:
                    log.exception(
                        "Writing the real-outcome trade failed for user_id=%s -- "
                        "continuing to the next subscriber", user_id,
                    )
                    # 2026-09-04 fix: every subscriber in this loop shares
                    # ONE db session (see this function's docstring for
                    # why -- the shadow row + every subscriber's row all
                    # go through it). A failure AT commit() (a real
                    # constraint/FK violation, not just an exception from
                    # om.get_real_outcome() before any DB call) leaves
                    # that shared session in Postgres's aborted-
                    # transaction state -- confirmed empirically (see
                    # tests/shadow_runner/test_multi_subscriber_write_trade.py's
                    # ...at_commit_time_blocks_the_next test): every
                    # subsequent subscriber in this same loop would then
                    # ALSO silently fail to get a row, cascading from one
                    # bad write, with nothing distinguishing "this
                    # subscriber's own write failed" from "an earlier
                    # subscriber poisoned the session." Roll back before
                    # continuing so subsequent subscribers get a clean
                    # session to write into.
                    db.rollback()
                    # Also journal loudly, not just log.exception() to
                    # stdout -- this is a real trade write silently not
                    # happening, exactly the class of failure this
                    # session's other fixes exist to surface. Own
                    # try/except: if even journaling THIS failure fails,
                    # don't let that crash the loop too -- the log.exception()
                    # above already made it visible in the container logs
                    # even in that worst case.
                    try:
                        write_event(
                            db,
                            {
                                "event_type": "safety_check_failed",
                                "timestamp": datetime.now(NY_TZ).replace(tzinfo=None),
                                "check_name": "write_real_outcome_trade_failed",
                                "error": str(e),
                            },
                            user_id, self.config.model,
                        )
                        db.commit()
                    except Exception:
                        log.exception(
                            "Additionally failed to journal the above write failure for user_id=%s",
                            user_id,
                        )
                        db.rollback()
                    else:
                        # 2026-09-04 write-path audit fix: same gap as
                        # write_shadow_trade_failed above -- this commit
                        # doesn't go through _write_events_now(), so
                        # without this the failure was journaled but
                        # never actually paged anyone. A REAL trade
                        # write silently not happening for a specific
                        # user is exactly the class of thing that should
                        # alert, not just sit in /events waiting to be
                        # noticed.
                        alert_for_event(
                            {"event_type": "safety_check_failed", "check_name": "write_real_outcome_trade_failed", "error": str(e)},
                            user_id, self.config.model,
                        )
        finally:
            db.close()