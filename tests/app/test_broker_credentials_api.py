import secrets

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_service_token
from app.models.broker_credential import BrokerCredential
from app.models.provisioning_machine import ProvisioningMachine


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _make_machine(db_session, label="m1", max_accounts=1, is_active=True):
    token = secrets.token_urlsafe(32)
    machine = ProvisioningMachine(
        label=label, max_accounts=max_accounts, is_active=is_active, machine_token_hash=hash_service_token(token)
    )
    db_session.add(machine)
    db_session.commit()
    return machine


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


def test_patch_cannot_set_bridge_url(client, db_session):
    """bridge_url used to be user-settable via PATCH (migration 0008-era
    manual flow); removed from BrokerCredentialUpdate as part of
    self-service provisioning (Phase 0) since it's now meant to be
    trustworthy automated state -- see app/routers/internal_provisioning.py's
    /complete endpoint, the only thing allowed to set it now. An unknown
    field is silently dropped by pydantic, so a bridge_url-only payload
    ends up with nothing to change."""
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
    assert resp.status_code == 400
    assert resp.json()["detail"] == "No fields provided to update"

    resp = client.get("/broker-credentials", headers=_auth_header(token))
    assert resp.json()[0]["bridge_configured"] is False


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


def test_create_broker_credential_sets_pending_when_active_machine_exists(client, db_session):
    _make_machine(db_session)
    token = _register_and_login(client, "bc_p@example.com")

    resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201
    assert resp.json()["provisioning_status"] == "pending"


def test_create_broker_credential_stays_not_requested_with_no_active_machine(client, db_session):
    # No machine registered at all -- this is also today's real
    # production state until at least one is (see
    # app/scripts/register_provisioning_machine.py).
    token = _register_and_login(client, "bc_q@example.com")

    resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201
    assert resp.json()["provisioning_status"] == "not_requested"


def test_create_broker_credential_stays_not_requested_when_only_machine_is_inactive(client, db_session):
    _make_machine(db_session, is_active=False)
    token = _register_and_login(client, "bc_r@example.com")

    resp = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )
    assert resp.json()["provisioning_status"] == "not_requested"


def test_list_broker_credentials_includes_provisioning_fields(client, db_session):
    token = _register_and_login(client, "bc_s@example.com")
    client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    )

    body = client.get("/broker-credentials", headers=_auth_header(token)).json()
    assert body[0]["provisioning_status"] == "not_requested"
    assert body[0]["provisioning_step"] is None
    assert body[0]["provisioning_error"] is None


def test_retry_provisioning_resets_failed_job_to_pending(client, db_session):
    token = _register_and_login(client, "bc_t@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True,
        provisioning_status="failed", provisioning_error="MT5 login verification failed: bad password",
        provisioning_step="verifying_login", provisioning_account_label="abcd1234",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/retry-provisioning", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioning_status"] == "pending"
    assert body["provisioning_error"] is None
    assert body["provisioning_step"] is None

    db_session.refresh(cred)
    assert cred.provisioning_machine_id is None
    assert cred.provisioning_claimed_at is None
    # Deliberately preserved -- see the endpoint's own comment: the
    # poller's cleanup logic depends on this staying stable across retries.
    assert cred.provisioning_account_label == "abcd1234"


def test_retry_provisioning_409_when_not_failed(client, db_session):
    token = _register_and_login(client, "bc_u@example.com")
    credential_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]
    # Fresh row lands 'not_requested' (no machine registered) -- not 'failed'.

    resp = client.post(f"/broker-credentials/{credential_id}/retry-provisioning", headers=_auth_header(token))
    assert resp.status_code == 409


def test_retry_provisioning_404_for_another_users_credential(client, db_session):
    token_a = _register_and_login(client, "bc_v@example.com")
    token_b = _register_and_login(client, "bc_w@example.com")
    user_id_b = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id_b, broker_name="b", server="s", account_type="demo",
        is_active=True, provisioning_status="failed",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/retry-provisioning", headers=_auth_header(token_a))
    assert resp.status_code == 404


def test_remove_never_claimed_credential_is_immediate(client, db_session):
    """not_requested/pending with no provisioning_account_label -- nothing
    has ever touched a VPS, so this needs no machine and no job."""
    token = _register_and_login(client, "bc_x@example.com")
    credential_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]

    resp = client.post(f"/broker-credentials/{credential_id}/remove", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioning_status"] == "removed"
    assert body["is_active"] is False


def test_removed_credential_excluded_from_list_but_row_persists(client, db_session):
    token = _register_and_login(client, "bc_y@example.com")
    credential_id = client.post(
        "/broker-credentials",
        json={"broker_name": "b", "account_login": "1", "account_password": "p", "server": "s", "account_type": "demo"},
        headers=_auth_header(token),
    ).json()["credential_id"]
    client.post(f"/broker-credentials/{credential_id}/remove", headers=_auth_header(token))

    resp = client.get("/broker-credentials", headers=_auth_header(token))
    assert resp.json() == []

    row = db_session.query(BrokerCredential).filter(BrokerCredential.credential_id == credential_id).first()
    assert row is not None
    assert row.provisioning_status == "removed"


def test_remove_claimed_credential_requires_active_machine(client, db_session):
    """provisioning_account_label set (something was actually attempted)
    but no active ProvisioningMachine exists to run the real teardown --
    409, not a silent 'decommissioning' with nothing able to claim it."""
    token = _register_and_login(client, "bc_z@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True,
        provisioning_status="active", provisioning_account_label="abcd1234",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/remove", headers=_auth_header(token))
    assert resp.status_code == 409

    db_session.refresh(cred)
    assert cred.provisioning_status == "active"  # unchanged -- request was rejected, not half-applied


def test_remove_claimed_credential_starts_decommissioning_with_active_machine(client, db_session):
    _make_machine(db_session)
    token = _register_and_login(client, "bc_aa@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True,
        provisioning_status="active", provisioning_account_label="abcd1234", bridge_url="http://x:8002",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/remove", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioning_status"] == "decommissioning"
    assert body["is_active"] is False


@pytest.mark.parametrize("status", ["in_progress", "decommissioning", "removing"])
def test_remove_409_while_already_busy(client, db_session, status):
    token = _register_and_login(client, f"bc_busy_{status}@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True,
        provisioning_status=status, provisioning_account_label="abcd1234",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/remove", headers=_auth_header(token))
    assert resp.status_code == 409


def test_remove_404_for_another_users_credential(client, db_session):
    token_a = _register_and_login(client, "bc_bb@example.com")
    token_b = _register_and_login(client, "bc_cc@example.com")
    user_id_b = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    cred = BrokerCredential(
        user_id=user_id_b, broker_name="b", server="s", account_type="demo", is_active=True,
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)

    resp = client.post(f"/broker-credentials/{cred.credential_id}/remove", headers=_auth_header(token_a))
    assert resp.status_code == 404
