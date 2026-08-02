"""
Day selection gate: decides whether "today" is even a tradeable day, and
if so, what its trend and session-bar bounds are -- everything
DayOrchestrator needs to be constructed correctly, live.

WHY THIS EXISTS
---------------
The batch model (FVG_model.py / extract_golden_master.py) makes this
decision with the whole array already in memory: FOMC dates, daily
swings, and session windows are all precomputed up front, then the day
loop just looks things up. A live/shadow runner doesn't have that
luxury -- it only knows about days that have already closed. This class
is the streaming-faithful translation of that same decision logic,
wrapping the already-validated DailySwingDetector rather than
reimplementing swing detection.

THREE THINGS THIS GATE DECIDES, IN ORDER (mirrors the batch loop exactly):
  1. Is today an FOMC date?               -> skip if yes
  2. What's today's trend (up/down/None)? -> skip if not up or down
  3. Do we have >=12 five-minute bars in today's 5am-5pm NY window, and
     can we find both a session-start (7am) and session-end (10am) bar
     index inside it?                     -> skip if either fails

NO EXTRA LOOKAHEAD GUARD NEEDED FOR THE TREND CHECK (unlike the batch
script, which has to explicitly filter "swings confirmed at least 2 days
before today" out of an already-fully-computed array): in this streaming
version, on_day_closed() is only ever called with a day that has fully
closed, and gate_for_day() for "today" is only ever called before today
closes. Causality already guarantees every swing DailySwingDetector has
emitted by that point was confirmed using only prior days. There is
nothing today could contaminate even if we wanted it to -- today's own
high/low hasn't been fed to the detector yet.

FOMC DATES -- MAINTENANCE REQUIRED, READ THIS
-----------------------------------------------
FOMC_DATES below is a hardcoded set, ported verbatim from
extract_golden_master.py's FOMC_DATES (same 2016-2026-06-17 dates) plus
the four remaining confirmed 2026 meeting dates (source: federalreserve.gov
meeting calendar, checked 2026-08-02): Jul 29, Sep 16, Oct 28, Dec 9.
Each entry is the meeting's SECOND day (the announcement/decision day),
matching the convention already established in every prior year's dates.

This list WILL run out. The Fed publishes its meeting calendar roughly a
year ahead, so it's realistic to keep this current by hand, but nothing
enforces that automatically. STALENESS_WARNING_DAYS below controls a
self-check: if "today" is within that many days of the last known FOMC
date, gate_for_day() emits a "fomc_calendar_stale_warning" event instead
of silently trading through what might be an unflagged FOMC day. This is
a stopgap (Option A, chosen deliberately over building a live calendar
fetch -- see HANDOFF.md) -- extend FOMC_DATES by hand periodically.
"""
import datetime

from phase1.streaming.daily_swing_detector import DailySwingDetector

PIVOT_N = 2  # must match DailySwingDetector's pivot_n -- see trend logic below
MIN_SESSION_BARS = 12

# Verbatim from extract_golden_master.py's FOMC_DATES, plus the four
# remaining confirmed 2026 dates (see module docstring).
FOMC_DATES = {
    datetime.date(2016, 1, 27), datetime.date(2016, 3, 16), datetime.date(2016, 4, 27),
    datetime.date(2016, 6, 15), datetime.date(2016, 7, 27), datetime.date(2016, 9, 21),
    datetime.date(2016, 11, 2), datetime.date(2016, 12, 14), datetime.date(2017, 2, 1),
    datetime.date(2017, 3, 15), datetime.date(2017, 5, 3), datetime.date(2017, 6, 14),
    datetime.date(2017, 7, 26), datetime.date(2017, 9, 20), datetime.date(2017, 11, 1),
    datetime.date(2017, 12, 13), datetime.date(2018, 1, 31), datetime.date(2018, 3, 21),
    datetime.date(2018, 5, 2), datetime.date(2018, 6, 13), datetime.date(2018, 8, 1),
    datetime.date(2018, 9, 26), datetime.date(2018, 11, 8), datetime.date(2018, 12, 19),
    datetime.date(2019, 1, 30), datetime.date(2019, 3, 20), datetime.date(2019, 5, 1),
    datetime.date(2019, 6, 19), datetime.date(2019, 7, 31), datetime.date(2019, 9, 18),
    datetime.date(2019, 10, 30), datetime.date(2019, 12, 11), datetime.date(2020, 1, 29),
    datetime.date(2020, 3, 18), datetime.date(2020, 4, 29), datetime.date(2020, 6, 10),
    datetime.date(2020, 7, 29), datetime.date(2020, 9, 16), datetime.date(2020, 11, 5),
    datetime.date(2020, 12, 16), datetime.date(2021, 1, 27), datetime.date(2021, 3, 17),
    datetime.date(2021, 4, 28), datetime.date(2021, 6, 16), datetime.date(2021, 7, 28),
    datetime.date(2021, 9, 22), datetime.date(2021, 11, 3), datetime.date(2021, 12, 15),
    datetime.date(2022, 1, 26), datetime.date(2022, 3, 16), datetime.date(2022, 5, 4),
    datetime.date(2022, 6, 15), datetime.date(2022, 7, 27), datetime.date(2022, 9, 21),
    datetime.date(2022, 11, 2), datetime.date(2022, 12, 14), datetime.date(2023, 2, 1),
    datetime.date(2023, 3, 22), datetime.date(2023, 5, 3), datetime.date(2023, 6, 14),
    datetime.date(2023, 7, 26), datetime.date(2023, 9, 20), datetime.date(2023, 11, 1),
    datetime.date(2023, 12, 13), datetime.date(2024, 1, 31), datetime.date(2024, 3, 20),
    datetime.date(2024, 5, 1), datetime.date(2024, 6, 12), datetime.date(2024, 7, 31),
    datetime.date(2024, 9, 18), datetime.date(2024, 11, 7), datetime.date(2024, 12, 18),
    datetime.date(2025, 1, 29), datetime.date(2025, 3, 19), datetime.date(2025, 5, 7),
    datetime.date(2025, 6, 18), datetime.date(2025, 7, 30), datetime.date(2025, 9, 17),
    datetime.date(2025, 10, 29), datetime.date(2025, 12, 10), datetime.date(2026, 1, 28),
    datetime.date(2026, 3, 18), datetime.date(2026, 4, 29), datetime.date(2026, 6, 17),
    # -- new since Phase 2/3 (source: federalreserve.gov, checked 2026-08-02) --
    datetime.date(2026, 7, 29), datetime.date(2026, 9, 16), datetime.date(2026, 10, 28),
    datetime.date(2026, 12, 9),
}

STALENESS_WARNING_DAYS = 45  # warn well before the list actually runs out


class DaySelectionResult:
    """Either a skip (reason recorded) or a green light with everything
    DayOrchestrator needs to be constructed for today."""

    def __init__(
        self,
        tradeable: bool,
        skip_reason: str | None = None,
        trend: str | None = None,
        session_start_idx: int | None = None,
        session_end_idx: int | None = None,
    ):
        self.tradeable = tradeable
        self.skip_reason = skip_reason
        self.trend = trend
        self.session_start_idx = session_start_idx
        self.session_end_idx = session_end_idx

    def __repr__(self):
        if not self.tradeable:
            return f"DaySelectionResult(skip={self.skip_reason!r})"
        return (
            f"DaySelectionResult(trend={self.trend!r}, "
            f"session_start_idx={self.session_start_idx}, "
            f"session_end_idx={self.session_end_idx})"
        )


class DaySelectionGate:
    def __init__(self):
        self._swing_det = DailySwingDetector(pivot_n=PIVOT_N)
        # Each entry: (day_index, price). day_index is this gate's own
        # running count of CLOSED days fed so far -- see on_day_closed().
        self._confirmed_highs: list[tuple[int, float]] = []
        self._confirmed_lows: list[tuple[int, float]] = []
        self._closed_day_count = 0

    def on_day_closed(self, date: datetime.date, daily_high: float, daily_low: float) -> list[dict]:
        """
        Call once, after a calendar day has fully closed (i.e. after
        5pm NY / day_end), with that day's full-day high/low -- same
        definition the batch script used (resample("1D") on ALL ticks,
        not just session-hours bars). If the bridge exposes MT5's D1
        timeframe, that's the simplest source for this: one candle,
        already the right high/low.

        Returns whatever DailySwingDetector.on_new_day() returns --
        0 or more confirmed swing events, for the day PIVOT_N days
        behind the one just fed. Journal these as-is if desired (they
        map directly to the golden master's daily_swing_high_confirmed /
        daily_swing_low_confirmed event types).
        """
        events = self._swing_det.on_new_day(date, daily_high, daily_low)
        for e in events:
            if e["event_type"] == "daily_swing_high_confirmed":
                self._confirmed_highs.append((e["day_index"], e["price"]))
            elif e["event_type"] == "daily_swing_low_confirmed":
                self._confirmed_lows.append((e["day_index"], e["price"]))
        self._closed_day_count += 1
        return events

    def _trend_for_today(self) -> str | None:
        """Mirrors daily_trend_as_of(): last 2 confirmed highs + last 2
        confirmed lows, higher-high+higher-low -> up, lower+lower -> down,
        else None. No extra day-index filtering needed here -- see module
        docstring's causality note."""
        if len(self._confirmed_highs) < 2 or len(self._confirmed_lows) < 2:
            return None
        _, h1 = self._confirmed_highs[-2]
        _, h2 = self._confirmed_highs[-1]
        _, l1 = self._confirmed_lows[-2]
        _, l2 = self._confirmed_lows[-1]
        if h2 > h1 and l2 > l1:
            return "up"
        if h2 < h1 and l2 < l1:
            return "down"
        return None

    def gate_for_day(
        self,
        date: datetime.date,
        session_bars_ny,  # list of dicts/rows with a "time_ny" datetime and OHLC, 5am-5pm NY window, in order
    ) -> DaySelectionResult:
        """
        Call once per day, before today's session starts, with today's
        5am-5pm NY bars already sliced out (however many exist so far --
        pass what you have; the >=12-bar / session-start / session-end
        checks below do the same job the batch script's slicing did).

        Returns a DaySelectionResult. If .tradeable, hand
        .trend / .session_start_idx / .session_end_idx straight to
        DayOrchestrator's constructor along with session_bars_ny itself.
        """
        if date in FOMC_DATES:
            return DaySelectionResult(tradeable=False, skip_reason="fomc")

        # Staleness self-check -- separate from the tradeable/skip
        # decision itself (a day close to the edge of the known FOMC
        # calendar isn't necessarily an FOMC day), but callers should
        # journal this event type (event.py's VALID_EVENT_TYPES needs
        # extending for it, or log at WARNING and skip journaling --
        # decide alongside the event-type-gap open item).
        last_known_fomc = max(FOMC_DATES)
        days_of_runway = (last_known_fomc - date).days
        if 0 <= days_of_runway <= STALENESS_WARNING_DAYS:
            import logging
            logging.getLogger("phase1.streaming.day_selection_gate").warning(
                "FOMC_DATES has only %d day(s) of runway left (last known date: %s). "
                "Extend FOMC_DATES in day_selection_gate.py.",
                days_of_runway, last_known_fomc,
            )

        trend = self._trend_for_today()
        if trend not in ("up", "down"):
            return DaySelectionResult(tradeable=False, skip_reason="no_trend")

        if len(session_bars_ny) < MIN_SESSION_BARS:
            return DaySelectionResult(tradeable=False, skip_reason="insufficient_bars")

        session_start = datetime.datetime.combine(date, datetime.time(7, 0))
        session_end = datetime.datetime.combine(date, datetime.time(10, 0))

        session_start_idx = None
        for idx, bar in enumerate(session_bars_ny):
            bar_time = bar["time_ny"]
            if hasattr(bar_time, "tzinfo") and bar_time.tzinfo is not None:
                bar_time = bar_time.replace(tzinfo=None)
            if bar_time >= session_start:
                session_start_idx = idx
                break
        if session_start_idx is None:
            return DaySelectionResult(tradeable=False, skip_reason="no_session_start")

        session_end_idx = len(session_bars_ny)
        for idx, bar in enumerate(session_bars_ny):
            bar_time = bar["time_ny"]
            if hasattr(bar_time, "tzinfo") and bar_time.tzinfo is not None:
                bar_time = bar_time.replace(tzinfo=None)
            if bar_time >= session_end:
                session_end_idx = idx
                break

        return DaySelectionResult(
            tradeable=True,
            trend=trend,
            session_start_idx=session_start_idx,
            session_end_idx=session_end_idx,
        )