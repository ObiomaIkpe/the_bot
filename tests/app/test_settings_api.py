from app.models.model_config import ModelConfig
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

    # Registration already provisions a default UserSettings row for
    # both users (app/core/provisioning.py) -- just tune the values this
    # test cares about instead of inserting a second row.
    settings_a = db_session.query(UserSettings).filter_by(user_id=user_a_id).one()
    settings_a.max_daily_loss_pct = 0.03
    settings_b = db_session.query(UserSettings).filter_by(user_id=user_b_id).one()
    settings_b.max_daily_loss_pct = 0.05
    settings_b.demo_or_live = "live"
    settings_b.is_paused = True
    db_session.commit()

    resp = client.get("/settings", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_daily_loss_pct"] == 0.03
    assert body["is_paused"] is False


def test_get_settings_404_when_none_configured(client, db_session):
    token = _register_and_login(client, "sc@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    # Registration now always provisions a settings row -- this test
    # covers the router's defensive 404-if-missing branch, which is
    # still real code even though a fresh registration can no longer
    # naturally land in this state.
    db_session.query(UserSettings).filter_by(user_id=user_id).delete()
    db_session.commit()

    resp = client.get("/settings", headers=_auth_header(token))
    assert resp.status_code == 404


def test_get_settings_requires_auth(client):
    resp = client.get("/settings")
    assert resp.status_code == 401


def test_patch_settings_updates_is_paused_and_journals_per_model(client, db_session):
    token = _register_and_login(client, "sd@example.com")

    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["is_paused"] is True

    events = client.get("/events", headers=_auth_header(token)).json()
    journaled = [e for e in events if e["event_type"] == "account_settings_updated"]
    # Registration auto-provisions all three models (fvg, ob, fvg_ob) --
    # PATCH /settings fans out one event per model_config the user has.
    assert len(journaled) == 3, "one event per model_config the user has"
    assert {e["model"] for e in journaled} == {"fvg", "ob", "fvg_ob"}


def test_patch_settings_no_model_configs_is_a_noop_journal(client, db_session):
    token = _register_and_login(client, "se@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    # Registration now always provisions 3 model_configs -- delete them
    # to exercise the defensive "user has zero" branch directly, since a
    # fresh registration can no longer naturally reach that state.
    db_session.query(ModelConfig).filter_by(user_id=user_id).delete()
    db_session.commit()

    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200

    events = client.get("/events", headers=_auth_header(token)).json()
    assert [e for e in events if e["event_type"] == "account_settings_updated"] == []


def test_patch_settings_404_when_no_settings_row(client, db_session):
    token = _register_and_login(client, "sf@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    db_session.query(UserSettings).filter_by(user_id=user_id).delete()
    db_session.commit()

    resp = client.patch("/settings", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 404


def test_patch_settings_no_fields_is_400(client, db_session):
    token = _register_and_login(client, "sg@example.com")
    resp = client.patch("/settings", json={}, headers=_auth_header(token))
    assert resp.status_code == 400
