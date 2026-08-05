"""
Unit tests for phase1/streaming/trade_attempt.py -- pure logic, no
database needed. Covers every distinct path: min-stop rejection, fill
with dynamic target computation, win/loss/scratch outcomes, same-bar
fill+close, the one-attempt-only rule (invalid target abandons
permanently, no retry at a later touch), insufficient seed history, and
the short-direction mirror.

Full-history validation is documented in PHASE1_VALIDATION.md as part
of the complete pipeline run (603/603 exact match).
"""
import pytest

from phase1.streaming.trade_attempt import TradeAttempt


def close(a, b, tol=1e-9):
    return abs(a - b) < tol


SEED = [
    (4, 1.1050, 1.1040), (5, 1.1060, 1.1050), (6, 1.1055, 1.1045),
    (7, 1.1058, 1.1048), (8, 1.1052, 1.1042), (9, 1.1049, 1.1039),
]
# highest high in SEED is 1.1060 (with low 1.1050) -> long target = 1.1055


def test_min_stop_rejection_is_immediate():
    attempt = TradeAttempt("long", entry_price=1.1000, stop=1.09995, fvg_bar_index=10)
    assert attempt.status == "rejected_min_stop"
    assert not attempt.is_active()


def test_sufficient_risk_stays_pending():
    attempt = TradeAttempt("long", entry_price=1.1000, stop=1.0990, fvg_bar_index=10)
    assert attempt.status == "pending"


def test_fill_computes_dynamic_target_from_seeded_window():
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    events = attempt.on_new_bar("t", 11, high=1.1005, low=1.0999)
    assert len(events) == 1
    assert events[0]["event_type"] == "order_filled"
    assert close(events[0]["target"], 1.1055)


def test_target_hit_is_a_win():
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    attempt.on_new_bar("t", 11, high=1.1005, low=1.0999)
    events = attempt.on_new_bar("t", 12, high=1.1060, low=1.1010)
    assert events[0]["outcome"] == "win"
    assert close(events[0]["exit_price"], 1.1055)


def test_stop_hit_is_a_loss():
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    attempt.on_new_bar("t", 11, high=1.1005, low=1.0999)
    events = attempt.on_new_bar("t", 12, high=1.1010, low=1.0985)
    assert events[0]["outcome"] == "loss"
    assert close(events[0]["exit_price"], 1.0990)


def test_same_bar_fill_and_close_produces_both_events_in_order():
    """The batch outcome loop starts AT the fill bar (q = p, inclusive) --
    a single bar can both fill the order and immediately close it."""
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    events = attempt.on_new_bar("t", 11, high=1.1060, low=1.0985)
    assert [e["event_type"] for e in events] == ["order_filled", "trade_closed"]


def test_invalid_target_abandons_permanently_no_retry():
    """The batch model breaks out of the fill search entirely on an
    invalid target -- it never re-evaluates a later touch of the same
    FVG. One attempt only."""
    bad_seed = [(4, 1.0990, 1.0980)] * 6  # target would be 1.0985 <= entry
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=bad_seed)
    assert attempt.on_new_bar("t", 11, high=1.1005, low=1.0999) == []
    assert attempt.status == "abandoned"
    assert attempt.on_new_bar("t", 12, high=1.1010, low=1.0995) == []
    assert attempt.status == "abandoned"


def test_insufficient_seed_history_abandons():
    """Matches the batch's `if p < 6: break` guard."""
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=[(9, 1.10, 1.09)])
    assert attempt.on_new_bar("t", 11, high=1.1005, low=1.0999) == []
    assert attempt.status == "abandoned"


def test_open_trade_scratches_at_end_of_day():
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    attempt.on_new_bar("t", 11, high=1.1005, low=1.0999)
    event = attempt.close_as_scratch("eod", final_close=1.1020)
    assert event["outcome"] == "scratch"
    assert close(event["exit_price"], 1.1020)
    assert attempt.status == "closed"


def test_scratch_on_unfilled_attempt_returns_none():
    attempt = TradeAttempt("long", 1.1000, 1.0990, 10, seed_bars=SEED)
    assert attempt.close_as_scratch("eod", 1.1020) is None


def test_short_direction_mirrors():
    short_seed = [
        (4, 1.0960, 1.0950), (5, 1.0955, 1.0940), (6, 1.0950, 1.0945),
        (7, 1.0948, 1.0942), (8, 1.0952, 1.0939), (9, 1.0949, 1.0938),
    ]
    # lowest low 1.0938 (with high 1.0949) -> short target 1.09435
    attempt = TradeAttempt("short", 1.1000, 1.1010, 10, seed_bars=short_seed)
    events = attempt.on_new_bar("t", 11, high=1.1001, low=1.0995)
    assert events[0]["direction"] == "short"
    assert close(events[0]["target"], 1.09435)

    # loss path: high >= stop
    attempt2 = TradeAttempt("short", 1.1000, 1.1010, 10, seed_bars=short_seed)
    attempt2.on_new_bar("t", 11, high=1.1001, low=1.0995)
    assert attempt2.on_new_bar("t", 12, high=1.1015, low=1.0998)[0]["outcome"] == "loss"

    # win path: low <= target
    attempt3 = TradeAttempt("short", 1.1000, 1.1010, 10, seed_bars=short_seed)
    attempt3.on_new_bar("t", 11, high=1.1001, low=1.0995)
    assert attempt3.on_new_bar("t", 12, high=1.1005, low=1.0940)[0]["outcome"] == "win"
