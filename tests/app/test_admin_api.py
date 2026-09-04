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


def _promote(db_session, email):
    user = db_session.query(User).filter(User.email == email).first()
    user.is_admin = True
    db_session.commit()
    return user


def test_admin_routes_require_auth(client):
    for path in ("/admin/events", "/admin/trades", "/admin/safety-checks", "/admin/audit-log", "/admin/model-configs"):
        assert client.get(path).status_code == 401


def test_admin_routes_reject_non_admin_user(client, db_session):
    # Every user defaults to is_admin=True as of migration 0019 -- this
    # test needs an explicit demote to still exercise the rejection path.
    token = _register_and_login(client, "not_admin@example.com")
    user = db_session.query(User).filter(User.email == "not_admin@example.com").first()
    user.is_admin = False
    db_session.commit()
    for path in ("/admin/events", "/admin/trades", "/admin/safety-checks", "/admin/audit-log", "/admin/model-configs"):
        resp = client.get(path, headers=_auth_header(token))
        assert resp.status_code == 403


def test_admin_events_shows_rows_from_multiple_users(client, db_session):
    token_a = _register_and_login(client, "admin_evt_a@example.com")
    token_b = _register_and_login(client, "admin_evt_b@example.com")
    user_a = db_session.query(User).filter(User.email == "admin_evt_a@example.com").first()
    user_b = db_session.query(User).filter(User.email == "admin_evt_b@example.com").first()
    admin = _promote(db_session, "admin_evt_a@example.com")

    db_session.add(Event(user_id=user_a.user_id, model="fvg", event_type="raid_detected", details={}))
    db_session.add(Event(user_id=user_b.user_id, model="fvg", event_type="mss_confirmed", details={}))
    db_session.commit()

    resp = client.get("/admin/events", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    emails = {row["user_email"] for row in body}
    assert emails == {"admin_evt_a@example.com", "admin_evt_b@example.com"}


def test_admin_events_includes_ownerless_shared_narrative_rows(client, db_session):
    """Multi-user fan-out, piece 1.5: /admin/events must use an OUTER
    join -- an inner join would silently drop every narrative row
    (Event.user_id IS NULL) since there's no User to join to. Narrative
    rows show user_email=None, not a crash or a dropped row."""
    token = _register_and_login(client, "admin_evt_narr@example.com")
    _promote(db_session, "admin_evt_narr@example.com")

    db_session.add(Event(user_id=None, model="fvg", event_type="raid_detected", details={}))
    db_session.commit()

    resp = client.get("/admin/events", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "raid_detected"
    assert body[0]["user_email"] is None


def test_admin_safety_checks_filters_to_safety_check_failed_only(client, db_session):
    token = _register_and_login(client, "admin_safety@example.com")
    user = db_session.query(User).filter(User.email == "admin_safety@example.com").first()
    _promote(db_session, "admin_safety@example.com")

    db_session.add(Event(user_id=user.user_id, model="fvg", event_type="raid_detected", details={}))
    db_session.add(
        Event(
            user_id=user.user_id, model="fvg", event_type="safety_check_failed",
            details={"check_name": "bridge_call", "error": "timeout"},
        )
    )
    db_session.commit()

    resp = client.get("/admin/safety-checks", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "safety_check_failed"
    assert body[0]["details"]["check_name"] == "bridge_call"


def test_admin_trades_shows_rows_from_multiple_users(client, db_session):
    token_a = _register_and_login(client, "admin_trade_a@example.com")
    token_b = _register_and_login(client, "admin_trade_b@example.com")
    user_a = db_session.query(User).filter(User.email == "admin_trade_a@example.com").first()
    user_b = db_session.query(User).filter(User.email == "admin_trade_b@example.com").first()
    _promote(db_session, "admin_trade_a@example.com")

    now = datetime.datetime.utcnow()
    for user in (user_a, user_b):
        db_session.add(
            Trade(
                user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
                entry_price=1.1, stop_price=1.0, target_price=1.3,
                entry_time_utc=now, entry_time_ny=now, risk_pct_used=0.01, equity_before=1000.0,
            )
        )
    db_session.commit()

    resp = client.get("/admin/trades", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    emails = {row["user_email"] for row in body}
    assert emails == {"admin_trade_a@example.com", "admin_trade_b@example.com"}


def test_admin_trades_includes_ownerless_shadow_row(client, db_session):
    """Multi-user fan-out, piece 2: /admin/trades must use an OUTER
    join -- the model's ownerless shadow row (Trade.user_id IS NULL,
    see migration 0021) has no User to join to, and an inner join would
    silently drop it. Shows user_email=None, not a crash or a dropped
    row."""
    token = _register_and_login(client, "admin_trade_shadow@example.com")
    _promote(db_session, "admin_trade_shadow@example.com")

    now = datetime.datetime.utcnow()
    db_session.add(
        Trade(
            user_id=None, model="fvg", is_shadow=True, direction="long",
            entry_price=1.1, stop_price=1.0, target_price=1.3,
            entry_time_utc=now, entry_time_ny=now, risk_pct_used=0.01, equity_before=1000.0,
        )
    )
    db_session.commit()

    resp = client.get("/admin/trades", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["user_email"] is None
    assert body[0]["is_shadow"] is True


def test_admin_trade_event_chain_matches_fill_and_close(client, db_session):
    token = _register_and_login(client, "admin_chain@example.com")
    user = db_session.query(User).filter(User.email == "admin_chain@example.com").first()
    _promote(db_session, "admin_chain@example.com")

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.3, outcome="win",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    fill_event = Event(
        user_id=user.user_id, model="fvg", event_type="order_filled",
        timestamp=entry_time, details={"direction": "long", "entry": 1.1},
    )
    close_event = Event(
        user_id=user.user_id, model="fvg", event_type="trade_closed",
        timestamp=entry_time + datetime.timedelta(hours=2),
        details={"outcome": "win", "exit_price": 1.3},
    )
    unrelated_event = Event(
        user_id=user.user_id, model="fvg", event_type="raid_detected",
        timestamp=entry_time - datetime.timedelta(minutes=30), details={},
    )
    db_session.add_all([fill_event, close_event, unrelated_event])
    db_session.commit()

    resp = client.get(f"/admin/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["day_events"]) == 3
    matched_fill = next(e for e in body["day_events"] if e["event_id"] == body["matched_fill_event_id"])
    assert matched_fill["event_type"] == "order_filled"
    matched_close = next(e for e in body["day_events"] if e["event_id"] == body["matched_close_event_id"])
    assert matched_close["event_type"] == "trade_closed"


def test_admin_trade_event_chain_merges_orphan_discovery_events_from_a_different_day(client, db_session):
    """2026-09-04: an orphan-recovered trade's own discovery/heal
    events land on the day it was FOUND, not its entry_time_ny day --
    could be days apart. The entry-day window this endpoint otherwise
    uses would silently omit them, leaving an admin looking at a
    recovered trade's chain with no sign it was ever an orphan at all.
    Must be merged in by ticket regardless of which day they're on."""
    token = _register_and_login(client, "admin_chain_orphan@example.com")
    user = db_session.query(User).filter(User.email == "admin_chain_orphan@example.com").first()
    _promote(db_session, "admin_chain_orphan@example.com")

    entry_time = datetime.datetime(2026, 9, 2, 13, 11, 4)
    discovery_time = datetime.datetime(2026, 9, 4, 8, 0, 0)
    ticket = 3173996588
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=False, direction="long",
        entry_price=1.1586, stop_price=1.15776, target_price=1.1620,
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
        setup_context={"source": "orphan_recovery"}, real_position_ticket=ticket, real_status="open",
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    recovered = Event(
        user_id=user.user_id, model="fvg", event_type="orphan_position_recovered",
        timestamp=discovery_time, details={"ticket": ticket, "direction": "long", "target": 1.1620},
    )
    recorded = Event(
        user_id=user.user_id, model="fvg", event_type="orphan_trade_recorded",
        timestamp=discovery_time, details={"ticket": ticket, "trade_id": str(trade.trade_id)},
        trade_id=trade.trade_id,
    )
    db_session.add_all([recovered, recorded])
    db_session.commit()

    resp = client.get(f"/admin/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    event_types = sorted(e["event_type"] for e in body["day_events"])
    assert event_types == ["orphan_position_recovered", "orphan_trade_recorded"]
    matched_close = next(
        (e for e in body["day_events"] if e["event_id"] == body["matched_close_event_id"]), None
    )
    assert matched_close is None  # still open, no close event to match


def test_admin_trade_event_chain_attributes_narrative_and_personal_rows_correctly(client, db_session):
    """Multi-user fan-out, piece 1.5: day_events now mixes a shared,
    ownerless narrative row in with the trade owner's own real-action
    events -- each row must be attributed correctly per-event, not
    blanket-labeled with the trade owner's email (that would falsely
    imply they personally caused the shared narrative event too)."""
    token = _register_and_login(client, "admin_chain_narr@example.com")
    user = db_session.query(User).filter(User.email == "admin_chain_narr@example.com").first()
    _promote(db_session, "admin_chain_narr@example.com")

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.3, outcome="win",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    narrative_event = Event(
        user_id=None, model="fvg", event_type="raid_detected",
        timestamp=entry_time - datetime.timedelta(minutes=30), details={},
    )
    personal_event = Event(
        user_id=user.user_id, model="fvg", event_type="real_trade_closed",
        timestamp=entry_time + datetime.timedelta(hours=2), details={},
    )
    db_session.add_all([narrative_event, personal_event])
    db_session.commit()

    resp = client.get(f"/admin/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["day_events"]) == 2
    narrative_row = next(e for e in body["day_events"] if e["event_type"] == "raid_detected")
    personal_row = next(e for e in body["day_events"] if e["event_type"] == "real_trade_closed")
    assert narrative_row["user_email"] is None
    assert personal_row["user_email"] == "admin_chain_narr@example.com"


def test_admin_trade_event_chain_prefers_real_trade_id_over_heuristic(client, db_session):
    """Two order_filled events, same direction/price, same day -- the
    old heuristic alone can't tell them apart (it'd always pick the
    first). The events.trade_id FK (logging/audit review part 3) is
    what actually disambiguates them; this proves the endpoint uses it
    rather than falling through to the ambiguous heuristic."""
    token = _register_and_login(client, "admin_chain_fk@example.com")
    user = db_session.query(User).filter(User.email == "admin_chain_fk@example.com").first()
    _promote(db_session, "admin_chain_fk@example.com")

    entry_time = datetime.datetime(2026, 8, 1, 10, 0, 0)
    trade = Trade(
        user_id=user.user_id, model="fvg", is_shadow=True, direction="long",
        entry_price=1.1, stop_price=1.0, target_price=1.3, exit_price=1.3, outcome="win",
        entry_time_utc=entry_time, entry_time_ny=entry_time, risk_pct_used=0.01, equity_before=1000.0,
    )
    db_session.add(trade)
    db_session.commit()
    db_session.refresh(trade)

    # Decoy: an identical-looking fill from an unrelated attempt earlier
    # the same day (e.g. a cancelled/replaced order) -- NOT linked to
    # this trade.
    decoy_fill = Event(
        user_id=user.user_id, model="fvg", event_type="order_filled",
        timestamp=entry_time - datetime.timedelta(hours=1),
        details={"direction": "long", "entry": 1.1},
    )
    real_fill = Event(
        user_id=user.user_id, model="fvg", event_type="order_filled",
        timestamp=entry_time, details={"direction": "long", "entry": 1.1},
        trade_id=trade.trade_id,
    )
    db_session.add_all([decoy_fill, real_fill])
    db_session.commit()
    db_session.refresh(real_fill)

    resp = client.get(f"/admin/trades/{trade.trade_id}/event-chain", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_fill_event_id"] == str(real_fill.event_id)


def test_admin_trade_event_chain_404_for_unknown_trade(client, db_session):
    token = _register_and_login(client, "admin_chain_404@example.com")
    _promote(db_session, "admin_chain_404@example.com")

    resp = client.get(
        "/admin/trades/00000000-0000-0000-0000-000000000000/event-chain", headers=_auth_header(token)
    )
    assert resp.status_code == 404


def test_admin_audit_log_shows_all_actors_and_filters(client, db_session):
    token = _register_and_login(client, "admin_audit@example.com")
    _promote(db_session, "admin_audit@example.com")
    # Registering + logging in above already wrote user_registered and
    # login_succeeded rows for this same account -- confirm they're
    # visible, then filter down to just one event_type.

    resp = client.get("/admin/audit-log", headers=_auth_header(token))
    assert resp.status_code == 200
    all_types = {row["event_type"] for row in resp.json()}
    assert "user_registered" in all_types
    assert "login_succeeded" in all_types

    filtered = client.get(
        "/admin/audit-log", params={"event_type": "user_registered"}, headers=_auth_header(token)
    )
    assert filtered.status_code == 200
    assert all(row["event_type"] == "user_registered" for row in filtered.json())


def test_admin_model_configs_shows_all_users(client, db_session):
    token_a = _register_and_login(client, "admin_mc_a@example.com")
    _register_and_login(client, "admin_mc_b@example.com")
    _promote(db_session, "admin_mc_a@example.com")

    resp = client.get("/admin/model-configs", headers=_auth_header(token_a))
    assert resp.status_code == 200
    emails = {row["user_email"] for row in resp.json()}
    assert emails == {"admin_mc_a@example.com", "admin_mc_b@example.com"}
    # 3 models each (fvg/ob/fvg_ob), auto-provisioned at registration
    assert len(resp.json()) == 6
