"""
Tests for the cross-day recovery gap fix (2026-09-02) -- see
PENDING_ITEMS.md's "Real bugs found 2026-09-02" and
PHASE3_VALIDATION.md's correction section for the incident this exists
to catch: shadow_runner had no recovery for anything before "today",
and a real ~20-hour outage spanning a day boundary meant a full day's
real trading activity was silently never journaled, invisible to the
app for about a week.

Three pieces, tested here:
1. get_last_event_timestamp() (persistence.py) -- the gap-detection
   query, unscoped by date unlike the existing per-date version.
2. check_for_orphaned_positions()/_heal_orphan() (orphan_recovery.py)
   -- the still-open-position self-heal.
3. _replay_historical_day()/_decide_day(historical=True) (runner.py)
   -- journal-only narrative reconstruction. The single most important
   test in this file is test_historical_replay_never_places_a_real_order
   -- proving it's structurally impossible for a historical replay to
   place a real order, not just that it doesn't happen to in these
   fixtures.
"""
import datetime

import shadow_runner.runner as runner_module
from app.models import Event, User
from shadow_runner.orphan_recovery import check_for_orphaned_positions
from shadow_runner.persistence import get_last_event_timestamp
from shadow_runner.runner import ShadowRunner
from tests.shadow_runner.test_order_manager import FakeBridge as OrderFakeBridge
from tests.shadow_runner.test_runner_orchestration import FakeDB, establish_trend, full_day_bars, make_config
from tests.streaming.test_trade_candidate_ready import FakeFVGDetector, FakeWatch


# ---------- piece 0: get_last_event_timestamp() ----------

def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_get_last_event_timestamp_returns_none_when_nothing_journaled(db_session):
    user = _make_user(db_session, "gap_a@example.com")
    assert get_last_event_timestamp(db_session, str(user.user_id), "fvg") is None


def test_get_last_event_timestamp_finds_the_most_recent_across_days(db_session):
    user = _make_user(db_session, "gap_b@example.com")
    older = Event(
        event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 27, 9, 0),
        details={}, user_id=user.user_id, model="fvg",
    )
    newer = Event(
        event_type="mss_confirmed", timestamp=datetime.datetime(2026, 8, 28, 12, 21, 43),
        details={}, user_id=user.user_id, model="fvg",
    )
    db_session.add_all([older, newer])
    db_session.commit()

    result = get_last_event_timestamp(db_session, str(user.user_id), "fvg")
    assert result == newer.timestamp


def test_get_last_event_timestamp_excludes_bootstrap_marker(db_session):
    """Same exclusion as get_last_event_timestamp_for_date(), same
    reasoning -- the bootstrap marker is bookkeeping, not real trading
    activity, and would otherwise mask a real gap sitting right behind
    it."""
    user = _make_user(db_session, "gap_c@example.com")
    real_event = Event(
        event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 20, 9, 0),
        details={}, user_id=user.user_id, model="fvg",
    )
    marker = Event(
        event_type="trend_history_bootstrapped", timestamp=datetime.datetime(2026, 8, 29, 8, 0),
        details={}, user_id=user.user_id, model="fvg",
    )
    db_session.add_all([real_event, marker])
    db_session.commit()

    result = get_last_event_timestamp(db_session, str(user.user_id), "fvg")
    assert result == real_event.timestamp, "the marker (even though newer) must not count as real activity"


def test_get_last_event_timestamp_scoped_to_user_and_model(db_session):
    user_a = _make_user(db_session, "gap_d1@example.com")
    user_b = _make_user(db_session, "gap_d2@example.com")
    e_a = Event(event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 20, 9, 0), details={}, user_id=user_a.user_id, model="fvg")
    e_b = Event(event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 25, 9, 0), details={}, user_id=user_b.user_id, model="fvg")
    db_session.add_all([e_a, e_b])
    db_session.commit()

    assert get_last_event_timestamp(db_session, str(user_a.user_id), "fvg") == e_a.timestamp
    assert get_last_event_timestamp(db_session, str(user_b.user_id), "fvg") == e_b.timestamp


# ---------- piece 2A: check_for_orphaned_positions() / _heal_orphan() ----------

class OrphanFakeBridge(OrderFakeBridge):
    """test_order_manager's FakeBridge already tracks positions/
    modified/get_candles-adjacent state -- extend with a controllable
    get_candles() for target computation."""

    def __init__(self):
        super().__init__()
        self.candles_response = []

    def get_candles(self, symbol, timeframe, count):
        return self.candles_response


def _make_bar(dt, high, low):
    return {"time_utc": dt, "time_ny": dt, "high": high, "low": low, "open": (high + low) / 2, "close": (high + low) / 2}


def test_no_orphans_when_every_open_position_is_known(db_session):
    user = _make_user(db_session, "orphan_a@example.com")
    bridge = OrphanFakeBridge()
    bridge._positions[555] = {
        "ticket": 555, "symbol": "EURUSDm", "direction": "long", "volume": 0.1,
        "open_price": 1.1050, "current_price": 1.1050, "stop_loss": 1.1040, "take_profit": 0.0,
        "profit": 0.0, "magic": 900001, "time_utc": datetime.datetime(2026, 9, 1, 10, 0),
        "time_ny": datetime.datetime(2026, 9, 1, 6, 0),
    }
    from app.models import Trade
    known = Trade(
        user_id=user.user_id, model="fvg", is_shadow=False, direction="long",
        entry_price=1.1050, stop_price=1.1040, target_price=1.1080,
        real_position_ticket=555, real_status="open",
        entry_time_utc=datetime.datetime(2026, 9, 1, 10, 0),
        entry_time_ny=datetime.datetime(2026, 9, 1, 6, 0),
        risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(known)
    db_session.commit()

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 9, 2, 8, 0), events.append,
    )
    assert results == []
    assert events == []
    assert bridge.modified == []


def test_orphan_found_and_healed_attaches_target(db_session):
    user = _make_user(db_session, "orphan_b@example.com")
    bridge = OrphanFakeBridge()
    fill_time = datetime.datetime(2026, 8, 27, 15, 59, 22)
    bridge._positions[3147397683] = {
        "ticket": 3147397683, "symbol": "EURUSDm", "direction": "long", "volume": 7.55,
        "open_price": 1.16460, "current_price": 1.16460, "stop_loss": 1.16395, "take_profit": 0.0,
        "profit": 0.0, "magic": 900001, "time_utc": fill_time, "time_ny": fill_time,
    }
    # 6 bars strictly before the fill -- highest high is the 3rd one.
    bridge.candles_response = [
        _make_bar(fill_time - datetime.timedelta(minutes=30), 1.1655, 1.1645),
        _make_bar(fill_time - datetime.timedelta(minutes=25), 1.1660, 1.1648),
        _make_bar(fill_time - datetime.timedelta(minutes=20), 1.1680, 1.1652),  # the extreme bar
        _make_bar(fill_time - datetime.timedelta(minutes=15), 1.1665, 1.1650),
        _make_bar(fill_time - datetime.timedelta(minutes=10), 1.1658, 1.1649),
        _make_bar(fill_time - datetime.timedelta(minutes=5), 1.1652, 1.1647),
    ]

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 8, 28, 12, 0), events.append,
    )

    assert results == [{"ticket": 3147397683, "healed": True}]
    assert bridge.modified == [(3147397683, (1.1680 + 1.1652) / 2)]
    recovered = [e for e in events if e["event_type"] == "orphan_position_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["ticket"] == 3147397683
    assert recovered[0]["target"] == (1.1680 + 1.1652) / 2


def test_orphan_heal_failure_emits_distinct_check_name(db_session):
    user = _make_user(db_session, "orphan_c@example.com")
    bridge = OrphanFakeBridge()
    fill_time = datetime.datetime(2026, 8, 27, 15, 59, 22)
    bridge._positions[999] = {
        "ticket": 999, "symbol": "EURUSDm", "direction": "long", "volume": 1.0,
        "open_price": 1.1050, "current_price": 1.1050, "stop_loss": 1.1040, "take_profit": 0.0,
        "profit": 0.0, "magic": 900001, "time_utc": fill_time, "time_ny": fill_time,
    }
    bridge.candles_response = [
        _make_bar(fill_time - datetime.timedelta(minutes=m), 1.1060, 1.1040) for m in (30, 25, 20, 15, 10, 5)
    ]
    def modify_position(ticket, take_profit):
        raise Exception("simulated bridge failure")
    bridge.modify_position = modify_position

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 8, 28, 12, 0), events.append,
    )

    assert results == [{"ticket": 999, "healed": False}]
    check_failures = [e for e in events if e["event_type"] == "safety_check_failed"]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "orphan_position_heal_failed"


def test_positions_fetch_failure_returns_empty_and_logs_check_failure(db_session):
    user = _make_user(db_session, "orphan_d@example.com")
    bridge = OrphanFakeBridge()

    def get_positions(magic):
        raise Exception("simulated bridge failure")
    bridge.get_positions = get_positions

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 8, 28, 12, 0), events.append,
    )
    assert results == []
    check_failures = [e for e in events if e["event_type"] == "safety_check_failed"]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "orphan_position_check_bridge_call"


# ---------- piece 3: _replay_historical_day() / _decide_day(historical=True) ----------

def test_historical_decide_day_never_constructs_an_order_manager():
    """Structural test: a tradeable historical day must never get an
    OrderManager, regardless of whether a real candidate ever fires --
    this is what makes it impossible for combined_sink to ever call
    on_trade_candidate_ready() during historical replay."""
    config = make_config()
    db = FakeDB([])
    bridge = OrderFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)
    runner.model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}

    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)

    cd = runner._replay_historical_day(next_date, bars)

    assert cd.tradeable is True, "sanity check -- this day should have been judged tradeable"
    assert cd.order_manager is None, "historical replay must never construct a real OrderManager"
    assert bridge.placed == []


def test_historical_replay_never_places_a_real_order():
    """The single most important test in this file. Same technique as
    test_order_manager_wiring.py's positive-case proof (a real
    trade_candidate_ready DOES reach OrderManager during live
    _process_bar) -- inverted here to prove the SAME real candidate
    firing during a HISTORICAL replay never reaches
    on_trade_candidate_ready(), because cd.order_manager is None."""
    config = make_config()
    db = FakeDB([])
    bridge = OrderFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)
    runner.model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}

    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)
    bars_before_10am = [b for b in bars if b["time_ny"].time() < ten_am]
    bars_from_10am_on = [b for b in bars if b["time_ny"].time() >= ten_am]

    # Replay up through (and including) the bar that triggers _decide_day
    # -- mirrors _replay_historical_day()'s own loop shape, stopped
    # right after the decide point so a fake candidate can be injected,
    # same technique test_order_manager_wiring.py uses at the live
    # _process_bar level.
    cd = runner._replay_historical_day(next_date, bars_before_10am + [bars_from_10am_on[0]])
    assert cd.order_manager is None
    assert cd.orchestrator is not None

    fire_bar = len(cd.bars)
    frame_idx = fire_bar - 2
    fvg_e = {"event_type": "fvg_found", "timestamp": "t", "top": 1.1060, "bottom": 1.1050, "frame_idx": frame_idx}
    mss_e = {"event_type": "mss_confirmed", "timestamp": "t", "direction": "bull", "mss_bar_index": fire_bar - 1}
    cd.orchestrator._fvg_det = FakeFVGDetector(fvg_e)
    watch = FakeWatch(fire_on_bar_index=fire_bar, mss_e=mss_e)
    cd.orchestrator._candidates = [{"raid_bar": fire_bar - 3, "watch": watch}]

    next_bar = bars_from_10am_on[1]
    cd.bars.append(next_bar)
    idx = len(cd.bars) - 1
    cd.orchestrator.on_new_bar(next_bar["time_ny"], idx, next_bar["high"], next_bar["low"], next_bar["close"])

    candidate_events = [e for e in cd.todays_events if e.get("event_type") == "trade_candidate_ready"]
    assert len(candidate_events) == 1, "sanity check -- the real candidate must have actually fired"
    assert bridge.placed == [], "a real trade_candidate_ready event during historical replay must NEVER place a real order"


class GapFakeBridge(OrderFakeBridge):
    def __init__(self):
        super().__init__()
        self.candles_response = []

    def get_candles(self, symbol, timeframe, count):
        return self.candles_response


class GapFakeDB(FakeDB):
    """FakeDB's own FakeQuery has no .all() -- fine for everything it
    was originally built for (UserSettings/Trade-equity .first()/.one()
    lookups), but check_for_orphaned_positions() needs a real
    Trade-query .all() too. No orphans exist in these fixtures (bridge
    positions are empty), so an empty list is the correct answer either
    way -- this just needs to not crash."""

    def query(self, model_cls):
        from app.models import Trade
        if model_cls is Trade:
            class _EmptyTradeQuery:
                def filter(self, *a, **k):
                    return self

                def all(self):
                    return []
            return _EmptyTradeQuery()
        return super().query(model_cls)


def test_recover_cross_day_gap_noop_when_nothing_actually_missed(monkeypatch):
    """last_known_date is literally yesterday and today just hasn't
    started yet -- nothing strictly between them, so this should do
    nothing at all (no alert, no bridge calls)."""
    alerts = []
    monkeypatch.setattr(runner_module, "send_telegram_alert", lambda text: alerts.append(text))

    config = make_config()
    shared_writes = []
    bridge = GapFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: FakeDB(shared_writes))
    runner.model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}

    today = datetime.date(2026, 9, 2)
    yesterday = datetime.date(2026, 9, 1)
    runner._recover_cross_day_gap(yesterday, today, datetime.datetime(2026, 9, 2, 8, 0))

    assert alerts == []
    assert shared_writes == []


def test_recover_cross_day_gap_full_flow(monkeypatch):
    """A real multi-day gap: alert fires naming the missed dates, the
    orphan check runs (no orphans in this fixture), and the missed
    day's narrative gets replayed and journaled."""
    alerts = []
    monkeypatch.setattr(runner_module, "send_telegram_alert", lambda text: alerts.append(text))

    config = make_config()
    shared_writes = []
    bridge = GapFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: GapFakeDB(shared_writes))
    runner.model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}

    missed_date = establish_trend(runner.gate)
    bridge.candles_response = full_day_bars(missed_date)
    today = missed_date + datetime.timedelta(days=1)
    last_known_date = missed_date - datetime.timedelta(days=1)

    runner._recover_cross_day_gap(last_known_date, today, datetime.datetime.combine(today, datetime.time(8, 0)))

    assert len(alerts) == 1
    assert str(missed_date) in alerts[0]
    assert str(last_known_date) in alerts[0]

    trend_events = [w for w in shared_writes if getattr(w, "event_type", "") == "day_trend_determined"]
    assert len(trend_events) == 1, "the missed day's narrative should have been reconstructed and journaled"


def test_historical_replay_skipped_day_still_journals_day_skip_event():
    """A weekend/FOMC day resolves to its normal day_skipped_* event
    during historical replay, same as live -- no separate holiday-
    filtering logic needed, DaySelectionGate's own machinery handles it."""
    config = make_config()
    shared_writes = []
    db = FakeDB(shared_writes)
    bridge = OrderFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)
    runner.model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}

    # No trend established -- gate_for_day() will resolve to a skip
    # (no_trend), exercising the non-tradeable branch of _decide_day.
    next_date = datetime.datetime.now().date() - datetime.timedelta(days=1)
    bars = full_day_bars(next_date)

    cd = runner._replay_historical_day(next_date, bars)

    assert cd.tradeable is False
    assert cd.order_manager is None
    skip_events = [w for w in shared_writes if getattr(w, "event_type", "").startswith("day_skipped_")]
    assert len(skip_events) == 1
