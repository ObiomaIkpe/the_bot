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

from app.models import UserSettings
from phase1.streaming.day_orchestrator import DayOrchestrator
from phase1.streaming.day_selection_gate import DaySelectionGate
from shadow_runner.bridge_client import BridgeClient, BridgeError
from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.day_state import CurrentDay
from shadow_runner.persistence import get_current_equity, write_event, write_trade

log = logging.getLogger("shadow_runner.runner")

NY_TZ = ZoneInfo("America/New_York")
BAR_DURATION = timedelta(minutes=5)  # M5 bars, matches config.symbol's timeframe


class ShadowRunner:
    def __init__(self, config: ShadowRunnerConfig, bridge: BridgeClient, session_factory, gate=None):
        self.config = config
        self.bridge = bridge
        self.session_factory = session_factory  # e.g. app.core.database.SessionLocal
        self.gate = gate or DaySelectionGate()
        self.current_day: CurrentDay | None = None
        self.last_processed_bar_time_ny: datetime | None = None

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
            time_module.sleep(self.config.poll_interval_seconds)

    # ---------- per-bar processing ----------

    def _process_bar(self, bar: dict) -> None:
        bar_date = bar["time_ny"].date()

        if self.current_day is None:
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

    def _decide_day(self, cd: CurrentDay) -> None:
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
        cd.orchestrator = DayOrchestrator(
            result.trend, result.session_start_idx, result.session_end_idx,
            event_sink=cd.todays_events.append,
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

    def _write_events_now(self, events: list[dict]) -> None:
        db = self.session_factory()
        try:
            for e in events:
                write_event(db, e, self.config.user_id, self.config.model)
            db.commit()
        finally:
            db.close()

    # ---------- day finalization ----------

    def _finalize_day(self, cd: CurrentDay) -> None:
        trade = None
        if cd.orchestrator is not None and cd.bars:
            last_bar = cd.bars[-1]
            trade = cd.orchestrator.finalize(last_bar["time_ny"], last_bar["close"])
            self._flush_new_events(cd)  # picks up the scratch-close event, if any

        if trade:
            self._write_trade(cd, trade)

        # Daily swing detection runs on EVERY closed day, tradeable or
        # not -- matches the batch script's unconditional upfront swing
        # computation over the whole `daily` array.
        if cd.day_high is not None and cd.day_low is not None:
            swing_events = self.gate.on_day_closed(cd.date, cd.day_high, cd.day_low)
            if swing_events:
                self._write_events_now(swing_events)

    def _write_trade(self, cd: CurrentDay, trade: dict) -> None:
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
            settings = (
                db.query(UserSettings).filter(UserSettings.user_id == self.config.user_id).one()
            )
            risk_pct = settings.risk_pct

            starting_equity_hint = None  # only fetched from the bridge if actually needed
            equity_before = get_current_equity(
                db, self.config.user_id, self.config.model,
                bridge_starting_equity=None,
            )
            if equity_before is None:  # no prior trade exists -- seed from the real account
                acct = self.bridge.account_info()
                starting_equity_hint = acct["balance"]
                equity_before = starting_equity_hint

            write_trade(
                db, trade,
                entry_time_utc=entry_bar["time_utc"],
                entry_time_ny=entry_bar["time_ny"],
                exit_time_utc=exit_bar["time_utc"],
                user_id=self.config.user_id,
                model=self.config.model,
                risk_pct=risk_pct,
                equity_before=equity_before,
                setup_context={"trend": cd.trend, "risk_pips": trade["risk_pips"]},
            )
            db.commit()
            log.info(
                "%s: trade journaled -- %s %s, outcome=%s, entry=%.5f exit=%.5f",
                cd.date, trade["direction"], self.config.model, trade["outcome"],
                trade["entry"], trade["exit_price"],
            )
        finally:
            db.close()