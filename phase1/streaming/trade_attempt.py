"""
Trade attempt tracker: everything that happens to ONE fair value gap
once it's found -- min-stop rejection, waiting for a limit fill,
computing the dynamic target, and tracking the trade through to its
outcome.

Batch logic being reimplemented (long/uptrend case -- short mirrors):

    entry_price = (fvg["top"] + fvg["bottom"]) / 2
    stop = lows[fvg["frame_idx"]]
    risk = entry_price - stop
    if risk / PIP < MIN_STOP_PIPS:
        continue  # rejected, no trade
    for p in range(j + 1, n):
        if lows[p] <= entry_price:
            if p < 6:
                break  # not enough day-history for a target window
            window_highs = highs[p-6:p]
            extreme_idx = p - 6 + argmax(window_highs)
            target = (highs[extreme_idx] + lows[extreme_idx]) / 2
            if target <= entry_price:
                break  # invalid target -- ABANDON, no retry at a later touch
            # filled
            for q in range(p, n):
                if lows[q] <= stop:
                    outcome, exit_price = "loss", stop
                    break
                if highs[q] >= target:
                    outcome, exit_price = "win", target
                    break
            break  # only ONE fill attempt ever happens, regardless of outcome
    if not filled: continue
    if outcome is None:
        outcome, exit_price = "scratch", <day's final close>

Three details that are easy to get wrong, all confirmed directly from
the batch code above:

1. **The target's 6-bar lookback uses the day's FULL price history, not
   "bars since this FVG."** If the fill happens soon after the FVG (a
   handful of bars later), some of those 6 bars can genuinely be from
   before the FVG existed -- even from within the raid-to-MSS leg. This
   is why this class must be SEEDED with up to 6 bars of history at
   construction time (see seed_bars below), rather than starting with
   an empty memory the way MSSWatch and FVGDetector do.

2. **Only ONE fill attempt ever happens per FVG.** If the first bar
   that touches the entry price produces an invalid target (`target <=
   entry_price` for long), the batch model does not keep searching for
   a LATER touch -- it abandons this FVG entirely, no trade. This class
   reflects that: once a touch is evaluated (valid or not), no further
   touches are ever checked.

3. **The stop/target outcome check starts AT the fill bar itself (`q`
   starts at `p`, inclusive), not the bar after.** A single bar can
   both fill the order AND immediately hit stop or target.
"""
from collections import deque

MIN_STOP_PIPS = 5
PIP = 0.0001
TARGET_LOOKBACK_BARS = 6


class TradeAttempt:
    def __init__(
        self,
        direction: str,
        entry_price: float,
        stop: float,
        fvg_bar_index: int,
        seed_bars: list[tuple] | None = None,
    ):
        """
        direction: "long" or "short" -- NOT "bull"/"bear". The caller
        (orchestrator) translates raid/MSS/FVG direction terminology
        into trade-direction terminology: bull -> long, bear -> short.

        entry_price, stop: already computed by the caller from the FVG
        (entry = midpoint of top/bottom, stop = the frame candle's
        low for long / high for short). This class only evaluates and
        tracks price against them -- it doesn't derive them.

        seed_bars: up to TARGET_LOOKBACK_BARS (bar_index, high, low)
        tuples, being the day's most recent bars as of RIGHT BEFORE
        this attempt starts watching (i.e. whatever a continuously-run
        rolling buffer held at the moment the FVG was found). Required
        for the target calculation to be correct if the fill happens
        soon after the FVG -- see point 1 above. Pass an empty list or
        None only if you're certain no fill will be evaluated within
        TARGET_LOOKBACK_BARS of the FVG (in practice: never skip this).
        """
        self.direction = direction
        self.entry_price = entry_price
        self.stop = stop
        self.fvg_bar_index = fvg_bar_index
        self.target = None
        self.status = "pending"  # -> "rejected_min_stop" | "filled" | "abandoned" | "closed"
        self.outcome = None
        self.exit_price = None
        self.fill_bar_index = None

        self._recent_bars = deque(maxlen=TARGET_LOOKBACK_BARS)
        if seed_bars:
            for b in seed_bars[-TARGET_LOOKBACK_BARS:]:
                self._recent_bars.append(b)

        risk = (entry_price - stop) if direction == "long" else (stop - entry_price)
        self.risk_pips = risk / PIP
        if self.risk_pips < MIN_STOP_PIPS:
            self.status = "rejected_min_stop"

    def is_active(self) -> bool:
        """False once this attempt has reached a terminal state --
        callers should stop feeding it bars and discard it."""
        return self.status in ("pending", "filled")

    def on_new_bar(self, timestamp, bar_index: int, high: float, low: float) -> list[dict]:
        """
        Feed bars strictly after the FVG's bar, in order. Returns a
        list of 0+ event dicts (order_filled and/or trade_closed can
        both appear in the same call, if the fill bar also immediately
        hits stop or target).
        """
        events = []
        if not self.is_active():
            return events

        if self.status == "pending":
            touched = (low <= self.entry_price) if self.direction == "long" else (high >= self.entry_price)
            if touched:
                if len(self._recent_bars) < TARGET_LOOKBACK_BARS:
                    # Matches batch's `if p < 6: break` -- practically
                    # unreachable with real Kill-Zone timing (raids
                    # never happen this early in the day), preserved
                    # faithfully regardless.
                    self.status = "abandoned"
                    return events

                if self.direction == "long":
                    extreme_bar = max(self._recent_bars, key=lambda b: b[1])  # highest high
                    target = (extreme_bar[1] + extreme_bar[2]) / 2
                    valid = target > self.entry_price
                else:
                    extreme_bar = min(self._recent_bars, key=lambda b: b[2])  # lowest low
                    target = (extreme_bar[1] + extreme_bar[2]) / 2
                    valid = target < self.entry_price

                if not valid:
                    self.status = "abandoned"  # no retry at a later touch -- matches batch's unconditional break
                    return events

                self.target = target
                self.status = "filled"
                self.fill_bar_index = bar_index
                events.append(
                    {
                        "event_type": "order_filled",
                        "timestamp": timestamp,
                        "direction": self.direction,
                        "entry": float(self.entry_price),
                        "stop": float(self.stop),
                        "target": float(self.target),
                        "fill_bar_index": bar_index,
                    }
                )
                # The outcome check starts at THIS SAME bar (q = p, inclusive).
                closed = self._check_close(timestamp, high, low)
                if closed:
                    events.append(closed)
                return events

            self._recent_bars.append((bar_index, high, low))
            return events

        if self.status == "filled":
            closed = self._check_close(timestamp, high, low)
            if closed:
                events.append(closed)
            return events

        return events

    def _check_close(self, timestamp, high: float, low: float) -> dict | None:
        if self.direction == "long":
            if low <= self.stop:
                self.outcome, self.exit_price = "loss", self.stop
            elif high >= self.target:
                self.outcome, self.exit_price = "win", self.target
        else:
            if high >= self.stop:
                self.outcome, self.exit_price = "loss", self.stop
            elif low <= self.target:
                self.outcome, self.exit_price = "win", self.target

        if self.outcome:
            self.status = "closed"
            return {
                "event_type": "trade_closed",
                "timestamp": timestamp,
                "direction": self.direction,
                "outcome": self.outcome,
                "exit_price": float(self.exit_price),
            }
        return None

    def close_as_scratch(self, timestamp, final_close: float) -> dict | None:
        """Call once, at end of day, if this attempt is still 'filled'
        (i.e. open) when the day ends -- matches batch's
        `if outcome is None: outcome, exit_price = "scratch", <final close>`.
        Returns None if this attempt wasn't open (nothing to scratch)."""
        if self.status != "filled":
            return None
        self.status = "closed"
        self.outcome = "scratch"
        self.exit_price = final_close
        return {
            "event_type": "trade_closed",
            "timestamp": timestamp,
            "direction": self.direction,
            "outcome": "scratch",
            "exit_price": float(final_close),
        }
