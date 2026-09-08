"""
htf_bias.py -- streaming reimplementation of the reference package's
htf_bias_cascade.py: a close-only, non-repainting 3-candle fractal bias
(wing=1 by default), applied independently at Monthly/Weekly/Daily
timeframes, with a Section-22-style full-agreement check across all
three.

Unlike signal_detector.py, this one needed no real streaming redesign
-- fractal_bias_series() is ALREADY causal bar by bar (bias[i] only
ever depends on closes up to and including i itself, no forward
lookahead at all), so FractalBiasTracker below is a direct, small
translation: a rolling buffer of the last `2*wing+1` closes, re-run the
same min/max-uniqueness check each time a new bar arrives.
"""
from collections import deque


class FractalBiasTracker:
    """One instance per timeframe (Monthly, Weekly, Daily each get
    their own). Feed each new CLOSED bar's close via update(); returns
    "bullish" | "bearish" | None (still-warming-up), matching
    fractal_bias_series()'s own None-until-first-confirmation and
    sticky-until-flipped semantics exactly."""

    def __init__(self, wing: int = 1):
        self.wing = wing
        self.span = 2 * wing
        self._closes: deque = deque(maxlen=self.span + 1)
        self._current: str | None = None

    def update(self, close: float) -> str | None:
        self._closes.append(close)
        if len(self._closes) < self.span + 1:
            return self._current  # still warming up -- always None at this point

        window = list(self._closes)
        mid = window[self.wing]
        if mid == min(window) and window.count(mid) == 1:
            self._current = "bullish"
        elif mid == max(window) and window.count(mid) == 1:
            self._current = "bearish"
        # else: no new confirmation this bar -- _current stays whatever it was
        return self._current


def fractal_bias_series_streaming(closes, wing: int = 1):
    """Batch-shaped convenience wrapper ONLY for testing bit-for-bit
    equivalence against the reference's fractal_bias_series(). Not used
    by the live cascade itself."""
    tracker = FractalBiasTracker(wing=wing)
    return [tracker.update(c) for c in closes]


def classify_case(monthly_bias, weekly_bias, daily_bias, signal_direction):
    """Direct translation of the reference's classify_case() -- pure
    function, no streaming concern (just a comparison of three already-
    known current values against a signal's direction)."""
    sig_dir_word = "bullish" if signal_direction == "long" else "bearish"
    if monthly_bias == weekly_bias == daily_bias == sig_dir_word:
        return "case1"
    if monthly_bias == weekly_bias == sig_dir_word and daily_bias is not None and daily_bias != monthly_bias:
        return "case2"
    if weekly_bias == daily_bias and weekly_bias is not None and weekly_bias != monthly_bias and monthly_bias == sig_dir_word:
        return "case3"
    return None


def full_agreement(monthly_bias, weekly_bias, daily_bias) -> str | None:
    """The reference's build_cascade_bias() "agreed" column: Daily's
    own bias value if all three timeframes agree, else None. This is
    the only cascade case the two locked Cascade Raid models actually
    use (Section 22 Case 1 -- see htf_bias_cascade.py's own module
    docstring: Cases 2/3's partial-agreement logic is defined but not
    wired into either locked model)."""
    if daily_bias is not None and daily_bias == weekly_bias == monthly_bias:
        return daily_bias
    return None
