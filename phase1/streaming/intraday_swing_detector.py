"""
Streaming intraday (5-minute) swing detector.

Batch logic being reimplemented (from extract_golden_master.py /
FVG_model.py), which runs FRESH FOR EACH DAY -- unlike the daily swing
detector, which runs continuously across the entire multi-year history:

    SWING_N = 2
    for k in range(SWING_N, n - SWING_N):
        if highs[k] == max(highs[k-SWING_N : k+SWING_N+1]):
            piv_high_all.append(k)
        if lows[k] == min(lows[k-SWING_N : k+SWING_N+1]):
            piv_low_all.append(k)

Where `n` is the number of 5-min bars in that one day's window (the
batch code slices `full = df_5m.loc[ref_start:day_end]` -- 5:00 AM to
5:00 PM NY -- separately for every day, and `piv_high_all`/`piv_low_all`
are recomputed from scratch each time).

Same centered-window idea as DailySwingDetector (see
phase1/streaming/README.md for the full walkthrough of that concept --
this class is the same mechanism, deliberately NOT sharing code with it
yet, since DailySwingDetector is already proven correct and touching it
to extract a shared base risks re-breaking something that currently
works). The two real differences from the daily version:

  1. This resets completely at the start of every trading day --
     call start_new_day() before feeding the first bar of each day.
     Nothing about yesterday should influence today's confirmations.
  2. The unit is 5-minute bars, not days, so the same "2 before, 2
     after" window is a much shorter real-time delay (10 minutes
     either side, not 2 days).

This class does NOT know about session times (5 AM start, Kill Zone,
etc.) -- it only knows "bars fed to it since the last start_new_day()
call." Whatever feeds it is responsible for starting a new day at the
right moment and for only feeding it the bars the batch model would
have included in that day's `full` window.
"""
from collections import deque


class IntradaySwingDetector:
    def __init__(self, swing_n: int = 2):
        self.swing_n = swing_n
        self._window = deque(maxlen=2 * swing_n + 1)
        self._bar_index = -1

    def start_new_day(self) -> None:
        """Call once, before feeding the first bar of a new trading day.
        Clears all memory of the previous day -- matches the batch model
        recomputing piv_high_all/piv_low_all fresh per day rather than
        carrying state across days."""
        self._window.clear()
        self._bar_index = -1

    def on_new_bar(self, timestamp, high: float, low: float) -> list[dict]:
        """
        Feed exactly one new 5-minute bar for the current day. Returns a
        list of zero or more confirmed events, matching the golden
        master's intraday_swing_high_confirmed / intraday_swing_low_confirmed
        shape:
            {"event_type": ..., "timestamp": ..., "price": ..., "bar_index": ...}

        bar_index counts from 0 at the start of the current day (i.e.
        since the last start_new_day() call) -- it is NOT a global bar
        count across the whole history.
        """
        self._bar_index += 1
        self._window.append((self._bar_index, timestamp, high, low))

        events = []
        if len(self._window) < self._window.maxlen:
            return events

        candidate_idx, candidate_ts, candidate_high, candidate_low = self._window[self.swing_n]
        highs = [h for (_, _, h, _) in self._window]
        lows = [l for (_, _, _, l) in self._window]

        if candidate_high == max(highs):
            events.append(
                {
                    "event_type": "intraday_swing_high_confirmed",
                    "timestamp": candidate_ts,
                    "price": float(candidate_high),
                    "bar_index": candidate_idx,
                }
            )
        if candidate_low == min(lows):
            events.append(
                {
                    "event_type": "intraday_swing_low_confirmed",
                    "timestamp": candidate_ts,
                    "price": float(candidate_low),
                    "bar_index": candidate_idx,
                }
            )
        return events
