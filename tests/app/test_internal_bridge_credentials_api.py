from app.models.audit_log import AuditLog


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _create_credential_and_mint_token(client, email, account_login="476123801", account_password="super-secret", server="Exness-MT5Trial9"):
    user_token = _register_and_login(client, email)
    create_resp = client.post(
        "/broker-credentials",
        json={
            "broker_name": "forex.com", "account_login": account_login,
            "account_password": account_password, "server": server, "account_type": "demo",
        },
        headers=_auth_header(user_token),
    )
    credential_id = create_resp.json()["credential_id"]
    mint_resp = client.post(f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(user_token))
    return credential_id, mint_resp.json()["bridge_token"], user_token


def test_fetch_credential_with_valid_token_returns_original_plaintext(client, db_session):
    _, bridge_token, _ = _create_credential_and_mint_token(
        client, "ib_a@example.com", account_login="1", account_password="super-secret", server="s",
    )

    resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": bridge_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"login": "1", "password": "super-secret", "server": "s"}


def test_fetch_credential_with_invalid_token_is_401(client, db_session):
    resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": "not-a-real-token"})
    assert resp.status_code == 401


def test_fetch_credential_with_missing_header_is_422(client, db_session):
    resp = client.get("/internal/bridge-credentials")
    assert resp.status_code == 422


def test_fetch_credential_for_inactive_credential_is_401(client, db_session):
    credential_id, bridge_token, user_token = _create_credential_and_mint_token(client, "ib_b@example.com")

    resp = client.patch(
        f"/broker-credentials/{credential_id}", json={"is_active": False}, headers=_auth_header(user_token),
    )
    assert resp.status_code == 200

    resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": bridge_token})
    assert resp.status_code == 401


def test_rotating_token_invalidates_the_old_one(client, db_session):
    credential_id, first_token, user_token = _create_credential_and_mint_token(client, "ib_c@example.com")

    ok_resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": first_token})
    assert ok_resp.status_code == 200

    second_token = client.post(
        f"/broker-credentials/{credential_id}/bridge-token", headers=_auth_header(user_token)
    ).json()["bridge_token"]

    old_resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": first_token})
    assert old_resp.status_code == 401

    new_resp = client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": second_token})
    assert new_resp.status_code == 200


def test_fetch_success_writes_audit_log(client, db_session):
    credential_id, bridge_token, _ = _create_credential_and_mint_token(client, "audit_ib_a@example.com")

    client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": bridge_token})

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "bridge_credentials_fetched").first()
    assert row is not None
    assert row.actor_type == "credential"
    assert str(row.actor_id) == credential_id
    assert str(row.resource_id) == credential_id


def test_fetch_with_invalid_token_writes_denied_audit_log(client, db_session):
    client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": "not-a-real-token"})

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "bridge_credentials_fetch_denied").first()
    assert row is not None
    assert row.actor_type == "unknown"
    assert row.actor_id is None
    assert row.details["reason"] == "not_found"
    assert row.details["token_hash"]  # a one-way hash, never the raw token


def test_fetch_for_inactive_credential_writes_denied_audit_log_with_actor(client, db_session):
    credential_id, bridge_token, user_token = _create_credential_and_mint_token(client, "audit_ib_b@example.com")
    client.patch(
        f"/broker-credentials/{credential_id}", json={"is_active": False}, headers=_auth_header(user_token),
    )

    client.get("/internal/bridge-credentials", headers={"X-Bridge-Token": bridge_token})

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "bridge_credentials_fetch_denied").first()
    assert row is not None
    assert row.actor_type == "credential"
    assert str(row.actor_id) == credential_id
    assert row.details["reason"] == "inactive"
