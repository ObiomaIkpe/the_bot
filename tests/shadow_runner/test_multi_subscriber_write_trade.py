"""
Multi-user fan-out, piece 2 (MULTI_USER_FANOUT_PLAN.md) -- direct tests
of ShadowRunner._write_trade()'s dual-write behavior: an always-written
ownerless "shadow" row (the model's own simulated outcome, ownerless per
migration 0021 -- see "Open questions, resolved" in the plan doc) plus
one row per subscriber whose OrderManager actually has a real outcome.

Same technique as test_write_trade_lookup.py (direct _write_trade() call
against a hand-built CurrentDay) but against a REAL db_session, so the
actual written rows can be queried back and asserted on -- these tests
are specifically about who gets which row, which write_trade_lookup.py
(monkeypatches write_trade() entirely) can't prove.
"""
import datetime

from app.models import Trade, User
from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.day_state import CurrentDay
from shadow_runner.runner import ShadowRunner, SHADOW_NOTIONAL_RISK_PCT
from tests.shadow_runner.test_order_manager import FakeBridge


def make_config():
    import os
    os.environ["BRIDGE_URL"] = "http://fake-bridge:8001"
    os.environ["SHADOW_RUNNER_USER_ID"] = "test-user-id"
    return ShadowRunnerConfig()


class FakeSubscriberOrderManager:
    """Directly exercises _write_trade()'s per-subscriber loop without
    driving a real OrderManager through a full fill sequence -- it only
    ever touches .get_real_outcome(), .model_config, and .bridge, so a
    lightweight stand-in is enough (same philosophy as
    test_write_trade_lookup.py testing _write_trade() in isolation)."""

    def __init__(self, model_config, bridge, real_outcome):
        self.model_config = model_config
        self.bridge = bridge
        self._real_outcome = real_outcome

    def get_real_outcome(self):
        return self._real_outcome


def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_bar(date, hour, minute, close=1.1000):
    return {
        "time_utc": datetime.datetime.combine(date, datetime.time(hour, minute)) - datetime.timedelta(hours=4),
        "time_ny": datetime.datetime.combine(date, datetime.time(hour, minute)),
        "open": close, "high": close + 0.0005, "low": close - 0.0005, "close": close,
        "tick_volume": 100, "spread": 8, "real_volume": 0,
    }


def _make_cd_and_trade(date):
    cd = CurrentDay(date)
    cd.bars = [_make_bar(date, 7, m) for m in (0, 5, 10, 15, 20)]
    cd.trend = "up"
    cd.todays_events = [
        {
            "event_type": "order_filled", "timestamp": cd.bars[2]["time_ny"],
            "direction": "long", "entry": 1.10500, "stop": 1.10400, "target": 1.10700,
            "fill_bar_index": 2,
        },
        {
            "event_type": "trade_closed", "timestamp": cd.bars[4]["time_ny"],
            "direction": "long", "outcome": "win", "exit_price": 1.10700,
        },
    ]
    trade = {
        "direction": "long", "entry": 1.10500, "stop": 1.10400, "target": 1.10700,
        "risk_pips": 10.0, "outcome": "win", "exit_price": 1.10700,
    }
    return cd, trade


def test_shadow_row_always_written_even_with_zero_subscribers(db_session):
    """The model's own simulated outcome must still be recorded even
    when nobody's actually subscribed -- this is what shadow-mode model
    evaluation has always depended on."""
    config = make_config()
    runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: db_session)
    date = datetime.date(2026, 8, 3)
    cd, trade = _make_cd_and_trade(date)
    # cd.order_managers stays empty -- no subscribers at all.

    runner._write_trade(cd, trade)

    rows = db_session.query(Trade).filter(Trade.model == "fvg").all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id is None
    assert row.is_shadow is True
    assert row.real_position_ticket is None
    assert row.risk_pct_used == SHADOW_NOTIONAL_RISK_PCT


def test_one_subscriber_with_a_real_outcome_gets_its_own_row_too(db_session):
    config = make_config()
    runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: db_session)
    subscriber = _make_user(db_session, "fanout_sub_a@example.com")
    subscriber_id = subscriber.user_id  # captured now -- see test_order_manager_wiring.py's comment

    date = datetime.date(2026, 8, 3)
    cd, trade = _make_cd_and_trade(date)
    sub_bridge = FakeBridge()
    real_outcome = {
        "position_ticket": 555, "fill_price": 1.10505, "fill_time_utc": cd.bars[2]["time_utc"],
        "fill_time_ny": cd.bars[2]["time_ny"], "close_price": 1.10700,
        "close_time_utc": cd.bars[4]["time_utc"], "close_time_ny": cd.bars[4]["time_ny"],
        "profit": 42.0, "close_reason": "take_profit",
    }
    cd.order_managers[subscriber_id] = FakeSubscriberOrderManager(
        {"model_name": "fvg", "status": "active", "risk_pct": 0.02, "magic_number": 900201},
        sub_bridge, real_outcome,
    )

    runner._write_trade(cd, trade)

    shadow_rows = db_session.query(Trade).filter(Trade.model == "fvg", Trade.user_id.is_(None)).all()
    assert len(shadow_rows) == 1

    sub_rows = db_session.query(Trade).filter(Trade.model == "fvg", Trade.user_id == subscriber_id).all()
    assert len(sub_rows) == 1
    sub_row = sub_rows[0]
    assert sub_row.is_shadow is False
    assert sub_row.risk_pct_used == 0.02
    assert sub_row.real_position_ticket == 555
    assert sub_row.real_profit == 42.0


def test_subscriber_with_no_real_outcome_gets_no_row(db_session):
    """Most subscribers most days: their candidate never filled. No
    Trade row at all for them -- not a row with nulled-out real_* fields."""
    config = make_config()
    runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: db_session)
    subscriber = _make_user(db_session, "fanout_sub_b@example.com")
    subscriber_id = subscriber.user_id

    date = datetime.date(2026, 8, 3)
    cd, trade = _make_cd_and_trade(date)
    cd.order_managers[subscriber_id] = FakeSubscriberOrderManager(
        {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900202},
        FakeBridge(), real_outcome=None,
    )

    runner._write_trade(cd, trade)

    all_rows = db_session.query(Trade).filter(Trade.model == "fvg").all()
    assert len(all_rows) == 1  # only the shadow row
    assert all_rows[0].user_id is None


def test_multiple_subscribers_each_get_their_own_row(db_session):
    config = make_config()
    runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: db_session)
    sub1 = _make_user(db_session, "fanout_sub_c1@example.com")
    sub2 = _make_user(db_session, "fanout_sub_c2@example.com")
    sub1_id, sub2_id = sub1.user_id, sub2.user_id

    date = datetime.date(2026, 8, 3)
    cd, trade = _make_cd_and_trade(date)
    real_outcome = {
        "position_ticket": 777, "fill_price": 1.10505, "fill_time_utc": cd.bars[2]["time_utc"],
        "fill_time_ny": cd.bars[2]["time_ny"], "close_price": 1.10700,
        "close_time_utc": cd.bars[4]["time_utc"], "close_time_ny": cd.bars[4]["time_ny"],
        "profit": 10.0, "close_reason": "take_profit",
    }
    cd.order_managers[sub1_id] = FakeSubscriberOrderManager(
        {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900203},
        FakeBridge(), real_outcome,
    )
    cd.order_managers[sub2_id] = FakeSubscriberOrderManager(
        {"model_name": "fvg", "status": "active", "risk_pct": 0.03, "magic_number": 900204},
        FakeBridge(), real_outcome=None,  # this one never filled
    )

    runner._write_trade(cd, trade)

    rows = db_session.query(Trade).filter(Trade.model == "fvg").all()
    user_ids = {r.user_id for r in rows}
    assert user_ids == {None, sub1_id}  # shadow row + sub1's real row -- sub2 gets nothing
    assert len(rows) == 2


def test_one_subscribers_write_failure_does_not_block_the_others(db_session):
    """Multi-user fan-out isolation, applied to the write loop itself --
    a broken OrderManager (raises on get_real_outcome()) must not stop
    the shadow row or any OTHER subscriber's row from being written."""
    config = make_config()
    runner = ShadowRunner(config, bridge=FakeBridge(), session_factory=lambda: db_session)
    broken_sub = _make_user(db_session, "fanout_broken@example.com")
    healthy_sub = _make_user(db_session, "fanout_healthy@example.com")
    broken_id, healthy_id = broken_sub.user_id, healthy_sub.user_id

    date = datetime.date(2026, 8, 3)
    cd, trade = _make_cd_and_trade(date)

    class ExplodingOrderManager:
        model_config = {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900205}

        def get_real_outcome(self):
            raise RuntimeError("simulated bridge failure")

    real_outcome = {
        "position_ticket": 888, "fill_price": 1.10505, "fill_time_utc": cd.bars[2]["time_utc"],
        "fill_time_ny": cd.bars[2]["time_ny"], "close_price": 1.10700,
        "close_time_utc": cd.bars[4]["time_utc"], "close_time_ny": cd.bars[4]["time_ny"],
        "profit": 5.0, "close_reason": "take_profit",
    }
    cd.order_managers[broken_id] = ExplodingOrderManager()
    cd.order_managers[healthy_id] = FakeSubscriberOrderManager(
        {"model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900206},
        FakeBridge(), real_outcome,
    )

    runner._write_trade(cd, trade)  # must not raise

    rows = db_session.query(Trade).filter(Trade.model == "fvg").all()
    user_ids = {r.user_id for r in rows}
    assert user_ids == {None, healthy_id}
