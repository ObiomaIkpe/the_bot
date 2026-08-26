import itertools

from app.models.model_config import ModelConfig
from app.models.user_settings import UserSettings

_magic_counter = itertools.count(810001)


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


def test_patch_settings_updates_is_paused_and_journals_per_model(client, db_session):
    token = _register_and_login(client, "sd@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    db_session.add(UserSettings(
        user_id=user_id, instrument="EURUSDm", max_daily_loss_pct=0.03,
        demo_or_live="demo", is_paused=False,
    ))
    db_session.add(ModelConfig(user_id=user_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter)))
    db_session.add(ModelConfig(user_id=user_id, model_name="ob", status="disabled", risk_pct=0.01, magic_number=next(_magic_counter)))
    db_session.commit()

    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["is_paused"] is True

    events = client.get("/events", headers=_auth_header(token)).json()
    journaled = [e for e in events if e["event_type"] == "account_settings_updated"]
    assert len(journaled) == 2, "one event per model_config the user has"
    assert {e["model"] for e in journaled} == {"fvg", "ob"}


def test_patch_settings_no_model_configs_is_a_noop_journal(client, db_session):
    token = _register_and_login(client, "se@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    db_session.add(UserSettings(
        user_id=user_id, instrument="EURUSDm", max_daily_loss_pct=0.03,
        demo_or_live="demo", is_paused=False,
    ))
    db_session.commit()

    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200

    events = client.get("/events", headers=_auth_header(token)).json()
    assert [e for e in events if e["event_type"] == "account_settings_updated"] == []


def test_patch_settings_404_when_no_settings_row(client, db_session):
    token = _register_and_login(client, "sf@example.com")
    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 404


def test_patch_settings_no_fields_is_400(client, db_session):
    token = _register_and_login(client, "sg@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    db_session.add(UserSettings(
        user_id=user_id, instrument="EURUSDm", max_daily_loss_pct=0.03,
        demo_or_live="demo", is_paused=False,
    ))
    db_session.commit()

    resp = client.patch("/settings", json={}, headers=_auth_header(token))
    assert resp.status_code == 400
