"""
MSS (market structure shift) watch.

Batch logic being reimplemented (uptrend case shown -- downtrend mirrors):

    recent_high_level = highs[piv_high_all[ph_pos - 1]]
    closes = full["close"].values
    for j in range(i + 1, min(i + 10, n)):
        if closes[j] > recent_high_level:
            log_event("mss_confirmed", ...)
            fvg = find_fvg(highs, lows, j, "bull")
            ...

In plain terms: after a raid at bar i, look at each of the next up-to-9
bars. If any bar's CLOSE crosses back beyond the reference level (the
swing on the opposite side from the one that triggered the raid), that's
a market structure shift.

Unlike the previous components, this is NOT a long-running detector fed
every bar of the day -- it's a short-lived watch, one per raid, that
only cares about the ~9 bars immediately following its own raid. This
shape matches the batch model's actual structure: MSS search is a bounded
lookahead tied to one specific raid, not a continuously-running process.

Two things worth being explicit about, both confirmed directly from the
batch code:

1. **The window is bounded by the bars in the day, NOT by the Kill
   Zone.** `min(i + 10, n)` uses `n` = the day's FULL bar count (5 AM to
   5 PM), not the Kill Zone's end. A raid detected near the Kill Zone's
   10 AM boundary can have its MSS search extend past 10 AM into bars
   RaidDetector would never have evaluated for a NEW raid.

2. **MSS can confirm more than once per raid.** The batch loop doesn't
   stop at the first bar whose close crosses the level -- it keeps
   checking every bar in the window (via `continue` when no valid FVG
   follows), so multiple mss_confirmed events can fire for the same
   raid if price closes beyond the level on several bars in a row
   without producing a valid trade. This class reflects that: call
   on_new_bar() for every bar in the window and it may return an event
   on more than one of them.
"""


class MSSWatch:
    MSS_WINDOW_BARS = 9  # matches batch's range(i+1, min(i+10, n)) -- 9 candidate bars

    def __init__(self, raid_bar_index: int, direction: str, reference_level: float):
        """
        direction: "bull" or "bear" (matches RaidDetector's raid_detected
        event's own "direction" field -- NOT "up"/"down").
        reference_level: the raid event's "mss_reference_level" field.
        """
        self.raid_bar_index = raid_bar_index
        self.direction = direction
        self.reference_level = reference_level

    def is_expired(self, bar_index: int) -> bool:
        """True once bar_index has moved past this watch's window --
        callers should stop feeding it bars and discard it."""
        return bar_index > self.raid_bar_index + self.MSS_WINDOW_BARS

    def on_new_bar(self, timestamp, bar_index: int, close: float) -> list[dict]:
        """
        Feed bars strictly after the raid, in order. Returns a list with
        0 or 1 mss_confirmed event -- bars outside the window (before or
        after) always return [].
        """
        if bar_index <= self.raid_bar_index or self.is_expired(bar_index):
            return []

        if self.direction == "bull" and close > self.reference_level:
            return [
                {
                    "event_type": "mss_confirmed",
                    "timestamp": timestamp,
                    "direction": "bull",
                    "level": float(self.reference_level),
                    "close": float(close),
                    "raid_bar_index": self.raid_bar_index,
                    "mss_bar_index": bar_index,
                }
            ]
        if self.direction == "bear" and close < self.reference_level:
            return [
                {
                    "event_type": "mss_confirmed",
                    "timestamp": timestamp,
                    "direction": "bear",
                    "level": float(self.reference_level),
                    "close": float(close),
                    "raid_bar_index": self.raid_bar_index,
                    "mss_bar_index": bar_index,
                }
            ]
        return []
