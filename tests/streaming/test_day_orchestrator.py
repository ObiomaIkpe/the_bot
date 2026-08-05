"""
Unit tests for phase1/streaming/day_orchestrator.py -- these pin down
the priority-resolution rules that the first (failed) orchestration
attempt got wrong. The decisive validation is the full-history run
documented in PHASE1_VALIDATION.md: 603/603 trades matching the golden
master exactly on every field (date, direction, entry, stop, target,
outcome, exit price, risk pips).

The tests below inject attempts directly to isolate the selection
logic; the full wiring (swings -> raids -> MSS -> FVG -> attempts) is
covered by the full-history validation, not re-tested here.
"""
from phase1.streaming.day_orchestrator import DayOrchestrator
from phase1.streaming.trade_attempt import TradeAttempt


def _seed():
    return [(i, 1.1050 + i * 0.0001, 1.1040 + i * 0.0001) for i in range(6)]


def test_earlier_candidate_key_wins_even_if_it_filled_later_in_time():
    """The core rule the failed first attempt violated: the day's trade
    is the lexicographically smallest (raid_bar, mss_bar) among FILLED
    attempts -- not the earliest fill in wall-clock time. A raid-24
    attempt filling at bar 50 beats a raid-25 attempt filling at bar
    40, because the batch model would have found raid 24's fill first
    and never evaluated raid 25 at all."""
    orch = DayOrchestrator("up", 24, 60)
    a = TradeAttempt("long", 1.1000, 1.0990, 30, seed_bars=_seed())
    b = TradeAttempt("long", 1.1010, 1.0999, 31, seed_bars=_seed())
    b.on_new_bar("t40", 40, high=1.1012, low=1.1005)  # B fills first in time
    a.on_new_bar("t50", 50, high=1.1005, low=1.0999)  # A fills later
    orch._attempts = [
        {"key": (25, 31), "attempt": b},
        {"key": (24, 30), "attempt": a},
    ]
    trade = orch.finalize("eod", 1.1020)
    assert abs(trade["entry"] - 1.1000) < 1e-9  # A won


def test_unfilled_attempts_never_win():
    """An attempt that never filled -- even with the earliest key --
    cannot be the day's trade. A filled LOSS still takes the day over
    it, matching the batch's `if not filled: continue`."""
    orch = DayOrchestrator("up", 24, 60)
    pending = TradeAttempt("long", 1.0900, 1.0890, 28, seed_bars=_seed())
    loser = TradeAttempt("long", 1.1000, 1.0990, 30, seed_bars=_seed())
    loser.on_new_bar("t", 40, high=1.1002, low=1.0999)  # fills
    loser.on_new_bar("t", 41, high=1.0995, low=1.0985)  # stopped out
    orch._attempts = [
        {"key": (24, 28), "attempt": pending},
        {"key": (26, 30), "attempt": loser},
    ]
    trade = orch.finalize("eod", 1.1020)
    assert trade["outcome"] == "loss"


def test_open_winner_scratched_at_finalize():
    orch = DayOrchestrator("up", 24, 60)
    open_attempt = TradeAttempt("long", 1.1000, 1.0990, 30, seed_bars=_seed())
    open_attempt.on_new_bar("t", 40, high=1.1002, low=1.0999)  # fills, never closes
    orch._attempts = [{"key": (24, 30), "attempt": open_attempt}]
    trade = orch.finalize("eod", 1.1033)
    assert trade["outcome"] == "scratch"
    assert abs(trade["exit_price"] - 1.1033) < 1e-9


def test_no_filled_attempts_means_no_trade():
    orch = DayOrchestrator("up", 24, 60)
    orch._attempts = [
        {"key": (24, 28), "attempt": TradeAttempt("long", 1.09, 1.08, 28, seed_bars=_seed())}
    ]
    assert orch.finalize("eod", 1.10) is None
