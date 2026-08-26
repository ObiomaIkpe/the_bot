import itertools

from app.models.model_config import ModelConfig

_magic_counter = itertools.count(800001)


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_model_configs_returns_only_current_users_configs(client, db_session):
    token_a = _register_and_login(client, "mca@example.com")
    token_b = _register_and_login(client, "mcb@example.com")
    user_a_id = client.get("/auth/me", headers=_auth_header(token_a)).json()["user_id"]
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    db_session.add(ModelConfig(user_id=user_a_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter)))
    db_session.add(ModelConfig(user_id=user_b_id, model_name="fvg", status="shadow", risk_pct=0.01, magic_number=next(_magic_counter)))
    db_session.commit()

    resp = client.get("/model-configs", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "active"


def test_list_model_configs_requires_auth(client):
    resp = client.get("/model-configs")
    assert resp.status_code == 401


def test_patch_model_config_updates_status_and_journals_event(client, db_session):
    token = _register_and_login(client, "mcd@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = ModelConfig(user_id=user_id, model_name="fvg", status="shadow", risk_pct=0.01, magic_number=next(_magic_counter))
    db_session.add(mc)
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"status": "active"}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    events = client.get("/events", headers=_auth_header(token)).json()
    journaled = [e for e in events if e["event_type"] == "model_config_updated"]
    assert len(journaled) == 1
    assert journaled[0]["details"]["changed_fields"] == {"status": "active"}


def test_patch_model_config_updates_is_paused(client, db_session):
    token = _register_and_login(client, "mce@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = ModelConfig(user_id=user_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter))
    db_session.add(mc)
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["is_paused"] is True


def test_patch_model_config_rejects_invalid_status(client, db_session):
    token = _register_and_login(client, "mcf@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = ModelConfig(user_id=user_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter))
    db_session.add(mc)
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"status": "not-a-real-status"}, headers=_auth_header(token))
    assert resp.status_code == 422


def test_patch_model_config_404_for_another_users_config(client, db_session):
    token_a = _register_and_login(client, "mcg@example.com")
    token_b = _register_and_login(client, "mch@example.com")
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]
    mc = ModelConfig(user_id=user_b_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter))
    db_session.add(mc)
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"status": "shadow"}, headers=_auth_header(token_a))
    assert resp.status_code == 404


def test_patch_model_config_no_fields_is_400(client, db_session):
    token = _register_and_login(client, "mci@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = ModelConfig(user_id=user_id, model_name="fvg", status="active", risk_pct=0.01, magic_number=next(_magic_counter))
    db_session.add(mc)
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={}, headers=_auth_header(token))
    assert resp.status_code == 400
