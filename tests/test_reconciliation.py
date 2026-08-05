"""
Tests for shadow_runner.reconciliation's pure calculation functions.
No real trade data needed -- these are testable right now, against
synthetic numbers with known, hand-verified expected results. Sign
convention gets special attention since it's the easiest thing to get
backwards here (see reconciliation.py's module docstring).
"""
import datetime

from shadow_runner.reconciliation import (
    PIP,
    entry_slippage_pips,
    exit_slippage_pips,
    real_realized_r,
    summarize_slippage,
    timing_gap_seconds,
)


# ---------- entry_slippage_pips ----------

def test_entry_slippage_long_worse_fill_is_positive_unfavorable():
    # Long: intended entry 1.1050, actually filled at 1.1053 (paid MORE) -- unfavorable.
    result = entry_slippage_pips("long", entry_price=1.1050, real_fill_price=1.1053)
    assert abs(result - 3.0) < 1e-6  # 0.0003 price diff / 0.0001 PIP = 3 pips
    assert result > 0


def test_entry_slippage_long_better_fill_is_negative_favorable():
    # Long: intended 1.1050, actually filled at 1.1047 (paid LESS) -- favorable.
    result = entry_slippage_pips("long", entry_price=1.1050, real_fill_price=1.1047)
    assert result < 0


def test_entry_slippage_short_worse_fill_is_positive_unfavorable():
    # Short: intended entry (sell) 1.1050, actually filled at 1.1047 (sold for LESS) -- unfavorable.
    result = entry_slippage_pips("short", entry_price=1.1050, real_fill_price=1.1047)
    assert result > 0


def test_entry_slippage_short_better_fill_is_negative_favorable():
    # Short: intended 1.1050, actually filled at 1.1053 (sold for MORE) -- favorable.
    result = entry_slippage_pips("short", entry_price=1.1050, real_fill_price=1.1053)
    assert result < 0


def test_entry_slippage_exact_fill_is_zero():
    assert entry_slippage_pips("long", 1.1050, 1.1050) == 0.0
    assert entry_slippage_pips("short", 1.1050, 1.1050) == 0.0


# ---------- exit_slippage_pips ----------

def test_exit_slippage_long_worse_close_is_positive_unfavorable():
    # Long: simulation expected exit at 1.1100, real close was 1.1090 (LOWER) -- unfavorable.
    result = exit_slippage_pips("long", simulated_exit_price=1.1100, real_close_price=1.1090)
    assert result > 0


def test_exit_slippage_long_better_close_is_negative_favorable():
    result = exit_slippage_pips("long", simulated_exit_price=1.1100, real_close_price=1.1110)
    assert result < 0


def test_exit_slippage_short_worse_close_is_positive_unfavorable():
    # Short: simulation expected exit at 1.1000, real close was 1.1010 (HIGHER) -- unfavorable.
    result = exit_slippage_pips("short", simulated_exit_price=1.1000, real_close_price=1.1010)
    assert result > 0


def test_exit_slippage_short_better_close_is_negative_favorable():
    result = exit_slippage_pips("short", simulated_exit_price=1.1000, real_close_price=1.0990)
    assert result < 0


# ---------- real_realized_r ----------

def test_real_realized_r_long_matches_hand_calculation():
    # Long: fill 1.1050, stop 1.1040 (risk = 10 pips), close 1.1070 (gain = 20 pips) -> R = 2.0
    r = real_realized_r("long", real_fill_price=1.1050, stop_price=1.1040, real_close_price=1.1070)
    assert abs(r - 2.0) < 1e-9


def test_real_realized_r_short_matches_hand_calculation():
    # Short: fill 1.1050, stop 1.1060 (risk = 10 pips), close 1.1030 (gain = 20 pips) -> R = 2.0
    r = real_realized_r("short", real_fill_price=1.1050, stop_price=1.1060, real_close_price=1.1030)
    assert abs(r - 2.0) < 1e-9


def test_real_realized_r_negative_for_a_loss():
    # Long: fill 1.1050, stop 1.1040, closed AT the stop -> R = -1.0
    r = real_realized_r("long", real_fill_price=1.1050, stop_price=1.1040, real_close_price=1.1040)
    assert abs(r - (-1.0)) < 1e-9


def test_real_realized_r_returns_none_for_zero_risk():
    r = real_realized_r("long", real_fill_price=1.1050, stop_price=1.1050, real_close_price=1.1070)
    assert r is None


# ---------- timing_gap_seconds ----------

def test_timing_gap_seconds_normal_positive_case():
    bar_close = datetime.datetime(2026, 8, 4, 9, 40, 0)
    real_fill = datetime.datetime(2026, 8, 4, 9, 40, 37)  # 37s after bar close
    gap = timing_gap_seconds(bar_close, real_fill)
    assert gap == 37.0


def test_timing_gap_seconds_negative_case_not_raised_just_returned():
    """A negative gap (fill somehow before the bar even closed) should
    be RETURNED, not raised -- report.py is responsible for flagging
    it, this function just does the arithmetic honestly."""
    bar_close = datetime.datetime(2026, 8, 4, 9, 40, 0)
    real_fill = datetime.datetime(2026, 8, 4, 9, 39, 0)  # somehow BEFORE bar close
    gap = timing_gap_seconds(bar_close, real_fill)
    assert gap == -60.0


# ---------- summarize_slippage ----------

def test_summarize_slippage_empty_list():
    result = summarize_slippage([])
    assert result == {"count": 0, "mean": None, "median": None, "min": None, "max": None}


def test_summarize_slippage_single_value():
    result = summarize_slippage([5.0])
    assert result["count"] == 1
    assert result["mean"] == 5.0
    assert result["median"] == 5.0
    assert result["min"] == 5.0
    assert result["max"] == 5.0


def test_summarize_slippage_odd_count_median():
    result = summarize_slippage([1.0, 5.0, 3.0])
    assert result["median"] == 3.0  # sorted: 1,3,5 -> middle is 3


def test_summarize_slippage_even_count_median():
    result = summarize_slippage([1.0, 2.0, 3.0, 4.0])
    assert result["median"] == 2.5  # sorted: 1,2,3,4 -> avg of 2 and 3


def test_summarize_slippage_mean_min_max():
    result = summarize_slippage([-2.0, 0.0, 4.0])
    assert result["mean"] == pytest_approx((-2.0 + 0.0 + 4.0) / 3)
    assert result["min"] == -2.0
    assert result["max"] == 4.0


def pytest_approx(x):
    """Tiny local helper -- avoids a pytest dependency for one float
    comparison in an environment where pytest itself isn't installed
    (see this project's other test files for the same constraint)."""
    return x