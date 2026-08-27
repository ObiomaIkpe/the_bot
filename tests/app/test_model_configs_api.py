from app.models.model_config import ModelConfig


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

    mc_a = db_session.query(ModelConfig).filter_by(user_id=user_a_id, model_name="fvg").one()
    mc_a.status = "active"
    mc_b = db_session.query(ModelConfig).filter_by(user_id=user_b_id, model_name="fvg").one()
    mc_b.status = "shadow"
    db_session.commit()

    resp = client.get("/model-configs", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    # Every user is auto-provisioned all 3 models (app/core/provisioning.py)
    # -- this test's real point is that user_b's configs never leak into
    # user_a's list, not the total count.
    assert len(body) == 3
    assert all(c["config_id"] != str(mc_b.config_id) for c in body)
    fvg = next(c for c in body if c["model_name"] == "fvg")
    assert fvg["status"] == "active"


def test_list_model_configs_requires_auth(client):
    resp = client.get("/model-configs")
    assert resp.status_code == 401


def test_patch_model_config_updates_status_and_journals_event(client, db_session):
    token = _register_and_login(client, "mcd@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "shadow"
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
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"is_paused": True}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["is_paused"] is True


def test_patch_model_config_rejects_invalid_status(client, db_session):
    token = _register_and_login(client, "mcf@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"status": "not-a-real-status"}, headers=_auth_header(token))
    assert resp.status_code == 422


def test_patch_model_config_404_for_another_users_config(client, db_session):
    token_a = _register_and_login(client, "mcg@example.com")
    token_b = _register_and_login(client, "mch@example.com")
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]
    mc = db_session.query(ModelConfig).filter_by(user_id=user_b_id, model_name="fvg").one()
    mc.status = "active"
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={"status": "shadow"}, headers=_auth_header(token_a))
    assert resp.status_code == 404


def test_patch_model_config_no_fields_is_400(client, db_session):
    token = _register_and_login(client, "mci@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    db_session.commit()

    resp = client.patch(f"/model-configs/{mc.config_id}", json={}, headers=_auth_header(token))
    assert resp.status_code == 400
