"""
fractal_swings.py -- streaming reimplementation of the reference
package's `scalp_common.find_fractal_swings()`.

The reference (batch, whole-array) version:

    w = FRACTAL_WING  # 2
    for i in range(w, n - w):
        window_h = high[i-w : i+w+1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            swing_high[i] = True
        window_l = low[i-w : i+w+1]
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            swing_low[i] = True

Bar i is a confirmed swing high/low only once bars i-w through i+w are
ALL known -- i.e. confirmation always lags the extreme bar by exactly
`wing` bars. That lag is exactly what makes this streamable with no
lookahead: a live process simply hasn't confirmed bar i yet until w
more bars have arrived after it, same as the batch version's own
array-slicing already implies.

STRICT uniqueness, matching the reference exactly: a tie for the
window's max/min (two bars in the window sharing the identical high)
means NEITHER is confirmed as a swing high that round -- `(window ==
value).sum() == 1` in the original, `count == 1` here.
"""
from collections import deque

FRACTAL_WING = 2  # matches scalp_common.py's own module-level constant


class FractalSwingDetector:
    """Feed bars one at a time via `update()`. Returns a confirmation
    dict the moment enough bars have arrived to confirm the bar that
    sits `wing` positions back from the one just fed -- None on every
    other call (not enough bars yet, most of the time there's nothing
    to confirm this exact call... no, actually: once past the initial
    fill, every single call confirms exactly one bar, since the window
    slides by one each time a new bar arrives)."""

    def __init__(self, wing: int = FRACTAL_WING):
        self.wing = wing
        self._window_size = 2 * wing + 1
        self._buf = deque(maxlen=self._window_size)
        self._next_idx = 0  # global index of the next bar update() will receive

    def update(self, high: float, low: float) -> dict | None:
        """Feed one new bar's (high, low). Returns
        {"idx": int, "high": float, "low": float, "swing_high": bool, "swing_low": bool}
        for the bar that is now fully confirmable (the one `wing` bars
        back from this call), or None if fewer than `2*wing+1` bars
        have been fed so far."""
        self._buf.append((high, low))
        confirmed_idx = self._next_idx - self.wing
        self._next_idx += 1

        if len(self._buf) < self._window_size:
            return None

        highs = [h for h, _ in self._buf]
        lows = [l for _, l in self._buf]
        mid_high = highs[self.wing]
        mid_low = lows[self.wing]

        swing_high = (mid_high == max(highs)) and (highs.count(mid_high) == 1)
        swing_low = (mid_low == min(lows)) and (lows.count(mid_low) == 1)

        return {
            "idx": confirmed_idx,
            "high": mid_high,
            "low": mid_low,
            "swing_high": swing_high,
            "swing_low": swing_low,
        }


def find_fractal_swings_streaming(highs, lows, wing: int = FRACTAL_WING):
    """Batch-shaped convenience wrapper ONLY for testing bit-for-bit
    equivalence against the reference's vectorized find_fractal_swings()
    -- feeds a full array through FractalSwingDetector one bar at a
    time and reassembles the same (swing_high, swing_low) boolean-list
    shape the reference function returns. Not used by the live
    detector itself (which consumes FractalSwingDetector directly,
    bar by bar, via update())."""
    n = len(highs)
    swing_high = [False] * n
    swing_low = [False] * n
    det = FractalSwingDetector(wing=wing)
    for i in range(n):
        result = det.update(highs[i], lows[i])
        if result is not None:
            swing_high[result["idx"]] = result["swing_high"]
            swing_low[result["idx"]] = result["swing_low"]
    return swing_high, swing_low
