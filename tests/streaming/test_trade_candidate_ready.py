"""
Tests for Phase 4 step 2b's trade_candidate_ready event -- the exact
moment DayOrchestrator first knows a candidate's real entry+stop, which
is what the live-order-manager needs to place a real pending order.

Rather than hand-engineering a full realistic raid->MSS->FVG price
sequence (expensive, fragile, and not what this addition actually
touches), these tests inject controlled fakes for the upstream watch/FVG
objects and drive on_new_bar() directly -- exercising the real
orchestration code this change modified, with predictable inputs.
"""
from phase1.streaming.day_orchestrator import DayOrchestrator


class FakeWatch:
    """Stands in for a real MSSWatch. Returns a canned mss_e on the bar
    index we tell it to, nothing on any other bar."""
    def __init__(self, fire_on_bar_index, mss_e):
        self._fire_on = fire_on_bar_index
        self._mss_e = mss_e
        self._fired = False

    def is_expired(self, bar_index):
        return False

    def on_new_bar(self, timestamp, bar_index, close):
        if bar_index == self._fire_on and not self._fired:
            self._fired = True
            return [self._mss_e]
        return []


class FakeFVGDetector:
    """Stands in for FVGDetector -- on_new_bar is a no-op (real one just
    updates internal state we don't need here); check_fvg returns a
    canned FVG on demand."""
    def __init__(self, fvg_e):
        self._fvg_e = fvg_e

    def on_new_bar(self, bar_index, high, low):
        pass

    def check_fvg(self, timestamp, direction):
        return self._fvg_e


def _seed_bars(frame_idx, frame_low, frame_high, count=6, start_idx=None):
    """Enough recent bars for TradeAttempt's min-stop check and for the
    frame_bar lookup in DayOrchestrator's own code (frame_idx must be
    present in this list)."""
    if start_idx is None:
        start_idx = frame_idx - count + 1
    bars = []
    for i in range(start_idx, start_idx + count):
        if i == frame_idx:
            bars.append((i, frame_high, frame_low))
        else:
            bars.append((i, 1.1050, 1.1040))
    return bars


def test_trade_candidate_ready_fires_with_correct_entry_and_stop():
    received = []
    orch = DayOrchestrator("up", 24, 60, event_sink=received.append)

    # frame_idx must fall within the last TARGET_LOOKBACK_BARS (6) bars
    # AT the moment MSS fires -- self._recent_bars is a rolling window,
    # not full history. Matches realistic data too: the real model's
    # frame_idx is always exactly 2 bars before the FVG/MSS confirmation
    # bar, never far in the past.
    fire_bar = 16
    frame_idx = fire_bar - 2  # 14
    fvg_e = {"event_type": "fvg_found", "timestamp": "t", "top": 1.1060, "bottom": 1.1050, "frame_idx": frame_idx}
    mss_e = {"event_type": "mss_confirmed", "timestamp": "t", "direction": "bull", "mss_bar_index": 15}

    orch._fvg_det = FakeFVGDetector(fvg_e)
    watch = FakeWatch(fire_on_bar_index=fire_bar, mss_e=mss_e)
    orch._candidates = [{"raid_bar": 12, "watch": watch}]

    # Feed enough bars for self._recent_bars to still contain frame_idx
    # (14) once bar_index 16 (where the fake watch fires) is processed.
    # Frame bar's low (1.1040) becomes the stop for a long trade.
    for i in range(5, 20):
        high, low = (1.1060, 1.1040) if i == frame_idx else (1.1052, 1.1048)
        orch.on_new_bar(f"t{i}", i, high, low, (high + low) / 2)

    ready_events = [e for e in received if e.get("event_type") == "trade_candidate_ready"]
    assert len(ready_events) == 1, f"expected exactly one trade_candidate_ready, got {len(ready_events)}"

    e = ready_events[0]
    assert e["direction"] == "long"
    assert e["entry"] == (1.1060 + 1.1050) / 2  # FVG midpoint, matches TradeAttempt's own calc
    assert e["stop"] == 1.1040  # frame bar's low, for a long
    assert e["raid_bar"] == 12
    assert e["mss_bar"] == 15

    # And the attempt should have actually been accepted (not rejected
    # for min-stop) -- confirm it's in self._attempts too.
    assert len(orch._attempts) == 1


def test_trade_candidate_ready_not_emitted_when_rejected_for_min_stop():
    """A candidate whose stop distance is too tight gets rejected --
    trade_candidate_ready must NOT fire for it (only
    fvg_rejected_min_stop should)."""
    received = []
    orch = DayOrchestrator("up", 24, 60, event_sink=received.append)

    fire_bar = 16
    frame_idx = fire_bar - 2  # same reasoning as above -- must stay in the rolling window
    # Entry and frame low are only ~1 pip apart -- well under MIN_STOP_PIPS.
    fvg_e = {"event_type": "fvg_found", "timestamp": "t", "top": 1.10502, "bottom": 1.10498, "frame_idx": frame_idx}
    mss_e = {"event_type": "mss_confirmed", "timestamp": "t", "direction": "bull", "mss_bar_index": 15}

    orch._fvg_det = FakeFVGDetector(fvg_e)
    watch = FakeWatch(fire_on_bar_index=fire_bar, mss_e=mss_e)
    orch._candidates = [{"raid_bar": 12, "watch": watch}]

    for i in range(5, 20):
        high, low = (1.10502, 1.10499) if i == frame_idx else (1.1052, 1.1048)
        orch.on_new_bar(f"t{i}", i, high, low, (high + low) / 2)

    ready_events = [e for e in received if e.get("event_type") == "trade_candidate_ready"]
    rejected_events = [e for e in received if e.get("event_type") == "fvg_rejected_min_stop"]

    assert ready_events == [], "trade_candidate_ready must not fire for a rejected candidate"
    assert len(rejected_events) == 1
    assert orch._attempts == []