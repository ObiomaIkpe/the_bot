"""
FVG (fair value gap) detector.

Batch logic being reimplemented (from extract_golden_master.py):

    def find_fvg(highs, lows, i, direction):
        if i < 2:
            return None
        if direction == "bear":
            if lows[i - 2] > highs[i]:
                return {"top": lows[i-2], "bottom": highs[i], "frame_idx": i-2}
        else:
            if highs[i - 2] < lows[i]:
                return {"top": lows[i], "bottom": highs[i-2], "frame_idx": i-2}
        return None

Unlike every previous component, this one needs NO waiting. The check
only ever looks at the candle 2 bars ago and the current candle, both
fully known the instant the current bar arrives -- there's no
confirmation delay, nothing here becomes "true later." The only reason
this is a class rather than a bare function is to carry the rolling
3-bar buffer so callers don't have to manage it themselves.

The batch model only ever calls find_fvg() at the exact bar an MSS
confirmation just fired -- this class mirrors that split deliberately:
on_new_bar() updates the rolling buffer unconditionally (call it for
EVERY bar), while check_fvg() is a separate call the orchestrator makes
only when it actually wants to test (i.e. right when MSS confirms),
using whatever's currently in the buffer. Calling check_fvg() at a bar
where the batch model never would have (any bar that isn't an MSS
confirmation) will find real gaps that genuinely exist in the price
data but that the batch model never looked for -- that's a caller
misuse, not a bug in this class.
"""
from collections import deque


class FVGDetector:
    def __init__(self):
        self._window = deque(maxlen=3)  # (bar_index, high, low), oldest to newest

    def on_new_bar(self, bar_index: int, high: float, low: float) -> None:
        """Call for every bar, unconditionally. Just updates the rolling
        window -- never checks anything or returns events."""
        self._window.append((bar_index, high, low))

    def check_fvg(self, timestamp, direction: str) -> dict | None:
        """
        Call only when you want to test for an FVG ending at the most
        recently fed bar -- typically right when MSS confirms.

        direction: "bull" or "bear" (matches MSSWatch's event direction).

        Returns an fvg_found event dict, or None if fewer than 3 bars
        have been fed yet or no gap exists:
            {"event_type": "fvg_found", "timestamp": ..., "direction": ...,
             "top": ..., "bottom": ..., "frame_idx": ..., "mss_bar_index": ...}
        """
        if len(self._window) < 3:
            return None

        (idx_2ago, high_2ago, low_2ago), _mid, (idx_now, high_now, low_now) = self._window

        if direction == "bear":
            if low_2ago > high_now:
                return {
                    "event_type": "fvg_found",
                    "timestamp": timestamp,
                    "direction": "bear",
                    "top": float(low_2ago),
                    "bottom": float(high_now),
                    "frame_idx": idx_2ago,
                    "mss_bar_index": idx_now,
                }
        else:  # "bull"
            if high_2ago < low_now:
                return {
                    "event_type": "fvg_found",
                    "timestamp": timestamp,
                    "direction": "bull",
                    "top": float(low_now),
                    "bottom": float(high_2ago),
                    "frame_idx": idx_2ago,
                    "mss_bar_index": idx_now,
                }
        return None
