"""
Tracks everything about "today" while it's in progress: the 5am-5pm
session bars accumulated so far (for DaySelectionGate + DayOrchestrator),
the running full-calendar-day high/low (for tomorrow's trend calc via
DaySelectionGate.on_day_closed), and the raw event list needed to recover
a winning trade's entry/exit timestamps at finalize time (see
runner.py's finalize_day()).

One instance = one calendar day. Discarded and replaced at day rollover.
"""
import datetime


SESSION_WINDOW_START = datetime.time(5, 0)   # ref_start, matches batch
SESSION_WINDOW_END = datetime.time(17, 0)    # day_end, matches batch
DECISION_READY_TIME = datetime.time(10, 0)   # session_end -- see module
                                              # docstring in runner.py for
                                              # why we wait this long


class CurrentDay:
    def __init__(self, date: datetime.date):
        self.date = date
        self.bars: list[dict] = []       # 5am-5pm bars only, in order -- what
                                          # DaySelectionGate/DayOrchestrator see
        self.day_high: float | None = None
        self.day_low: float | None = None
        self.last_bar_seen: dict | None = None  # most recent bar of ANY hour,
                                                  # for day-rollover detection

        self.decided = False             # True once gate_for_day() has run
        self.tradeable = False
        self.trend: str | None = None
        self.skip_reason: str | None = None
        self.orchestrator = None         # DayOrchestrator, only if tradeable
        self.todays_events: list[dict] = []  # raw event dicts, for the
                                              # entry/exit-timestamp lookup
                                              # at finalize time

    def update_daily_range(self, bar: dict) -> None:
        """Call for EVERY bar of this NY calendar day, regardless of hour
        -- matches the batch script's df.resample("1D") on ALL ticks, not
        just the 5am-5pm session slice."""
        h, l = bar["high"], bar["low"]
        self.day_high = h if self.day_high is None else max(self.day_high, h)
        self.day_low = l if self.day_low is None else min(self.day_low, l)
        self.last_bar_seen = bar

    def is_session_bar(self, bar: dict) -> bool:
        t = bar["time_ny"].time()
        return SESSION_WINDOW_START <= t < SESSION_WINDOW_END

    def ready_to_decide(self) -> bool:
        """True once we've accumulated bars reaching at least 10am NY --
        the earliest point session_end_idx becomes a real, fixed value
        rather than 'wherever we happen to be right now'. See the
        session_end_idx design note in HANDOFF.md / this phase's chat
        history for why this can't be decided any earlier."""
        if self.decided or not self.bars:
            return False
        return self.bars[-1]["time_ny"].time() >= DECISION_READY_TIME

    def find_bar_by_time_ny(self, timestamp: datetime.datetime) -> dict | None:
        """Used at finalize time to recover a trade's exit bar (for
        exit_time_utc) from an event's echoed-back timestamp."""
        for b in self.bars:
            if b["time_ny"] == timestamp:
                return b
        return None