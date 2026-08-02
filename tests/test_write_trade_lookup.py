"""
Directly tests ShadowRunner._write_trade()'s logic for recovering a
winning trade's entry/exit timestamps from the day's already-journaled
events -- this is the part most likely to silently pick the wrong bar,
since finalize()'s returned trade dict doesn't carry timestamps itself.
"""
import datetime

import shadow_runner.persistence as persistence
from shadow_runner.day_state import CurrentDay
from shadow_runner.runner import ShadowRunner
from tests.test_runner_orchestration import FakeDB, make_config


class FakeBridge:
    def account_info(self):
        return {"balance": 50000.0}


def make_bar(date, hour, minute, close=1.1000):
    return {
        "time_utc": datetime.datetime.combine(date, datetime.time(hour, minute)) - datetime.timedelta(hours=4),
        "time_ny": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "open": close, "high": close + 0.0005, "low": close - 0.0005, "close": close,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


def test_write_trade_recovers_correct_entry_and_exit_bars():
    date = datetime.date(2026, 8, 3)
    cd = CurrentDay(date)
    # Bars at index 0..4 -- fill happens at index 2, close at index 4.
    cd.bars = [make_bar(date, 7, m) for m in (0, 5, 10, 15, 20)]
    cd.trend = "up"

    cd.todays_events = [
        {"event_type": "raid_detected", "timestamp": cd.bars[0]["time_ny"]},
        {
            "event_type": "order_filled",
            "timestamp": cd.bars[2]["time_ny"],
            "direction": "long",
            "entry": 1.10500,
            "stop": 1.10400,
            "target": 1.10700,
            "fill_bar_index": 2,
        },
        {
            "event_type": "trade_closed",
            "timestamp": cd.bars[4]["time_ny"],
            "direction": "long",
            "outcome": "win",
            "exit_price": 1.10700,
        },
    ]

    trade = {
        "direction": "long", "entry": 1.10500, "stop": 1.10400, "target": 1.10700,
        "risk_pips": 10.0, "outcome": "win", "exit_price": 1.10700,
    }

    written = {}

    def fake_write_trade(db, trade_arg, entry_time_utc, entry_time_ny, exit_time_utc, **kw):
        written["entry_time_utc"] = entry_time_utc
        written["entry_time_ny"] = entry_time_ny
        written["exit_time_utc"] = exit_time_utc

    orig = persistence.write_trade
    import shadow_runner.runner as runner_module
    runner_module.write_trade = fake_write_trade
    try:
        config = make_config()
        runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: FakeDB([]))
        runner._write_trade(cd, trade)
    finally:
        runner_module.write_trade = orig

    assert written["entry_time_ny"] == cd.bars[2]["time_ny"], "picked wrong entry bar"
    assert written["entry_time_utc"] == cd.bars[2]["time_utc"]
    assert written["exit_time_utc"] == cd.bars[4]["time_utc"], "picked wrong exit bar"


def test_write_trade_scratch_always_uses_last_bar_regardless_of_events():
    """Scratches close exactly at end of day by construction -- no
    trade_closed event lookup needed or attempted for this case."""
    date = datetime.date(2026, 8, 3)
    cd = CurrentDay(date)
    cd.bars = [make_bar(date, 7, m) for m in (0, 5, 10)]
    cd.trend = "up"
    cd.todays_events = [
        {
            "event_type": "order_filled", "timestamp": cd.bars[0]["time_ny"],
            "direction": "short", "entry": 1.10000, "stop": 1.10100, "target": 1.09800,
            "fill_bar_index": 0,
        },
        # deliberately NO trade_closed event -- still open, hence scratch
    ]
    trade = {
        "direction": "short", "entry": 1.10000, "stop": 1.10100, "target": 1.09800,
        "risk_pips": 10.0, "outcome": "scratch", "exit_price": 1.10050,
    }

    written = {}

    def fake_write_trade(db, trade_arg, entry_time_utc, entry_time_ny, exit_time_utc, **kw):
        written["exit_time_utc"] = exit_time_utc

    import shadow_runner.runner as runner_module
    orig = persistence.write_trade
    runner_module.write_trade = fake_write_trade
    try:
        config = make_config()
        runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: FakeDB([]))
        runner._write_trade(cd, trade)
    finally:
        runner_module.write_trade = orig

    assert written["exit_time_utc"] == cd.bars[-1]["time_utc"]  # last bar = EOD