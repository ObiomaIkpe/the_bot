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
    assert get_last_event_timestamp(db_session, "fvg") is None


def test_get_last_event_timestamp_finds_the_most_recent_across_days(db_session):
    older = Event(
        event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 27, 9, 0),
        details={}, user_id=None, model="fvg",
    )
    newer = Event(
        event_type="mss_confirmed", timestamp=datetime.datetime(2026, 8, 28, 12, 21, 43),
        details={}, user_id=None, model="fvg",
    )
    db_session.add_all([older, newer])
    db_session.commit()

    result = get_last_event_timestamp(db_session, "fvg")
    assert result == newer.timestamp


def test_get_last_event_timestamp_excludes_bootstrap_marker(db_session):
    """Same exclusion as get_last_event_timestamp_for_date(), same
    reasoning -- the bootstrap marker is bookkeeping, not real trading
    activity, and would otherwise mask a real gap sitting right behind
    it."""
    real_event = Event(
        event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 20, 9, 0),
        details={}, user_id=None, model="fvg",
    )
    marker = Event(
        event_type="trend_history_bootstrapped", timestamp=datetime.datetime(2026, 8, 29, 8, 0),
        details={}, user_id=None, model="fvg",
    )
    db_session.add_all([real_event, marker])
    db_session.commit()

    result = get_last_event_timestamp(db_session, "fvg")
    assert result == real_event.timestamp, "the marker (even though newer) must not count as real activity"


def test_get_last_event_timestamp_scoped_to_model(db_session):
    """Multi-user fan-out, piece 1.5: get_last_event_timestamp() dropped
    its user_id parameter entirely -- narrative events are shared,
    model-level state now (MULTI_USER_FANOUT_PLAN.md section 5), not
    personal to any one subscriber; there's no longer a "which user's
    raid_detected" question to ask, since a given model+timestamp has
    exactly one shared raid_detected, not one per subscriber. What still
    needs proving is that it's scoped to the right MODEL -- a different
    model's narrative must never leak into this one's gap check."""
    e_fvg = Event(
        event_type="raid_detected", timestamp=datetime.datetime(2026, 8, 20, 9, 0),
        details={}, user_id=None, model="fvg",
    )
    db_session.add(e_fvg)
    db_session.commit()

    assert get_last_event_timestamp(db_session, "fvg") == e_fvg.timestamp
    assert get_last_event_timestamp(db_session, "some_other_model_no_events_for") is None


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
        # Real bug found 2026-09-04: BridgeClient.get_positions() returns
        # time_utc/time_ny as raw ISO strings off the wire, never parsed
        # into datetimes (unlike get_candles(), which does parse them) --
        # a prior version of this fixture used real datetime objects here,
        # which is exactly why check_for_orphaned_positions()'s type-
        # mismatch bug (comparing a parsed bar datetime against this raw
        # string) went uncaught until it hit a real orphan in production.
        "profit": 0.0, "magic": 900001, "time_utc": datetime.datetime(2026, 9, 1, 10, 0).isoformat(),
        "time_ny": datetime.datetime(2026, 9, 1, 6, 0).isoformat(),
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
        datetime.datetime(2026, 9, 2, 8, 0), events.append, risk_pct=0.01,
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
        # Raw ISO string, matching BridgeClient.get_positions()'s real
        # (unparsed) return shape -- see the comment on the position
        # fixture above for why this matters.
        "profit": 0.0, "magic": 900001, "time_utc": fill_time.isoformat(), "time_ny": fill_time.isoformat(),
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
        datetime.datetime(2026, 8, 28, 12, 0), events.append, risk_pct=0.01,
    )

    assert len(results) == 1
    assert results[0]["ticket"] == 3147397683
    assert results[0]["healed"] is True
    assert results[0]["trade_id"] is not None
    assert bridge.modified == [(3147397683, (1.1680 + 1.1652) / 2)]
    recovered = [e for e in events if e["event_type"] == "orphan_position_recovered"]
    assert len(recovered) == 1
    assert recovered[0]["ticket"] == 3147397683
    assert recovered[0]["target"] == (1.1680 + 1.1652) / 2

    # 2026-09-04 fix: a real, permanent Trade record for this orphan --
    # NOT just a healed take-profit -- so it never disappears from trade
    # history once it eventually closes.
    recorded = [e for e in events if e["event_type"] == "orphan_trade_recorded"]
    assert len(recorded) == 1
    from app.models import Trade
    row = db_session.query(Trade).filter(Trade.trade_id == results[0]["trade_id"]).one()
    assert row.user_id == user.user_id
    assert row.direction == "long"
    assert row.entry_price == 1.16460
    assert row.stop_price == 1.16395
    assert row.target_price == (1.1680 + 1.1652) / 2
    assert row.real_position_ticket == 3147397683
    assert row.real_status == "open"
    assert row.is_shadow is False
    # No simulated outcome exists for an orphan -- genuinely open,
    # genuinely unresolved, not a fabricated value.
    assert row.outcome is None
    assert row.exit_price is None
    assert row.realized_r is None


def test_orphan_heal_failure_emits_distinct_check_name(db_session):
    user = _make_user(db_session, "orphan_c@example.com")
    bridge = OrphanFakeBridge()
    fill_time = datetime.datetime(2026, 8, 27, 15, 59, 22)
    bridge._positions[999] = {
        "ticket": 999, "symbol": "EURUSDm", "direction": "long", "volume": 1.0,
        "open_price": 1.1050, "current_price": 1.1050, "stop_loss": 1.1040, "take_profit": 0.0,
        "profit": 0.0, "magic": 900001, "time_utc": fill_time.isoformat(), "time_ny": fill_time.isoformat(),
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
        datetime.datetime(2026, 8, 28, 12, 0), events.append, risk_pct=0.01,
    )

    assert len(results) == 1
    assert results[0]["ticket"] == 999
    assert results[0]["healed"] is False
    # 2026-09-04 fix, the whole point of this test: healing (the
    # take-profit attach) and recording (the permanent Trade row) are
    # deliberately INDEPENDENT protections -- one failing must never
    # cost the other. The heal failed above; the record must still exist.
    assert results[0]["trade_id"] is not None
    check_failures = [e for e in events if e["event_type"] == "safety_check_failed"]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "orphan_position_heal_failed"
    recorded = [e for e in events if e["event_type"] == "orphan_trade_recorded"]
    assert len(recorded) == 1

    from app.models import Trade
    row = db_session.query(Trade).filter(Trade.trade_id == results[0]["trade_id"]).one()
    assert row.real_position_ticket == 999
    assert row.real_status == "open"
    # Heal failed before a target was ever attached on the broker side --
    # target_price still needs a real (NOT NULL) value, so it falls back
    # to the computed target (still computed successfully here; only the
    # broker-side modify_position() call itself failed).
    assert row.target_price == (1.1060 + 1.1040) / 2


def test_positions_fetch_failure_returns_empty_and_logs_check_failure(db_session):
    user = _make_user(db_session, "orphan_d@example.com")
    bridge = OrphanFakeBridge()

    def get_positions(magic):
        raise Exception("simulated bridge failure")
    bridge.get_positions = get_positions

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 8, 28, 12, 0), events.append, risk_pct=0.01,
    )
    assert results == []
    check_failures = [e for e in events if e["event_type"] == "safety_check_failed"]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "orphan_position_check_bridge_call"


def test_orphan_ticket_beyond_32bit_integer_range_still_writes(db_session):
    """Real bug found 2026-09-04 while building the fix below: MT5
    ticket numbers already exceed Postgres's 32-bit INTEGER range
    (max 2,147,483,647) -- every real ticket observed this session did
    (3147397683, 3173996588, 3173996701). Confirmed live: zero rows in
    the production `trades` table ever had real_position_ticket
    populated, despite weeks of real trading -- consistent with EVERY
    real trade write silently failing at the database level the
    instant it tried to store a ticket this large (migration 0022 fixed
    the column type). This test uses a ticket in that exact range,
    deliberately, so a regression back to a 32-bit column fails loudly
    here instead of silently in production again."""
    user = _make_user(db_session, "orphan_bigint@example.com")
    bridge = OrphanFakeBridge()
    fill_time = datetime.datetime(2026, 9, 2, 13, 11, 4)
    big_ticket = 3173996588  # exceeds 2**31 - 1 (2147483647)
    bridge._positions[big_ticket] = {
        "ticket": big_ticket, "symbol": "EURUSDm", "direction": "long", "volume": 5.86,
        "open_price": 1.1586, "current_price": 1.1586, "stop_loss": 1.15776, "take_profit": 0.0,
        "profit": 0.0, "magic": 900001, "time_utc": fill_time.isoformat(), "time_ny": fill_time.isoformat(),
    }
    bridge.candles_response = [
        _make_bar(fill_time - datetime.timedelta(minutes=m), 1.1600, 1.1580) for m in (30, 25, 20, 15, 10, 5)
    ]

    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 9, 4, 8, 0), events.append, risk_pct=0.01,
    )

    assert len(results) == 1
    assert results[0]["trade_id"] is not None, "the write must succeed, not silently fail"

    from app.models import Trade
    row = db_session.query(Trade).filter(Trade.trade_id == results[0]["trade_id"]).one()
    assert row.real_position_ticket == big_ticket


def test_orphan_full_lifecycle_recorded_and_tracked_to_natural_close(db_session):
    """The single most important test in this file, alongside
    test_historical_replay_never_places_a_real_order above -- proves
    the COMPLETE 2026-09-04 fix end to end, not just its individual
    pieces: an orphan is found -> gets a real, permanent Trade record
    -> gets handed off to ongoing tracking -> its eventual natural
    close gets properly recorded on that SAME row. Before this fix, an
    orphan could be found and even successfully healed (target
    attached) and STILL permanently vanish from trade history the
    moment it closed, since nothing ever created a record for it or
    watched for its close -- confirmed to have actually happened to the
    two positions that prompted this fix."""
    from shadow_runner.position_tracker import PositionTracker

    user = _make_user(db_session, "orphan_lifecycle@example.com")
    bridge = OrphanFakeBridge()
    fill_time = datetime.datetime(2026, 9, 2, 13, 11, 4)
    ticket = 3173996588
    bridge._positions[ticket] = {
        "ticket": ticket, "symbol": "EURUSDm", "direction": "long", "volume": 5.86,
        "open_price": 1.1586, "current_price": 1.16242, "stop_loss": 1.15776, "take_profit": 0.0,
        "profit": 2238.52, "magic": 900001, "time_utc": fill_time.isoformat(), "time_ny": fill_time.isoformat(),
    }
    bridge.candles_response = [
        _make_bar(fill_time - datetime.timedelta(minutes=m), 1.1600, 1.1580) for m in (30, 25, 20, 15, 10, 5)
    ]

    # Step 1: the orphan check finds it, heals it, and (the 2026-09-04
    # fix) records it.
    events = []
    results = check_for_orphaned_positions(
        bridge, "EURUSDm", 900001, db_session, str(user.user_id), "fvg",
        datetime.datetime(2026, 9, 4, 8, 0), events.append, risk_pct=0.01,
    )
    assert len(results) == 1
    assert results[0]["healed"] is True
    trade_id = results[0]["trade_id"]
    assert trade_id is not None

    # Step 2: the caller (runner.py/_recover_cross_day_gap or
    # PositionTracker.check_for_orphans, both do this identically) hands
    # off ongoing tracking, exactly like a normally-caught fill would get.
    model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001}
    tracker = PositionTracker(bridge, lambda: db_session, str(user.user_id), model_config)
    tracker.register_new_position(ticket, trade_id, results[0]["entry_time_ny"])
    assert ticket in tracker._tracked

    # Step 3: the position closes naturally on the broker, days later --
    # ordinary check_positions() polling must catch it, same as any
    # other tracked position.
    bridge._positions.pop(ticket)
    bridge._closed_history[ticket] = {
        "ticket": ticket, "is_closed": True, "close_price": 1.1650,
        "close_time_utc": "2026-09-05T10:00:00+00:00", "close_time_ny": "2026-09-05T06:00:00-04:00",
        "profit": 3745.0, "close_reason": "take_profit",
    }
    tracker.check_positions()

    # Step 4: the SAME row -- not a new one -- now shows the real,
    # final outcome. This is the whole point: nothing about this trade
    # is lost, from the moment it's found through to its actual close.
    from app.models import Trade
    row = db_session.query(Trade).filter(Trade.trade_id == trade_id).one()
    assert row.real_status == "closed"
    assert row.real_close_price == 1.1650
    assert row.real_profit == 3745.0
    assert row.real_close_reason == "take_profit"
    assert ticket not in tracker._tracked


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
    assert cd.order_managers == {}, "historical replay must never construct a real OrderManager"
    assert bridge.placed == []


def test_historical_replay_never_places_a_real_order():
    """The single most important test in this file. Same technique as
    test_order_manager_wiring.py's positive-case proof (a real
    trade_candidate_ready DOES reach OrderManager during live
    _process_bar) -- inverted here to prove the SAME real candidate
    firing during a HISTORICAL replay never reaches
    on_trade_candidate_ready(), because cd.order_managers is empty."""
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
    assert cd.order_managers == {}
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
    """check_for_orphaned_positions() needs a real Trade-query .all() --
    no orphans exist in these fixtures (bridge positions are empty), so
    an empty list is the correct answer either way -- this just needs to
    not crash."""

    def query(self, *model_classes):
        from app.models import Trade
        if len(model_classes) == 1 and model_classes[0] is Trade:
            class _EmptyTradeQuery:
                def filter(self, *a, **k):
                    return self

                def all(self):
                    return []
            return _EmptyTradeQuery()
        return super().query(*model_classes)


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
    assert cd.order_managers == {}
    skip_events = [w for w in shared_writes if getattr(w, "event_type", "").startswith("day_skipped_")]
    assert len(skip_events) == 1
