"""
Tests for GET /trades/{trade_id}/event-chain -- the trader-facing "why
was this trade placed" story (app.core.trade_story.build_trade_chain()).
Builds a realistic synthetic day of events using the exact field shapes
the real detectors emit (see trade_story.py's module docstring for
which files those are), including a DECOY candidate on the same day
that must NOT appear in the result -- proving this is scoped to the
one trade, unlike admin's whole-day event-chain endpoint.
"""
import datetime

from app.models.event import Event
from app.models.trade import Trade
from app.models.user import User


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_full_chain_resolves_and_excludes_same_day_decoy(client, db_session):
    token = _register_and_login(client, "chain_full@example.com")
    user = db_session.query(User).filter(User.email == "chain_full@example.com").first()

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.10740, stop_price=1.10500, target_price=1.11000, exit_price=1.11000,
        outcome="win", entry_time_utc=entry_time, entry_time_ny=entry_time,
        risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    raid = Event(
        user_id=user.user_id, model="fvg", event_type="raid_detected",
        timestamp=entry_time - datetime.timedelta(minutes=30),
        details={"direction": "bull", "raid_level": 1.10500, "raid_bar_low": 1.10480, "bar_index": 12, "mss_reference_level": 1.10700},
    )
    mss = Event(
        user_id=user.user_id, model="fvg", event_type="mss_confirmed",
        timestamp=entry_time - datetime.timedelta(minutes=20),
        details={"direction": "bull", "level": 1.10700, "close": 1.10720, "raid_bar_index": 12, "mss_bar_index": 20},
    )
    fvg = Event(
        user_id=user.user_id, model="fvg", event_type="fvg_found",
        timestamp=entry_time - datetime.timedelta(minutes=15),
        details={"direction": "bull", "top": 1.10750, "bottom": 1.10730, "frame_idx": 18, "mss_bar_index": 20},
    )
    candidate = Event(
        user_id=user.user_id, model="fvg", event_type="trade_candidate_ready",
        timestamp=entry_time - datetime.timedelta(minutes=10),
        details={"direction": "bull", "entry": 1.10740, "stop": 1.10500, "raid_bar": 12, "mss_bar": 20},
    )
    fill = Event(
        user_id=user.user_id, model="fvg", event_type="order_filled", timestamp=entry_time,
        details={"direction": "bull", "entry": 1.10740, "stop": 1.10500, "target": 1.11000, "fill_bar_index": 22},
        trade_id=trade.trade_id,
    )
    close = Event(
        user_id=user.user_id, model="fvg", event_type="trade_closed",
        timestamp=entry_time + datetime.timedelta(hours=2),
        details={"direction": "bull", "outcome": "win", "exit_price": 1.11000},
        trade_id=trade.trade_id,
    )

    # Decoy: an entirely separate candidate/raid the SAME day, same
    # model -- must not appear in this trade's chain.
    decoy_raid = Event(
        user_id=user.user_id, model="fvg", event_type="raid_detected",
        timestamp=entry_time + datetime.timedelta(minutes=5),
        details={"direction": "bear", "raid_level": 1.11200, "raid_bar_high": 1.11220, "bar_index": 30, "mss_reference_level": 1.11000},
    )

    db_session.add_all([raid, mss, fvg, candidate, fill, close, decoy_raid])
    db_session.commit()

    resp = client.get(f"/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["fully_resolved"] is True

    event_types = [e["event_type"] for e in body["chain"]]
    assert event_types == ["raid_detected", "mss_confirmed", "fvg_found", "trade_candidate_ready", "order_filled", "trade_closed"]
    # The decoy's raid_detected must not sneak in.
    assert len(body["chain"]) == 6
    assert all("bear" not in e["details"].values() for e in body["chain"] if e["event_type"] == "raid_detected")

    # Every row carries a human-readable narrative, not just raw details.
    assert all(e["narrative"] for e in body["chain"])
    fill_row = next(e for e in body["chain"] if e["event_type"] == "order_filled")
    assert "1.10740" in fill_row["narrative"]


def test_trade_belonging_to_another_user_404s(client, db_session):
    owner_token = _register_and_login(client, "chain_owner@example.com")
    _register_and_login(client, "chain_other@example.com")
    owner = db_session.query(User).filter(User.email == "chain_owner@example.com").first()
    other_token_resp = client.post(
        "/auth/login", data={"username": "chain_other@example.com", "password": "a-real-password"},
    )
    other_token = other_token_resp.json()["access_token"]

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=owner.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.3, outcome="win",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    # The owner can see it.
    owner_resp = client.get(f"/trades/{trade.trade_id}/event-chain", headers=_auth_header(owner_token))
    assert owner_resp.status_code == 200

    # A different user gets 404, not the owner's data.
    other_resp = client.get(f"/trades/{trade.trade_id}/event-chain", headers=_auth_header(other_token))
    assert other_resp.status_code == 404


def test_unknown_trade_id_404s(client, db_session):
    token = _register_and_login(client, "chain_unknown@example.com")
    resp = client.get(
        "/trades/00000000-0000-0000-0000-000000000000/event-chain", headers=_auth_header(token),
    )
    assert resp.status_code == 404


def test_scratch_trade_with_no_candidate_falls_back_partially_resolved(client, db_session):
    """A trade whose fill event exists but no matching trade_candidate_ready
    can be found (e.g. very old data) -- must return what it has
    (fill + close) with fully_resolved=False, not error."""
    token = _register_and_login(client, "chain_scratch@example.com")
    user = db_session.query(User).filter(User.email == "chain_scratch@example.com").first()

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.05, outcome="scratch",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    fill = Event(
        user_id=user.user_id, model="fvg", event_type="order_filled", timestamp=entry_time,
        details={"direction": "long", "entry": 1.1, "stop": 1.0, "target": 1.3, "fill_bar_index": 5},
        trade_id=trade.trade_id,
    )
    close = Event(
        user_id=user.user_id, model="fvg", event_type="trade_closed",
        timestamp=entry_time + datetime.timedelta(hours=1),
        details={"direction": "long", "outcome": "scratch", "exit_price": 1.05},
        trade_id=trade.trade_id,
    )
    db_session.add_all([fill, close])
    db_session.commit()

    resp = client.get(f"/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["fully_resolved"] is False
    event_types = [e["event_type"] for e in body["chain"]]
    assert event_types == ["order_filled", "trade_closed"]
