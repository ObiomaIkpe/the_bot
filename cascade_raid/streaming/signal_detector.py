"""
signal_detector.py -- streaming reimplementation of the reference
package's `scalp_common.detect_signals()` (liquidity sweep -> rapid
market-structure-shift -> fair-value-gap -> equilibrium-filter signal
detection).

THE CORE STREAMING CHALLENGE, and how it's solved here:

The reference is a whole-array batch function. At the exact bar a sweep
is detected, it immediately resolves that sweep's entire fate --
scanning up to MSS_WINDOW bars AHEAD for the structure break, then
walking backward for the FVG -- because in a batch context those future
bars already sit in the array. A live process obviously can't do that;
it only knows about a sweep's future outcome once that future actually
arrives, bar by bar.

This is solved with explicit PENDING WATCHES: the instant a sweep is
detected, a pending-MSS record is created (direction, sweep bar's
own extreme, the structure-break threshold, a deadline `sweep_idx +
MSS_WINDOW`) instead of resolving it immediately. Every subsequent bar
checks ALL currently-pending watches against its own close; the first
one to break the threshold resolves (and only then is the backward FVG
walk performed, using a rolling bar-history buffer). A watch that
reaches its deadline unresolved simply expires -- no signal, matching
the reference's `if mss_idx is not None`.

Multiple pending watches (same direction or opposite) CAN be in flight
at once -- confirmed by tracing the reference: `recent_sh_price` is
consumed (set to None) the INSTANT a sweep triggers, not when its MSS
resolves, so a fresh swing high can reconfirm and get swept again while
an earlier sweep's MSS search window is still open. This class supports
that -- pending watches are a list, not a single slot.

Everything else mirrors the reference exactly: strict pool-consumption
semantics (swept whether or not a signal ultimately results), the
per-bar ordering (pool update, then bearish check, then bullish check),
and the equilibrium filter's aggressive-half requirement.
"""
from collections import deque

from cascade_raid.streaming.fractal_swings import FractalSwingDetector

PIP = 0.0001
STRUCTURE_LOOKBACK = 10
MSS_WINDOW = 15
FILL_EXPIRY = 30
FRACTAL_WING = 2

# Bar-history buffer needs to cover, at worst, from a pending watch's
# sweep_idx (created up to MSS_WINDOW bars ago) through "now", plus
# STRUCTURE_LOOKBACK for the threshold computation at sweep time, plus
# a small margin.
_HISTORY_MAXLEN = STRUCTURE_LOOKBACK + MSS_WINDOW + FRACTAL_WING + 10


class SignalDetector:
    def __init__(self):
        self._fractal = FractalSwingDetector(wing=FRACTAL_WING)
        self._history: deque = deque(maxlen=_HISTORY_MAXLEN)  # (idx, high, low)

        self._recent_sh_price = None
        self._recent_sh_idx = None
        self._recent_sl_price = None
        self._recent_sl_idx = None

        self._pending_bearish: list[dict] = []  # {sweep_idx, sweep_high, structure_low, deadline}
        self._pending_bullish: list[dict] = []  # {sweep_idx, sweep_low, structure_high, deadline}

        self._next_idx = 0

    def _hl_at(self, idx: int):
        """O(1): `_history` holds strictly-consecutive global indices,
        so idx's position is a direct offset from the oldest entry --
        no need to scan."""
        oldest_idx = self._history[0][0]
        pos = idx - oldest_idx
        if pos < 0 or pos >= len(self._history):
            raise KeyError(f"bar {idx} no longer in history buffer (increase _HISTORY_MAXLEN)")
        _, h, l = self._history[pos]
        return h, l

    def _find_fvg_bearish(self, sweep_idx: int, mss_idx: int):
        for k in range(mss_idx, sweep_idx + 1, -1):
            h_k, _ = self._hl_at(k)
            _, l_km2 = self._hl_at(k - 2)
            if l_km2 > h_k:
                h_frame, l_frame = self._hl_at(k - 1)
                return k, l_km2, h_k, h_frame, l_frame  # k, fvg_top, fvg_bot, frame_hi, frame_lo
        return None

    def _find_fvg_bullish(self, sweep_idx: int, mss_idx: int):
        for k in range(mss_idx, sweep_idx + 1, -1):
            _, l_k = self._hl_at(k)
            h_km2, _ = self._hl_at(k - 2)
            if h_km2 < l_k:
                h_frame, l_frame = self._hl_at(k - 1)
                return k, l_k, h_km2, h_frame, l_frame  # k, fvg_top, fvg_bot, frame_hi, frame_lo
        return None

    def update(self, high: float, low: float, close: float) -> list[dict]:
        """Feed one new bar (already-closed). Returns a list of zero or
        more signal dicts newly confirmed on THIS bar -- same shape as
        one entry of the reference's detect_signals() output, plus
        `signal_idx` for downstream ordering."""
        i = self._next_idx
        self._next_idx += 1
        self._history.append((i, high, low))

        new_signals: list[dict] = []

        # Pool update -- matches the reference's exact per-bar ordering
        # (uses the fractal confirmation for bar i-wing, computed from
        # the window ending at bar i).
        confirmation = self._fractal.update(high, low)
        if confirmation is not None:
            if confirmation["swing_high"]:
                self._recent_sh_price = confirmation["high"]
                self._recent_sh_idx = confirmation["idx"]
            if confirmation["swing_low"]:
                self._recent_sl_price = confirmation["low"]
                self._recent_sl_idx = confirmation["idx"]

        # Resolve any pending MSS watches using THIS bar's close --
        # first-match-wins per watch, matching the reference's `break`.
        still_pending_bearish = []
        for watch in self._pending_bearish:
            if close < watch["structure_low"]:
                new_signals.extend(self._resolve_bearish(watch, mss_idx=i))
            elif i < watch["deadline"]:
                still_pending_bearish.append(watch)
            # else: deadline reached this bar with no break -> expires, dropped
        self._pending_bearish = still_pending_bearish

        still_pending_bullish = []
        for watch in self._pending_bullish:
            if close > watch["structure_high"]:
                new_signals.extend(self._resolve_bullish(watch, mss_idx=i))
            elif i < watch["deadline"]:
                still_pending_bullish.append(watch)
        self._pending_bullish = still_pending_bullish

        # --- Bearish candidate: sweep of recent swing high ---
        if self._recent_sh_price is not None and high > self._recent_sh_price and close < self._recent_sh_price:
            structure_lo_start = max(0, i - STRUCTURE_LOOKBACK)
            structure_low = min(
                l for idx_, _, l in self._history if structure_lo_start <= idx_ <= i
            )
            self._pending_bearish.append(dict(
                sweep_idx=i, sweep_high=high, structure_low=structure_low, deadline=i + MSS_WINDOW,
            ))
            self._recent_sh_price = None  # pool consumed either way

        # --- Bullish candidate: sweep of recent swing low ---
        if self._recent_sl_price is not None and low < self._recent_sl_price and close > self._recent_sl_price:
            structure_hi_start = max(0, i - STRUCTURE_LOOKBACK)
            structure_high = max(
                h for idx_, h, _ in self._history if structure_hi_start <= idx_ <= i
            )
            self._pending_bullish.append(dict(
                sweep_idx=i, sweep_low=low, structure_high=structure_high, deadline=i + MSS_WINDOW,
            ))
            self._recent_sl_price = None

        return new_signals

    def _resolve_bearish(self, watch: dict, mss_idx: int) -> list[dict]:
        fvg = self._find_fvg_bearish(watch["sweep_idx"], mss_idx)
        if fvg is None:
            return []
        _, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
        _, mss_low = self._hl_at(mss_idx)
        leg_range = watch["sweep_high"] - mss_low
        if leg_range <= 0:
            return []
        fvg_mid = (fvg_top + fvg_bot) / 2
        eq = mss_low + leg_range * 0.5
        if fvg_mid < eq:  # not the aggressive/expensive half for bearish
            return []
        return [dict(
            direction="short", sweep_idx=watch["sweep_idx"], mss_idx=mss_idx,
            fvg_near=fvg_top, fvg_far=fvg_bot,
            frame_high=frame_hi, frame_low=frame_lo,
            leg_extreme=mss_low, signal_idx=mss_idx,
        )]

    def _resolve_bullish(self, watch: dict, mss_idx: int) -> list[dict]:
        fvg = self._find_fvg_bullish(watch["sweep_idx"], mss_idx)
        if fvg is None:
            return []
        _, fvg_top, fvg_bot, frame_hi, frame_lo = fvg
        mss_high, _ = self._hl_at(mss_idx)
        leg_range = mss_high - watch["sweep_low"]
        if leg_range <= 0:
            return []
        fvg_mid = (fvg_top + fvg_bot) / 2
        eq = watch["sweep_low"] + leg_range * 0.5
        if fvg_mid > eq:  # not the aggressive/cheap half for bullish
            return []
        return [dict(
            direction="long", sweep_idx=watch["sweep_idx"], mss_idx=mss_idx,
            fvg_near=fvg_bot, fvg_far=fvg_top,
            frame_high=frame_hi, frame_low=frame_lo,
            leg_extreme=mss_high, signal_idx=mss_idx,
        )]


def detect_signals_streaming(m1_highs, m1_lows, m1_closes) -> list[dict]:
    """Batch-shaped convenience wrapper ONLY for testing bit-for-bit
    equivalence against the reference's detect_signals() -- feeds a
    full series through SignalDetector bar by bar. Not used by the live
    detector itself."""
    det = SignalDetector()
    signals = []
    for h, l, c in zip(m1_highs, m1_lows, m1_closes):
        signals.extend(det.update(h, l, c))
    return signals
