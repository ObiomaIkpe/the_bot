"""
Streaming daily swing detector.

Batch logic being reimplemented (from extract_golden_master.py /
FVG_model.py):

    PIVOT_N = 2
    for i in range(PIVOT_N, n_days - PIVOT_N):
        if d_highs[i] == max(d_highs[i-PIVOT_N : i+PIVOT_N+1]):
            swing_high_idx.append(i)
        if d_lows[i] == min(d_lows[i-PIVOT_N : i+PIVOT_N+1]):
            swing_low_idx.append(i)

Day i is a confirmed swing high/low if it's the max/min of the 5-day
window centered on it (2 days before, 2 days after). A streaming process
can't evaluate day i until it has also seen the 2 days after it -- so
confirmation is necessarily delayed by PIVOT_N days behind the bar that
triggers it. This is not a limitation introduced by streaming; the batch
version has the exact same delay, it's just invisible because the whole
array already exists.

No lookahead by construction: on_new_day() only ever receives ONE day's
worth of OHLC at a time. There is no reference to future days anywhere
in this class -- the deque only ever holds days that have already been
handed to it.
"""
from collections import deque


class DailySwingDetector:
    def __init__(self, pivot_n: int = 2):
        self.pivot_n = pivot_n
        self._window = deque(maxlen=2 * pivot_n + 1)
        self._day_index = -1

    def on_new_day(self, timestamp, high: float, low: float) -> list[dict]:
        """
        Feed exactly one new day's high/low. Returns a list of zero or
        more confirmed events, in the same shape as the golden master's
        daily_swing_high_confirmed / daily_swing_low_confirmed:
            {"event_type": ..., "timestamp": ..., "price": ..., "day_index": ...}

        Events are only ever returned for the day that sits PIVOT_N days
        behind the one just fed in -- never for "today".
        """
        self._day_index += 1
        self._window.append((self._day_index, timestamp, high, low))

        events = []
        if len(self._window) < self._window.maxlen:
            return events  # not enough history yet to confirm anything

        candidate_idx, candidate_ts, candidate_high, candidate_low = self._window[self.pivot_n]
        highs = [h for (_, _, h, _) in self._window]
        lows = [l for (_, _, _, l) in self._window]

        if candidate_high == max(highs):
            events.append(
                {
                    "event_type": "daily_swing_high_confirmed",
                    "timestamp": candidate_ts,
                    "price": float(candidate_high),
                    "day_index": candidate_idx,
                }
            )
        if candidate_low == min(lows):
            events.append(
                {
                    "event_type": "daily_swing_low_confirmed",
                    "timestamp": candidate_ts,
                    "price": float(candidate_low),
                    "day_index": candidate_idx,
                }
            )
        return events
