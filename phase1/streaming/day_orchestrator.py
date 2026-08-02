"""
Day orchestrator: coordinates all five streaming components across one
trading day, reproducing the batch model's day loop exactly.

WHY THIS EXISTS -- THE SCHEDULING BUG IT FIXES
----------------------------------------------
A first orchestration attempt (validation-script wiring, never shipped
as a component) assumed "one raid's search at a time": once a raid
started its MSS search, no new raid was considered until that search
resolved. That produced 318 trades instead of 603 against the golden
master. The batch model actually works differently:

    for i in session bars:              # EVERY bar is a fresh raid candidate
        if trade_found: break
        if raid at bar i:
            for j in i+1 .. i+9:        # MSS window for THIS raid
                if MSS at j and FVG at j and min-stop ok:
                    fill search j+1 .. end of day
                    if filled: trade (any outcome), trade_found, break
                    else: continue      # next j, same raid
            # raid i's search failed entirely -> bar i+1 gets ITS turn

On a full array this all resolves instantly per bar. In streaming time
it cannot: raid 24's search isn't known to have failed until its window
and fill searches are exhausted -- but bars 25, 26, ... have already
passed, and each deserved its own candidacy. The only faithful
streaming translation is:

  1. EVERY raid spawns its own candidate search, run in parallel.
  2. Within a raid, EVERY qualifying MSS bar spawns its own
     TradeAttempt, also run in parallel.
  3. The day's trade is the attempt with the lexicographically
     smallest (raid_bar, mss_bar) key among those that actually
     FILLED. Not the earliest fill in wall-clock time -- the earliest
     CANDIDATE. (A raid-24 attempt filling at bar 50 beats a raid-25
     attempt filling at bar 40, because the batch model would have
     found raid 24's fill first and never evaluated raid 25 at all.)
  4. "Filled" means filled -- a loss or end-of-day scratch still wins
     the day. Unfilled/abandoned/min-stop-rejected attempts never win.

SECOND BUG THIS FIXES: TradeAttempt seed bars must include the MSS bar
itself (the batch's target window highs[p-6:p] spans p-6..p-1, which
includes the MSS bar j when the fill comes soon after j). The failed
attempt seeded with bars up to j-1 only, producing wrong targets on
trades where the fill came within 6 bars of the FVG -- the likely
cause of the 29 field mismatches observed alongside the missing
trades. Here the rolling seed buffer is updated with the current bar
BEFORE any attempts are spawned at that bar.

WHAT THIS DELIBERATELY DOESN'T RE-VERIFY: this class trusts each
component to be individually correct (they each have exact-match
validation against the golden master); its only job is scheduling
them the way the batch model does.

PHASE 3 ADDITION -- event_sink (additive only, see below)
-----------------------------------------------------------
Originally this class computed and used every sub-component's events
internally but never exposed them -- fine when the only thing anyone
needed was the final trade from finalize(). Phase 3 (shadow mode) needs
to journal every intermediate event too (swings, raids, MSS, FVGs,
fills, closes), not just the final trade.

The fix is a single optional constructor parameter, event_sink -- a
callback invoked with each event dict at the exact point in the
existing code where that event already gets computed. This is the same
pattern extract_golden_master.py itself uses (log_event() as a pure
side effect that never influences control flow): every line below that
mentions event_sink is a new, additive call; nothing about the
existing scheduling logic, priority resolution, or return values
changed. If you diff this file against the pre-Phase-3 version and find
anything other than added `if self._event_sink:` blocks and the
constructor parameter itself, that's a bug in this change, not a
license to alter behavior. The existing test_day_orchestrator.py suite
(which drives finalize() directly, bypassing on_new_bar) still passes
unmodified, and event_sink defaults to None so every previous caller
behaves identically.
"""
from phase1.streaming.intraday_swing_detector import IntradaySwingDetector
from phase1.streaming.raid_detector import RaidDetector
from phase1.streaming.mss_watch import MSSWatch
from phase1.streaming.fvg_detector import FVGDetector
from phase1.streaming.trade_attempt import TradeAttempt, TARGET_LOOKBACK_BARS


class DayOrchestrator:
    def __init__(self, trend: str, session_start_idx: int, session_end_idx: int, event_sink=None):
        """
        trend: "up" or "down" -- this day's daily-trend direction.
        session_start_idx / session_end_idx: the Kill Zone's bar-index
        bounds within this day's bars (raids only spawn inside these;
        MSS watches and fill searches run to end of day regardless).
        event_sink: optional callable(event: dict) -> None, invoked for
        every intermediate event as it's computed (swings, raids, MSS,
        FVGs found/rejected, fills, closes). Added for Phase 3
        journaling -- see module docstring. None (default) = no change
        from pre-Phase-3 behavior.

        One orchestrator instance = one day. Construct fresh each day.
        """
        self.trend = trend
        self.session_start_idx = session_start_idx
        self.session_end_idx = session_end_idx
        self._event_sink = event_sink

        self._swing_det = IntradaySwingDetector(swing_n=2)
        self._swing_det.start_new_day()
        self._raid_det = RaidDetector()
        self._raid_det.start_new_day()
        self._fvg_det = FVGDetector()

        self._candidates = []  # list of {"raid_bar": int, "watch": MSSWatch}
        self._attempts = []    # list of {"key": (raid_bar, mss_bar), "attempt": TradeAttempt}
        self._recent_bars = []  # rolling last-TARGET_LOOKBACK_BARS (bar_index, high, low), INCLUDING current bar

    def _emit(self, event: dict) -> None:
        """Pure side effect, exactly like extract_golden_master.py's
        log_event() -- never influences control flow. No-op if no sink
        was provided."""
        if self._event_sink is not None:
            self._event_sink(event)

    def on_new_bar(self, timestamp, bar_index: int, high: float, low: float, close: float) -> None:
        """Feed every bar of the day (from 5 AM), in order."""
        # Rolling seed buffer updated FIRST -- attempts spawned at this bar
        # must see this bar in their seed (see "second bug" note above).
        self._recent_bars.append((bar_index, high, low))
        self._recent_bars = self._recent_bars[-TARGET_LOOKBACK_BARS:]

        swing_events = self._swing_det.on_new_bar(timestamp, high, low)
        for e in swing_events:
            self._emit(e)
        self._fvg_det.on_new_bar(bar_index, high, low)

        # 1. Feed all live attempts (fill/outcome tracking). Attempts spawned
        #    at THIS bar are appended later and therefore start from the next
        #    bar -- matching the batch's fill search range(j+1, n).
        for a in self._attempts:
            if a["attempt"].is_active():
                for e in a["attempt"].on_new_bar(timestamp, bar_index, high, low):
                    self._emit(e)

        # 2. Feed all live MSS watches; spawn attempts from confirmations.
        #    A watch spawned at THIS bar (below) guards internally against
        #    being evaluated on its own raid bar.
        for c in self._candidates:
            watch = c["watch"]
            if watch.is_expired(bar_index):
                continue
            for mss_e in watch.on_new_bar(timestamp, bar_index, close):
                self._emit(mss_e)
                fvg_e = self._fvg_det.check_fvg(timestamp, mss_e["direction"])
                if not fvg_e:
                    continue
                self._emit(fvg_e)
                trade_dir = "long" if mss_e["direction"] == "bull" else "short"
                entry_price = (fvg_e["top"] + fvg_e["bottom"]) / 2
                # stop = the frame candle's low (long) / high (short); the frame
                # candle is 2 bars back from the current bar, so it's in the
                # seed buffer -- but index it via the FVG event to stay exact.
                frame_idx = fvg_e["frame_idx"]
                frame_bar = next(b for b in self._recent_bars if b[0] == frame_idx)
                stop = frame_bar[2] if trade_dir == "long" else frame_bar[1]
                attempt = TradeAttempt(
                    trade_dir, entry_price, stop, bar_index,
                    seed_bars=list(self._recent_bars),
                )
                if attempt.status == "rejected_min_stop":
                    # TradeAttempt itself doesn't emit an event for this
                    # (it's a construction-time rejection, not something
                    # discovered via on_new_bar) -- synthesized here to
                    # match extract_golden_master.py's fvg_rejected_min_stop
                    # log_event() call, same fields (direction, risk_pips).
                    self._emit(
                        {
                            "event_type": "fvg_rejected_min_stop",
                            "timestamp": timestamp,
                            "direction": mss_e["direction"],
                            "risk_pips": float(attempt.risk_pips),
                        }
                    )
                else:
                    self._attempts.append(
                        {"key": (c["raid_bar"], mss_e["mss_bar_index"]), "attempt": attempt}
                    )

        # 3. Raid detection (Kill Zone only, enforced via in_kill_zone flag).
        #    EVERY raid spawns its own candidate -- this is the fix.
        in_kz = self.session_start_idx <= bar_index < self.session_end_idx
        raid_events = self._raid_det.on_new_bar(
            timestamp, bar_index, high, low, self.trend, in_kz, swing_events
        )
        for raid_e in raid_events:
            self._emit(raid_e)
            self._candidates.append(
                {
                    "raid_bar": raid_e["bar_index"],
                    "watch": MSSWatch(
                        raid_e["bar_index"], raid_e["direction"], raid_e["mss_reference_level"]
                    ),
                }
            )

    def finalize(self, last_timestamp, final_close: float) -> dict | None:
        """
        Call once after the day's final bar. Scratches any still-open
        winner and returns the day's trade dict (or None if no attempt
        ever filled), matching the batch model's per-day trade record:
            {"direction", "entry", "stop", "target", "risk_pips",
             "outcome", "exit_price"}
        """
        filled = [
            a for a in self._attempts
            if a["attempt"].status == "closed" or a["attempt"].status == "filled"
        ]
        if not filled:
            return None

        winner = min(filled, key=lambda a: a["key"])["attempt"]

        if winner.status == "filled":  # still open at end of day
            scratch_e = winner.close_as_scratch(last_timestamp, final_close)
            if scratch_e:
                self._emit(scratch_e)

        return {
            "direction": winner.direction,
            "entry": winner.entry_price,
            "stop": winner.stop,
            "target": winner.target,
            "risk_pips": winner.risk_pips,
            "outcome": winner.outcome,
            "exit_price": winner.exit_price,
        }