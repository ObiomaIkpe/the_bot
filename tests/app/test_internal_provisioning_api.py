import secrets

from app.core.security import hash_service_token
from app.models.broker_credential import BrokerCredential
from app.models.provisioning_machine import ProvisioningMachine


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _machine_header(token):
    return {"X-Machine-Token": token}


def _make_machine(db_session, label="m1", max_accounts=1, is_active=True):
    token = secrets.token_urlsafe(32)
    machine = ProvisioningMachine(
        label=label, max_accounts=max_accounts, is_active=is_active, machine_token_hash=hash_service_token(token)
    )
    db_session.add(machine)
    db_session.commit()
    db_session.refresh(machine)
    return machine, token


def _make_pending_credential(client, db_session, email, account_login="476123801", account_password="super-secret"):
    """Bypasses the real create endpoint's provisioning_status default
    (still 'not_requested' in Phase 0 -- see broker_credentials.py) since
    nothing sets 'pending' automatically yet. Registering the user still
    goes through the real endpoint, so provision_new_user_defaults() runs
    and this user gets real ModelConfig rows (used by the magic_numbers
    assertions below)."""
    token = _register_and_login(client, email)
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True,
        provisioning_status="pending",
    )
    cred.account_login = account_login
    cred.account_password = account_password
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)
    return cred, token


def test_claim_with_no_pending_jobs_returns_none(client, db_session):
    _, machine_token = _make_machine(db_session)

    resp = client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))
    assert resp.status_code == 200
    assert resp.json() == {"job": None, "reason": "none_pending"}


def test_claim_returns_job_and_flips_status_to_in_progress(client, db_session):
    machine, machine_token = _make_machine(db_session, max_accounts=5)
    cred, _ = _make_pending_credential(client, db_session, "prov_a@example.com")

    resp = client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))
    assert resp.status_code == 200
    job = resp.json()["job"]
    assert job is not None
    assert job["credential_id"] == str(cred.credential_id)
    assert job["account_login"] == "476123801"
    assert job["account_password"] == "super-secret"
    assert job["server"] == "s"
    assert len(job["magic_numbers"]) == 3  # ALL_MODEL_NAMES -- auto-provisioned at registration
    assert job["bridge_token"]

    db_session.refresh(cred)
    assert cred.provisioning_status == "in_progress"
    assert cred.provisioning_machine_id == machine.machine_id
    assert cred.provisioning_claimed_at is not None
    assert cred.provisioning_account_label == str(cred.credential_id)[:8]
    # The returned plaintext token must verify against what got stored
    assert cred.bridge_fetch_token_hash == hash_service_token(job["bridge_token"])


def test_claim_at_capacity_does_not_claim_and_returns_reason(client, db_session):
    machine, machine_token = _make_machine(db_session, max_accounts=1)
    cred1, _ = _make_pending_credential(client, db_session, "prov_b1@example.com")
    cred2, _ = _make_pending_credential(client, db_session, "prov_b2@example.com")

    first = client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))
    claimed_id = first.json()["job"]["credential_id"]
    assert claimed_id in (str(cred1.credential_id), str(cred2.credential_id))

    second = client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))
    assert second.status_code == 200
    assert second.json() == {"job": None, "reason": "at_capacity"}

    # Exactly one of the two got claimed (order between them is an
    # unspecified tiebreak -- see claim_provisioning_job()'s own comment);
    # the other must remain untouched, still available for another machine.
    db_session.refresh(cred1)
    db_session.refresh(cred2)
    statuses = {str(cred1.credential_id): cred1.provisioning_status, str(cred2.credential_id): cred2.provisioning_status}
    assert statuses[claimed_id] == "in_progress"
    other_id = str(cred2.credential_id) if claimed_id == str(cred1.credential_id) else str(cred1.credential_id)
    assert statuses[other_id] == "pending"


def test_claim_with_invalid_machine_token_is_401(client, db_session):
    resp = client.post("/internal/provisioning-jobs/claim", headers=_machine_header("not-a-real-token"))
    assert resp.status_code == 401


def test_claim_with_inactive_machine_is_401(client, db_session):
    _, machine_token = _make_machine(db_session, is_active=False)
    resp = client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))
    assert resp.status_code == 401


def test_claim_with_missing_header_is_422(client, db_session):
    resp = client.post("/internal/provisioning-jobs/claim")
    assert resp.status_code == 422


def test_complete_sets_active_and_bridge_url(client, db_session):
    _, machine_token = _make_machine(db_session, max_accounts=5)
    cred, _ = _make_pending_credential(client, db_session, "prov_c@example.com")
    client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/provisioning-jobs/{cred.credential_id}/complete",
        json={"bridge_url": "http://38.247.137.208:8003"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 204

    db_session.refresh(cred)
    assert cred.provisioning_status == "active"
    assert cred.bridge_url == "http://38.247.137.208:8003"
    assert cred.provisioning_error is None


def test_fail_sets_failed_and_error(client, db_session):
    _, machine_token = _make_machine(db_session, max_accounts=5)
    cred, _ = _make_pending_credential(client, db_session, "prov_d@example.com")
    client.post("/internal/provisioning-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/provisioning-jobs/{cred.credential_id}/fail",
        json={"error": "MT5 login rejected: invalid password"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 204

    db_session.refresh(cred)
    assert cred.provisioning_status == "failed"
    assert cred.provisioning_error == "MT5 login rejected: invalid password"


def test_complete_for_job_not_claimed_by_this_machine_is_409(client, db_session):
    machine_a, token_a = _make_machine(db_session, label="m-a", max_accounts=5)
    machine_b, token_b = _make_machine(db_session, label="m-b", max_accounts=5)
    cred, _ = _make_pending_credential(client, db_session, "prov_e@example.com")

    client.post("/internal/provisioning-jobs/claim", headers=_machine_header(token_a))

    resp = client.post(
        f"/internal/provisioning-jobs/{cred.credential_id}/complete",
        json={"bridge_url": "http://example.invalid:8001"},
        headers=_machine_header(token_b),
    )
    assert resp.status_code == 409

    db_session.refresh(cred)
    assert cred.provisioning_status == "in_progress"  # untouched by the rejected report


def test_fail_for_job_not_in_progress_is_409(client, db_session):
    _, machine_token = _make_machine(db_session, max_accounts=5)
    cred, _ = _make_pending_credential(client, db_session, "prov_f@example.com")
    # Never claimed -- still 'pending', not 'in_progress'.

    resp = client.post(
        f"/internal/provisioning-jobs/{cred.credential_id}/fail",
        json={"error": "should not apply"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 409
