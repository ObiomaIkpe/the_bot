"""
Tests for shadow_runner/scripts/backfill_narrative_aug10_sept4_2026.py
-- Piece A of the historical-reconciliation plan (misty-seeking-crescent.md).

Following this project's established convention for one-off scripts
(heal_orphans_2026_09_04.py has no test file of its own): the script's
own main() -- real ShadowRunnerConfig() from env vars, real
SessionLocal -- is deliberately not tested directly. What IS new,
script-specific logic (not already covered by
_replay_historical_day()/_decide_day(historical=True)'s own extensive
tests in test_cross_day_recovery.py) gets tested here: the date-range
computation, the idempotency skip-check, and the per-date
fetch-filter-replay loop.
"""
import datetime

from app.models import Event
from shadow_runner.runner import ShadowRunner
from shadow_runner.scripts.backfill_narrative_aug10_sept4_2026 import (
    dates_in_range,
    dates_to_replay,
    run_backfill,
)
from tests.shadow_runner.test_runner_orchestration import FakeDB, establish_trend, full_day_bars, make_config


# ---------- dates_in_range() ----------

def test_dates_in_range_is_start_inclusive_today_exclusive():
    result = dates_in_range(datetime.date(2026, 8, 10), datetime.date(2026, 8, 13))
    assert result == [datetime.date(2026, 8, 10), datetime.date(2026, 8, 11), datetime.date(2026, 8, 12)]


def test_dates_in_range_empty_when_start_is_today():
    assert dates_in_range(datetime.date(2026, 8, 10), datetime.date(2026, 8, 10)) == []


# ---------- dates_to_replay() ----------

def test_dates_to_replay_skips_dates_that_already_have_journaled_events(db_session):
    already_journaled = datetime.date(2026, 8, 15)
    db_session.add(Event(
        event_type="raid_detected",
        timestamp=datetime.datetime(2026, 8, 15, 9, 0),
        details={}, user_id=None, model="fvg",
    ))
    db_session.commit()

    candidates = [datetime.date(2026, 8, 14), already_journaled, datetime.date(2026, 8, 16)]
    result = dates_to_replay(db_session, "fvg", candidates)

    assert already_journaled not in result
    assert set(result) == {datetime.date(2026, 8, 14), datetime.date(2026, 8, 16)}


def test_dates_to_replay_all_candidates_when_nothing_journaled_yet(db_session):
    candidates = [datetime.date(2026, 8, 14), datetime.date(2026, 8, 15)]
    assert dates_to_replay(db_session, "fvg", candidates) == candidates


def test_dates_to_replay_scoped_to_the_right_model(db_session):
    """A different model's narrative must not make this model's date
    look already-covered -- same scoping get_last_event_timestamp_for_date
    itself already guarantees, proven again here at this call site."""
    db_session.add(Event(
        event_type="raid_detected",
        timestamp=datetime.datetime(2026, 8, 15, 9, 0),
        details={}, user_id=None, model="ob",
    ))
    db_session.commit()

    result = dates_to_replay(db_session, "fvg", [datetime.date(2026, 8, 15)])
    assert result == [datetime.date(2026, 8, 15)], "an 'ob' event must not count as 'fvg' already being journaled"


# ---------- run_backfill() ----------

class _StubBridge:
    """Only what run_backfill() actually calls -- get_candles_paginated()
    itself is already thoroughly tested in
    test_bridge_client_pagination.py, so this just returns a fixed,
    pre-built bar list rather than re-implementing pagination."""

    def __init__(self, bars):
        self.bars = bars

    def get_candles_paginated(self, symbol, timeframe, total_bars_needed):
        return self.bars


def test_run_backfill_replays_dates_with_bars_and_leaves_others_an_honest_gap(capsys):
    config = make_config()
    db = FakeDB([])
    tradeable_date = None

    # establish_trend() needs a real gate to compute a genuinely-tradeable
    # next_date -- build the runner first, then use its gate.
    runner = ShadowRunner(config, bridge=_StubBridge([]), session_factory=lambda: db)
    tradeable_date = establish_trend(runner.gate)
    bars_for_tradeable_date = full_day_bars(tradeable_date)
    weekend_date = tradeable_date + datetime.timedelta(days=1)  # no bars for this one at all

    runner.bridge = _StubBridge(bars_for_tradeable_date)  # no bars for weekend_date -- simulates it being beyond reach

    run_backfill(runner, db, config.symbol, config.model, [tradeable_date, weekend_date])

    out = capsys.readouterr().out
    assert f"{tradeable_date}: replaying" in out
    assert f"{weekend_date}: no bars available" in out
    assert "DONE" in out


def test_run_backfill_noop_when_nothing_to_replay(capsys):
    config = make_config()
    db = FakeDB([])
    runner = ShadowRunner(config, bridge=_StubBridge([]), session_factory=lambda: db)

    run_backfill(runner, db, config.symbol, config.model, [])

    out = capsys.readouterr().out
    assert "Nothing to do" in out
