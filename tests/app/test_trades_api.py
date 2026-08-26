import datetime

from app.models.trade import Trade


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _make_trade(user_id, **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    kwargs = dict(
        user_id=user_id, model="fvg", is_shadow=True,
        direction="long", entry_price=1.1000, stop_price=1.0990, target_price=1.1020,
        entry_time_utc=now, entry_time_ny=now,
        risk_pct_used=0.01, equity_before=10000.0,
    )
    kwargs.update(overrides)
    return Trade(**kwargs)


def test_list_trades_returns_only_current_users_trades(client, db_session):
    token_a = _register_and_login(client, "ta@example.com")
    token_b = _register_and_login(client, "tb@example.com")
    user_a_id = client.get("/auth/me", headers=_auth_header(token_a)).json()["user_id"]
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    db_session.add(_make_trade(user_a_id))
    db_session.add(_make_trade(user_b_id))
    db_session.commit()

    resp = client.get("/trades", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1


def test_list_trades_requires_auth(client):
    resp = client.get("/trades")
    assert resp.status_code == 401


def test_list_trades_filters_by_outcome_and_shadow(client, db_session):
    token = _register_and_login(client, "tc@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    db_session.add(_make_trade(user_id, is_shadow=True, outcome="win"))
    db_session.add(_make_trade(user_id, is_shadow=False, outcome="loss"))
    db_session.commit()

    resp = client.get("/trades", params={"outcome": "win"}, headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["outcome"] == "win"

    resp = client.get("/trades", params={"is_shadow": False}, headers=_auth_header(token))
    body = resp.json()
    assert len(body) == 1
    assert body[0]["is_shadow"] is False
