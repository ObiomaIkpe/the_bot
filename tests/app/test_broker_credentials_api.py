from app.models.broker_credential import BrokerCredential


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_broker_credential_never_returns_password(client, db_session):
    token = _register_and_login(client, "bc_a@example.com")

    resp = client.post(
        "/broker-credentials",
        json={
            "broker_name": "forex.com", "account_login": "476123801",
            "account_password": "super-secret", "server": "FOREXcom-Demo", "account_type": "demo",
        },
        headers=_auth_header(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_login"] == "476123801"
    assert "account_password" not in body
    assert body["bridge_configured"] is False


def test_create_broker_credential_stores_encrypted_not_plaintext(client, db_session):
    token = _register_and_login(client, "bc_b@example.com")
    client.post(
        "/broker-credentials",
        json={
            "broker_name": "forex.com", "account_login": "1", "account_password": "super-secret",
            "server": "s", "account_type": "demo",
        },
        headers=_auth_header(token),
    )
    row = db_session.query(BrokerCredential).first()
    assert row._account_password_enc != "super-secret"
    assert row.account_password == "super-secret"  # decrypts correctly via the property


def test_list_broker_credentials_scoped_to_current_user(client, db_session):
    token_a = _register_and_login(client, "bc_c@example.com")
    token_b = _register_and_login(client, "bc_d@example.com")

    client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token_a),
    )
    client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "2", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token_b),
    )

    resp = client.get("/broker-credentials", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["account_login"] == "1"


def test_create_broker_credential_rejects_invalid_account_type(client, db_session):
    token = _register_and_login(client, "bc_e@example.com")
    resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "not-real"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422


def test_patch_sets_bridge_url(client, db_session):
    token = _register_and_login(client, "bc_f@example.com")
    create_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    credential_id = create_resp.json()["credential_id"]

    resp = client.patch(
        f"/broker-credentials/{credential_id}",
        json={"bridge_url": "http://38.247.137.208:8002"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["bridge_configured"] is True


def test_patch_404_for_another_users_credential(client, db_session):
    token_a = _register_and_login(client, "bc_g@example.com")
    token_b = _register_and_login(client, "bc_h@example.com")
    create_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token_b),
    )
    credential_id = create_resp.json()["credential_id"]

    resp = client.patch(
        f"/broker-credentials/{credential_id}",
        json={"is_active": False},
        headers=_auth_header(token_a),
    )
    assert resp.status_code == 404


def test_list_broker_credentials_requires_auth(client):
    resp = client.get("/broker-credentials")
    assert resp.status_code == 401
