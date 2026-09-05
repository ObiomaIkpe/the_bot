"""
Tests for shadow_runner/historical_reconciliation.py -- Piece B of the
historical-reconciliation plan (misty-seeking-crescent.md). Uses the
real db_session fixture (not a FakeDB) since reconcile_deals() does
real Event/Trade queries -- same style as test_cross_day_recovery.py's
piece-0 (get_last_event_timestamp) tests.

Per the plan's own mandate: these tests prove the LOGIC is correct.
They do NOT substitute for the mandatory live-VPS validation (real
mt5.history_deals_get() boundary behavior, a real cross-check against
the MT5 terminal) before this is considered actually done.
"""
import datetime
import uuid

from app.core.trade_story import build_trade_chain
from app.models import Event, Trade, User
from shadow_runner.historical_reconciliation import reconcile_deals
from shadow_runner.persistence import trade_exists_for_ticket


def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return str(user.user_id)


def _deal(position_id, entry, symbol="EURUSDm", magic=900001, type_="buy", price=1.1000,
          profit=0.0, reason="stop_loss", time_ny=None):
    time_ny = time_ny or datetime.datetime(2026, 8, 12, 9, 5, tzinfo=datetime.timezone.utc)
    return {
        "ticket": 12345, "position_id": position_id, "symbol": symbol, "magic": magic,
        "entry": entry, "type": type_, "volume": 0.1, "price": price, "profit": profit,
        "time_utc": time_ny, "time_ny": time_ny, "reason": reason,
    }


def _candidate_event(db_session, direction="long", entry=1.1000, stop=1.0990, date=datetime.date(2026, 8, 12)):
    e = Event(
        event_type="trade_candidate_ready",
        timestamp=datetime.datetime.combine(date, datetime.time(9, 0)),
        details={"direction": direction, "entry": entry, "stop": stop},
        user_id=None, model="fvg",
    )
    db_session.add(e)
    db_session.commit()
    return e


class _StubBridge:
    def __init__(self, candles=None, balance=10000.0):
        self.candles = candles or []
        self._balance = balance

    def get_candles_paginated(self, symbol, timeframe, total_bars_needed):
        return self.candles

    def account_info(self):
        return {"balance": self._balance}


def _bars_before(dt, n=20, base=1.0995):
    return [
        {"time_utc": dt - datetime.timedelta(minutes=5 * (i + 1)), "time_ny": dt - datetime.timedelta(minutes=5 * (i + 1)),
         "high": base + 0.0002, "low": base - 0.0002, "open": base, "close": base}
        for i in range(n)
    ]


def test_matched_deal_writes_a_full_trade_row_using_candidate_numbers_not_real_fill_price(db_session):
    entry_time = datetime.datetime(2026, 8, 12, 9, 5, tzinfo=datetime.timezone.utc)
    _candidate_event(db_session, direction="long", entry=1.1000, stop=1.0990)

    entry_deal = _deal(555, "in", type_="buy", price=1.10015, time_ny=entry_time)  # real fill has slippage
    close_deal = _deal(555, "out", type_="sell", price=1.1050, profit=50.0,
                        time_ny=entry_time + datetime.timedelta(hours=2))
    bridge = _StubBridge(candles=_bars_before(entry_time))

    events = reconcile_deals(
        db_session, bridge, [entry_deal, close_deal], magic=900001,
        user_id=_make_user(db_session, "matched@example.com"), model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )
    db_session.commit()

    assert len(events) == 1
    assert events[0]["event_type"] == "historical_trade_reconciled"
    assert events[0]["matched"] is True

    trade = db_session.query(Trade).filter(Trade.real_position_ticket == 555).one()
    assert trade.entry_price == 1.1000, "must use the candidate's simulated entry, not the real fill price"
    assert trade.stop_price == 1.0990
    assert trade.real_fill_price == 1.10015, "the real fill price still belongs in real_fill_price"
    assert trade.real_close_price == 1.1050
    assert trade.real_profit == 50.0
    assert trade.real_status == "closed"
    assert trade.is_shadow is False
    assert trade.setup_context == {"source": "historical_reconciliation"}
    assert trade.exit_price is None, "the SIMULATED result is deliberately not wired up this pass"
    assert trade.equity_after is None, "documented limitation -- never fabricate a chained value"


def test_unmatched_deal_journals_a_raw_fact_only_no_trade_row(db_session):
    """No candidate event exists at all -- must not fabricate a
    Trade row (stop_price/target_price are NOT NULL with no honest
    value here)."""
    entry_time = datetime.datetime(2026, 8, 13, 9, 5, tzinfo=datetime.timezone.utc)
    entry_deal = _deal(556, "in", type_="buy", price=1.1000, time_ny=entry_time)
    close_deal = _deal(556, "out", type_="sell", price=1.1010, profit=10.0,
                        time_ny=entry_time + datetime.timedelta(hours=1))
    bridge = _StubBridge()

    events = reconcile_deals(
        db_session, bridge, [entry_deal, close_deal], magic=900001,
        user_id=str(uuid.uuid4()), model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )
    db_session.commit()

    assert len(events) == 1
    assert events[0]["matched"] is False
    assert events[0]["profit"] == 10.0
    assert db_session.query(Trade).filter(Trade.real_position_ticket == 556).first() is None


def test_already_known_ticket_is_skipped(db_session):
    user_id = _make_user(db_session, "already_known@example.com")
    entry_time = datetime.datetime(2026, 8, 14, 9, 5, tzinfo=datetime.timezone.utc)
    existing = Trade(
        user_id=user_id, model="fvg", is_shadow=False, direction="long",
        entry_price=1.1, stop_price=1.09, target_price=1.12,
        entry_time_utc=entry_time, entry_time_ny=entry_time,
        risk_pct_used=0.01, equity_before=10000.0, real_position_ticket=557,
    )
    db_session.add(existing)
    db_session.commit()

    entry_deal = _deal(557, "in", time_ny=entry_time)
    close_deal = _deal(557, "out", time_ny=entry_time + datetime.timedelta(hours=1))
    events = reconcile_deals(
        db_session, _StubBridge(), [entry_deal, close_deal], magic=900001,
        user_id=user_id, model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )

    assert events == []


def test_partial_close_sequence_is_flagged_not_guessed_at(db_session):
    entry_time = datetime.datetime(2026, 8, 15, 9, 5, tzinfo=datetime.timezone.utc)
    entry_deal = _deal(558, "in", time_ny=entry_time)
    close_1 = _deal(558, "out", price=1.101, time_ny=entry_time + datetime.timedelta(hours=1))
    close_2 = _deal(558, "out", price=1.102, time_ny=entry_time + datetime.timedelta(hours=2))

    events = reconcile_deals(
        db_session, _StubBridge(), [entry_deal, close_1, close_2], magic=900001,
        user_id=str(uuid.uuid4()), model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "safety_check_failed"
    assert events[0]["check_name"] == "historical_reconciliation_partial_close_sequence_skipped"
    assert db_session.query(Trade).filter(Trade.real_position_ticket == 558).first() is None


def test_deals_with_a_different_magic_number_are_ignored(db_session):
    entry_time = datetime.datetime(2026, 8, 16, 9, 5, tzinfo=datetime.timezone.utc)
    entry_deal = _deal(559, "in", magic=999999, time_ny=entry_time)
    close_deal = _deal(559, "out", magic=999999, time_ny=entry_time + datetime.timedelta(hours=1))

    events = reconcile_deals(
        db_session, _StubBridge(), [entry_deal, close_deal], magic=900001,
        user_id=str(uuid.uuid4()), model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )

    assert events == []


def test_still_open_position_no_out_deal_is_left_alone(db_session):
    """Not this piece's job -- either genuinely still open (ordinary
    live tracking's job) or a true orphan (orphan_recovery.py's job)."""
    entry_time = datetime.datetime(2026, 8, 17, 9, 5, tzinfo=datetime.timezone.utc)
    entry_deal = _deal(560, "in", time_ny=entry_time)

    events = reconcile_deals(
        db_session, _StubBridge(), [entry_deal], magic=900001,
        user_id=str(uuid.uuid4()), model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )

    assert events == []


def test_matched_reconciled_trade_actually_resolves_its_full_story_end_to_end(db_session):
    """The whole point of using the candidate's simulated entry_price
    (not the real fill price) for the Trade row: proves
    build_trade_chain() -- completely unmodified, no special-casing for
    reconciled trades anywhere in trade_story.py -- resolves the full
    raid/mss/fvg/candidate/fill chain for a Piece B trade exactly like
    it would for an ordinary one. Naive datetimes throughout, matching
    this codebase's established NY-wall-clock convention (see
    tests/app/test_trade_event_chain_api.py)."""
    entry_time = datetime.datetime(2026, 8, 12, 9, 5, 0)
    user_id = _make_user(db_session, "full_chain_reconciled@example.com")

    raid = Event(
        user_id=None, model="fvg", event_type="raid_detected",
        timestamp=entry_time - datetime.timedelta(minutes=30),
        details={"direction": "long", "bar_index": 12},
    )
    mss = Event(
        user_id=None, model="fvg", event_type="mss_confirmed",
        timestamp=entry_time - datetime.timedelta(minutes=20),
        details={"direction": "long", "raid_bar_index": 12, "mss_bar_index": 20},
    )
    fvg = Event(
        user_id=None, model="fvg", event_type="fvg_found",
        timestamp=entry_time - datetime.timedelta(minutes=15),
        details={"direction": "long", "mss_bar_index": 20},
    )
    candidate = Event(
        user_id=None, model="fvg", event_type="trade_candidate_ready",
        timestamp=entry_time - datetime.timedelta(minutes=10),
        details={"direction": "long", "entry": 1.1000, "stop": 1.0990, "raid_bar": 12, "mss_bar": 20},
    )
    fill = Event(
        user_id=None, model="fvg", event_type="order_filled", timestamp=entry_time,
        details={"direction": "long", "entry": 1.1000, "stop": 1.0990},
    )
    db_session.add_all([raid, mss, fvg, candidate, fill])
    db_session.commit()

    entry_deal = _deal(561, "in", type_="buy", price=1.10015, time_ny=entry_time)  # real slippage vs 1.1000
    close_deal = _deal(561, "out", type_="sell", price=1.1050, profit=50.0,
                        time_ny=entry_time + datetime.timedelta(hours=2))
    bridge = _StubBridge(candles=_bars_before(entry_time))

    reconcile_deals(
        db_session, bridge, [entry_deal, close_deal], magic=900001,
        user_id=user_id, model="fvg", risk_pct=0.01, symbol="EURUSDm",
    )
    db_session.commit()

    trade = db_session.query(Trade).filter(Trade.real_position_ticket == 561).one()
    result = build_trade_chain(db_session, trade)

    assert result.fully_resolved is True, "must resolve exactly like an ordinary trade, no special-casing needed"
    event_types = [e.event_type for e in result.chain]
    assert event_types == ["raid_detected", "mss_confirmed", "fvg_found", "trade_candidate_ready", "order_filled"]


def test_trade_exists_for_ticket_direct(db_session):
    user_id = _make_user(db_session, "ticket_exists@example.com")
    entry_time = datetime.datetime(2026, 8, 18, 9, 5, 0)

    assert trade_exists_for_ticket(db_session, user_id, "fvg", 999) is False

    db_session.add(Trade(
        user_id=user_id, model="fvg", is_shadow=False, direction="long",
        entry_price=1.1, stop_price=1.09, target_price=1.12,
        entry_time_utc=entry_time, entry_time_ny=entry_time,
        risk_pct_used=0.01, equity_before=10000.0, real_position_ticket=999,
    ))
    db_session.commit()

    assert trade_exists_for_ticket(db_session, user_id, "fvg", 999) is True
    assert trade_exists_for_ticket(db_session, user_id, "ob", 999) is False, "must be scoped to the right model"
