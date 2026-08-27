import pytest
from sqlalchemy.exc import IntegrityError

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


def test_issue_bridge_token_requires_auth(client):
    resp = client.post("/broker-credentials/00000000-0000-0000-0000-000000000000/bridge-token")
    assert resp.status_code == 401


def test_issue_bridge_token_returns_token_and_sets_hash(client, db_session):
    token = _register_and_login(client, "bc_i@example.com")
    create_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    credential_id = create_resp.json()["credential_id"]

    resp = client.post(f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(token))
    assert resp.status_code == 200
    bridge_token = resp.json()["bridge_token"]
    assert bridge_token

    row = db_session.query(BrokerCredential).filter(BrokerCredential.credential_id == credential_id).first()
    assert row.bridge_fetch_token_hash is not None
    assert row.bridge_fetch_token_hash != bridge_token


def test_issue_bridge_token_404_for_another_users_credential(client, db_session):
    token_a = _register_and_login(client, "bc_j@example.com")
    token_b = _register_and_login(client, "bc_k@example.com")
    create_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token_b),
    )
    credential_id = create_resp.json()["credential_id"]

    resp = client.post(f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(token_a))
    assert resp.status_code == 404


def test_reissuing_bridge_token_rotates_it(client, db_session):
    token = _register_and_login(client, "bc_l@example.com")
    create_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    credential_id = create_resp.json()["credential_id"]

    first = client.post(f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(token)).json()["bridge_token"]
    second = client.post(f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(token)).json()["bridge_token"]
    assert first != second


def test_creating_second_credential_deactivates_the_first(client, db_session):
    token = _register_and_login(client, "bc_m@example.com")
    first_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b1", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]

    second_resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b2", "account_login": "2", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    assert second_resp.status_code == 201
    assert second_resp.json()["is_active"] is True

    creds = {c["credential_id"]: c["is_active"] for c in client.get("/broker-credentials", headers=_auth_header(token)).json()}
    assert creds[first_id] is False
    assert creds[second_resp.json()["credential_id"]] is True


def test_patch_activating_one_credential_deactivates_the_other(client, db_session):
    token = _register_and_login(client, "bc_n@example.com")
    first_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b1", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]
    second_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b2", "account_login": "2", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]
    # Creating the second already deactivated the first -- explicitly
    # re-activate it and confirm the second flips off in response.
    resp = client.patch(f"/broker-credentials/{first_id}", json={"is_active": True}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    creds = {c["credential_id"]: c["is_active"] for c in client.get("/broker-credentials", headers=_auth_header(token)).json()}
    assert creds[first_id] is True
    assert creds[second_id] is False


def test_db_rejects_two_active_credentials_for_same_user(client, db_session):
    """Bypasses the router entirely -- proves the partial unique index
    itself (migration 0010), not just the application logic sitting on
    top of it."""
    token = _register_and_login(client, "bc_o@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    cred1 = BrokerCredential(user_id=user_id, broker_name="b1", server="s", account_type="demo", is_active=True)
    cred1.account_login = "1"
    cred1.account_password = "p"
    db_session.add(cred1)
    db_session.commit()

    cred2 = BrokerCredential(user_id=user_id, broker_name="b2", server="s", account_type="demo", is_active=True)
    cred2.account_login = "2"
    cred2.account_password = "p"
    db_session.add(cred2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
