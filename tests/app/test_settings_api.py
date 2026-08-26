from app.models.user_settings import UserSettings


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_get_settings_returns_current_users_row(client, db_session):
    token_a = _register_and_login(client, "sa@example.com")
    token_b = _register_and_login(client, "sb@example.com")
    user_a_id = client.get("/auth/me", headers=_auth_header(token_a)).json()["user_id"]
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    db_session.add(UserSettings(
        user_id=user_a_id, instrument="EURUSDm", max_daily_loss_pct=0.03,
        demo_or_live="demo", is_paused=False,
    ))
    db_session.add(UserSettings(
        user_id=user_b_id, instrument="EURUSDm", max_daily_loss_pct=0.05,
        demo_or_live="live", is_paused=True,
    ))
    db_session.commit()

    resp = client.get("/settings", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_daily_loss_pct"] == 0.03
    assert body["is_paused"] is False


def test_get_settings_404_when_none_configured(client, db_session):
    token = _register_and_login(client, "sc@example.com")
    resp = client.get("/settings", headers=_auth_header(token))
    assert resp.status_code == 404


def test_get_settings_requires_auth(client):
    resp = client.get("/settings")
    assert resp.status_code == 401
