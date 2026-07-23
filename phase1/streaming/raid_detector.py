"""
Streaming raid detector.

Batch logic being reimplemented (from extract_golden_master.py), shown
here for the uptrend case -- the downtrend case is the exact mirror:

    for i in range(session_start_idx, session_end_idx):
        pl_pos = bisect.bisect_left(piv_low_all, i - SWING_N)
        ph_pos = bisect.bisect_left(piv_high_all, i - SWING_N)
        if pl_pos == 0 or ph_pos == 0:
            continue
        raid_level = lows[piv_low_all[pl_pos - 1]]
        if lows[i] >= raid_level:
            continue
        # raid detected

In plain terms: at bar i, find the most recently confirmed swing low
whose confirming bar was STRICTLY BEFORE bar i's own confirmation could
happen (`bisect_left(..., i - SWING_N)` enforces this). If price at bar
i has swept below that level, it's a raid. Two easy-to-miss details
carried over faithfully here:

1. **A swing confirmed AT bar i is not usable for bar i's own raid
   check.** Only swings confirmed at some earlier bar count. See the
   "Causality proof" section in phase1/streaming/README.md for why the
   implementation below (check first, then update state) reproduces
   this exactly.

2. **Both a confirmed swing high AND a confirmed swing low must exist**
   before ANY raid check happens, regardless of trend direction --
   even though an uptrend raid only directly uses the swing low. This
   is because the swing high is needed immediately afterward for MSS
   detection (not built yet), and the batch model skips the whole bar
   if that won't be available. Skipping this requirement would let
   RaidDetector fire raids the batch model would have silently passed
   over.
"""


class RaidDetector:
    def __init__(self):
        self._latest_confirmed_swing_high = None  # (bar_index, price) or None
        self._latest_confirmed_swing_low = None

    def start_new_day(self) -> None:
        """Call once per day, before feeding that day's first bar --
        matches the batch model's per-day piv_high_all/piv_low_all reset."""
        self._latest_confirmed_swing_high = None
        self._latest_confirmed_swing_low = None

    def on_new_bar(
        self,
        timestamp,
        bar_index: int,
        high: float,
        low: float,
        direction: str,
        in_kill_zone: bool,
        new_swing_events: list[dict],
    ) -> list[dict]:
        """
        Must be called once per bar, for EVERY bar of the day starting
        from 5 AM -- not just Kill Zone bars. Swing state has to track
        the whole day (swings from before the Kill Zone are valid raid
        references), even though the raid CHECK itself only ever fires
        when in_kill_zone is True.

        new_swing_events: exactly what IntradaySwingDetector.on_new_bar()
        returned for THIS SAME bar. This method uses the state as it
        stood BEFORE folding those in, then updates state afterward --
        that ordering is what makes "a swing confirmed at this bar can't
        be used for this bar's own raid check" true. Do not reorder this.

        direction: "up" or "down" (from the daily trend filter -- not
        yet a component, supplied by the caller for now).

        Returns a list with 0 or 1 raid_detected event dict, matching
        the golden master's shape:
            {"event_type": "raid_detected", "timestamp": ..., "direction": "bull"/"bear",
             "raid_level": ..., "raid_bar_low"/"raid_bar_high": ..., "bar_index": ...}
        """
        events = []

        both_swings_confirmed = (
            self._latest_confirmed_swing_high is not None
            and self._latest_confirmed_swing_low is not None
        )

        if in_kill_zone and both_swings_confirmed:
            if direction == "up":
                raid_level = self._latest_confirmed_swing_low[1]
                if low < raid_level:
                    events.append(
                        {
                            "event_type": "raid_detected",
                            "timestamp": timestamp,
                            "direction": "bull",
                            "raid_level": float(raid_level),
                            "raid_bar_low": float(low),
                            "bar_index": bar_index,
                            # Addition beyond the golden master's own raid_detected
                            # shape -- the OTHER side's confirmed swing, needed by
                            # MSSWatch immediately afterward. Pure addition: doesn't
                            # change any field already validated against the
                            # golden master's 100%-match check.
                            "mss_reference_level": float(self._latest_confirmed_swing_high[1]),
                        }
                    )
            elif direction == "down":
                raid_level = self._latest_confirmed_swing_high[1]
                if high > raid_level:
                    events.append(
                        {
                            "event_type": "raid_detected",
                            "timestamp": timestamp,
                            "direction": "bear",
                            "raid_level": float(raid_level),
                            "raid_bar_high": float(high),
                            "bar_index": bar_index,
                            "mss_reference_level": float(self._latest_confirmed_swing_low[1]),
                        }
                    )

        # Fold in THIS bar's new confirmations only after the check above --
        # they become usable starting from the NEXT bar's check.
        for e in new_swing_events:
            if e["event_type"] == "intraday_swing_high_confirmed":
                self._latest_confirmed_swing_high = (e["bar_index"], e["price"])
            elif e["event_type"] == "intraday_swing_low_confirmed":
                self._latest_confirmed_swing_low = (e["bar_index"], e["price"])

        return events
