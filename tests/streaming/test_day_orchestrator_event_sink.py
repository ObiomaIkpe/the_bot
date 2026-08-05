"""
Tests for the Phase 3 event_sink addition to DayOrchestrator -- separate
from test_day_orchestrator.py (which covers the pre-existing scheduling
logic and must keep passing unmodified, confirmed separately).
"""
from phase1.streaming.day_orchestrator import DayOrchestrator
from phase1.streaming.trade_attempt import TradeAttempt


def _seed():
    return [(i, 1.1050 + i * 0.0001, 1.1040 + i * 0.0001) for i in range(6)]


def test_no_sink_provided_is_a_safe_no_op():
    """Default behavior (event_sink=None) must not raise -- this is what
    every pre-Phase-3 caller does."""
    orch = DayOrchestrator("up", 24, 60)
    open_attempt = TradeAttempt("long", 1.1000, 1.0990, 30, seed_bars=_seed())
    open_attempt.on_new_bar("t", 40, high=1.1002, low=1.0999)  # fills, never closes
    orch._attempts = [{"key": (24, 30), "attempt": open_attempt}]
    trade = orch.finalize("eod", 1.1033)  # would raise if _emit() mishandled sink=None
    assert trade["outcome"] == "scratch"


def test_scratch_close_event_reaches_the_sink():
    """The one event finalize() itself can emit (the end-of-day scratch
    close) -- everything else flows through on_new_bar, tested via the
    full-history validation instead (impractical to hand-construct a
    realistic raid->MSS->FVG->fill bar sequence in a unit test)."""
    received = []
    orch = DayOrchestrator("up", 24, 60, event_sink=received.append)
    open_attempt = TradeAttempt("long", 1.1000, 1.0990, 30, seed_bars=_seed())
    open_attempt.on_new_bar("t", 40, high=1.1002, low=1.0999)  # fills, never closes
    orch._attempts = [{"key": (24, 30), "attempt": open_attempt}]
    trade = orch.finalize("eod", 1.1033)

    assert trade["outcome"] == "scratch"  # finalize()'s return value unaffected by the sink
    scratch_events = [e for e in received if e.get("event_type") == "trade_closed"]
    assert len(scratch_events) == 1
    assert scratch_events[0]["outcome"] == "scratch"
    assert abs(scratch_events[0]["exit_price"] - 1.1033) < 1e-9


def test_sink_receives_nothing_when_no_events_occur():
    """No attempts, no bars fed -- finalize() with nothing to scratch
    should leave the sink empty, not call it with garbage."""
    received = []
    orch = DayOrchestrator("up", 24, 60, event_sink=received.append)
    assert orch.finalize("eod", 1.10) is None
    assert received == []