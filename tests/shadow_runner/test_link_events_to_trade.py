"""
Tests for shadow_runner.persistence.link_events_to_trade() -- logging/
audit review part 3 (the events.trade_id FK, migration 0017). Runs
against a real migrated test DB (db_session fixture, see
tests/conftest.py) since this exercises a real UPDATE ... WHERE
event_id IN (...) query, not something a fake in-memory DB can stand in for.
"""
import uuid

from app.models.event import Event
from app.models.trade import Trade
from app.models.user import User
from shadow_runner.persistence import link_events_to_trade


def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_trade(db_session, user):
    import datetime
    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.3, outcome="win",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)
    return trade


def test_link_events_to_trade_sets_trade_id_on_given_events(db_session):
    user = _make_user(db_session, "link_events_1@example.com")
    trade = _make_trade(db_session, user)

    fill = Event(user_id=user.user_id, model="fvg", event_type="order_filled", details={})
    close = Event(user_id=user.user_id, model="fvg", event_type="trade_closed", details={})
    unrelated = Event(user_id=user.user_id, model="fvg", event_type="raid_detected", details={})
    db_session.add_all([fill, close, unrelated])
    db_session.commit()
    db_session.refresh(fill)
    db_session.refresh(close)
    db_session.refresh(unrelated)

    link_events_to_trade(db_session, [fill.event_id, close.event_id], trade.trade_id)
    db_session.commit()

    db_session.refresh(fill)
    db_session.refresh(close)
    db_session.refresh(unrelated)
    assert fill.trade_id == trade.trade_id
    assert close.trade_id == trade.trade_id
    assert unrelated.trade_id is None, "must not touch events outside the given id list"


def test_link_events_to_trade_no_ops_on_empty_list(db_session):
    user = _make_user(db_session, "link_events_2@example.com")
    trade = _make_trade(db_session, user)
    # No event_ids -- must not raise, must not touch anything.
    link_events_to_trade(db_session, [], trade.trade_id)
    db_session.commit()


def test_link_events_to_trade_ignores_unknown_event_id(db_session):
    user = _make_user(db_session, "link_events_3@example.com")
    trade = _make_trade(db_session, user)
    # A random id that matches no row -- must not raise.
    link_events_to_trade(db_session, [uuid.uuid4()], trade.trade_id)
    db_session.commit()
