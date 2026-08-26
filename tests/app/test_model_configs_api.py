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
