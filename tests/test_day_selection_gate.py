"""
Unit tests for phase1/streaming/day_selection_gate.py -- pure logic,
synthetic data. Mirrors the style of test_daily_swing_detector.py.

Not a substitute for validating against the actual batch script's
day-by-day decisions (day_skipped_fomc / day_skipped_no_trend /
day_skipped_insufficient_bars / day_skipped_no_session_start counts from
PHASE1_VALIDATION.md) -- that comparison should happen once the shadow
runner is live, same spirit as the golden-master check.
"""
import datetime

from phase1.streaming.day_selection_gate import (
    DaySelectionGate,
    FOMC_DATES,
    STALENESS_WARNING_DAYS,
)


def make_bar(date, hour, minute, o=1.10, h=1.101, l=1.099, c=1.1005):
    return {
        "time_ny": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "open": o, "high": h, "low": l, "close": c,
    }


def session_bars(date, start_hour=5, end_hour=17, step_min=5):
    bars = []
    t = datetime.datetime.combine(date, datetime.time(start_hour, 0))
    end = datetime.datetime.combine(date, datetime.time(end_hour, 0))
    while t <= end:
        bars.append(make_bar(date, t.hour, t.minute))
        t += datetime.timedelta(minutes=step_min)
    return bars


def establish_up_trend(gate: DaySelectionGate):
    """Feed 9 closed days engineered so exactly two swing highs and two
    swing lows get confirmed -- at day index 2 and day index 6 -- with
    day 6's high/low both higher than day 2's (a real higher-high +
    higher-low structure, not a monotonic run, which never produces a
    swing point: with pivot_n=2, a symmetric window's max/min always
    sits at the window's edge for monotonic data, never at its center)."""
    highs = [1.10, 1.11, 1.15, 1.11, 1.10, 1.12, 1.20, 1.12, 1.10]
    lows = [1.05, 1.04, 1.00, 1.04, 1.09, 1.08, 1.03, 1.09, 1.10]
    d = datetime.date(2026, 7, 1)
    for i in range(9):
        gate.on_day_closed(d, highs[i], lows[i])
        d += datetime.timedelta(days=1)
    return d  # next date, ready for gate_for_day


def test_fomc_date_skipped_before_anything_else():
    gate = DaySelectionGate()
    fomc_date = next(iter(FOMC_DATES))
    result = gate.gate_for_day(fomc_date, session_bars(fomc_date))
    assert result.tradeable is False
    assert result.skip_reason == "fomc"


def test_no_trend_when_insufficient_swing_history():
    gate = DaySelectionGate()
    # No days fed at all -- can't have 2 confirmed highs/lows yet.
    result = gate.gate_for_day(datetime.date(2026, 8, 3), session_bars(datetime.date(2026, 8, 3)))
    assert result.tradeable is False
    assert result.skip_reason == "no_trend"


def test_up_trend_produces_tradeable_result_with_session_indices():
    gate = DaySelectionGate()
    next_date = establish_up_trend(gate)
    bars = session_bars(next_date)
    result = gate.gate_for_day(next_date, bars)
    assert result.tradeable is True
    assert result.trend == "up"
    # session_start (7:00) should land at bar index 24 (5:00 to 7:00 is
    # 2 hours = 24 five-minute bars, 0-indexed start of window at 5:00)
    assert bars[result.session_start_idx]["time_ny"].hour == 7
    assert bars[result.session_start_idx]["time_ny"].minute == 0
    assert bars[result.session_end_idx]["time_ny"].hour == 10
    assert bars[result.session_end_idx]["time_ny"].minute == 0


def test_insufficient_bars_skips_even_with_valid_trend():
    gate = DaySelectionGate()
    next_date = establish_up_trend(gate)
    too_few_bars = session_bars(next_date)[:5]  # fewer than MIN_SESSION_BARS (12)
    result = gate.gate_for_day(next_date, too_few_bars)
    assert result.tradeable is False
    assert result.skip_reason == "insufficient_bars"


def test_no_session_start_when_bars_end_before_7am():
    gate = DaySelectionGate()
    next_date = establish_up_trend(gate)
    early_bars = session_bars(next_date, start_hour=5, end_hour=6, step_min=5)
    result = gate.gate_for_day(next_date, early_bars)
    assert result.tradeable is False
    assert result.skip_reason == "no_session_start"


def test_fomc_dates_include_all_eight_2026_meetings():
    """Regression guard: the four dates added during Phase 3 (Jul 29,
    Sep 16, Oct 28, Dec 9) alongside the four already present from the
    original golden-master list."""
    expected_2026 = {
        datetime.date(2026, 1, 28), datetime.date(2026, 3, 18),
        datetime.date(2026, 4, 29), datetime.date(2026, 6, 17),
        datetime.date(2026, 7, 29), datetime.date(2026, 9, 16),
        datetime.date(2026, 10, 28), datetime.date(2026, 12, 9),
    }
    present_2026 = {d for d in FOMC_DATES if d.year == 2026}
    assert present_2026 == expected_2026


def test_staleness_warning_fires_near_end_of_known_calendar(caplog):
    gate = DaySelectionGate()
    last_known = max(FOMC_DATES)
    near_edge_date = last_known - datetime.timedelta(days=STALENESS_WARNING_DAYS - 1)
    import logging
    with caplog.at_level(logging.WARNING, logger="phase1.streaming.day_selection_gate"):
        gate.gate_for_day(near_edge_date, [])
    assert any("runway" in rec.message for rec in caplog.records)