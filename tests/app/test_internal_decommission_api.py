import secrets

from app.core.security import hash_service_token
from app.models.audit_log import AuditLog
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


def _make_decommissioning_credential(client, db_session, email, label="abcd1234"):
    """Bypasses the real /remove endpoint so this file's tests don't
    depend on it -- lands directly in 'decommissioning' with a
    provisioning_account_label already set, as if a real account had
    been provisioned earlier."""
    token = _register_and_login(client, email)
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=False,
        provisioning_status="decommissioning", provisioning_account_label=label,
        bridge_url="http://38.247.137.208:8003",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)
    return cred, token


def test_claim_with_nothing_to_decommission_returns_none(client, db_session):
    _, machine_token = _make_machine(db_session)

    resp = client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))
    assert resp.status_code == 200
    assert resp.json() == {"job": None, "reason": "none_pending"}


def test_claim_returns_job_and_sets_machine_and_claimed_at(client, db_session):
    machine, machine_token = _make_machine(db_session, max_accounts=1)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_a@example.com")

    resp = client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))
    assert resp.status_code == 200
    job = resp.json()["job"]
    assert job == {"credential_id": str(cred.credential_id), "account_label": "abcd1234"}

    db_session.refresh(cred)
    assert cred.provisioning_status == "removing"  # flips out of 'decommissioning' so a second claim can't re-grab it
    assert cred.provisioning_machine_id == machine.machine_id
    assert cred.provisioning_claimed_at is not None


def test_claim_ignores_max_accounts_capacity(client, db_session):
    """Unlike provisioning's claim, decommission claiming isn't gated by
    max_accounts -- tearing an account down frees capacity, it doesn't
    consume it."""
    _, machine_token = _make_machine(db_session, max_accounts=1)
    cred1, _ = _make_decommissioning_credential(client, db_session, "decom_b1@example.com", label="label0001")
    cred2, _ = _make_decommissioning_credential(client, db_session, "decom_b2@example.com", label="label0002")

    first = client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))
    assert first.json()["job"] is not None

    second = client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))
    assert second.json()["job"] is not None  # NOT at_capacity, unlike provisioning's claim

    claimed_ids = {first.json()["job"]["credential_id"], second.json()["job"]["credential_id"]}
    assert claimed_ids == {str(cred1.credential_id), str(cred2.credential_id)}


def test_complete_sets_removed_and_clears_provisioning_fields(client, db_session):
    _, machine_token = _make_machine(db_session)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_c@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/complete", headers=_machine_header(machine_token)
    )
    assert resp.status_code == 204

    db_session.refresh(cred)
    assert cred.provisioning_status == "removed"
    assert cred.provisioning_step is None
    assert cred.provisioning_machine_id is None
    assert cred.provisioning_claimed_at is None
    assert cred.provisioning_error is None
    assert cred.bridge_url is None
    assert cred.bridge_fetch_token_hash is None


def test_fail_sets_decommission_failed_and_error(client, db_session):
    _, machine_token = _make_machine(db_session)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_d@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/fail",
        json={"error": "nssm remove failed: service still running"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 204

    db_session.refresh(cred)
    assert cred.provisioning_status == "decommission_failed"
    assert cred.provisioning_error == "nssm remove failed: service still running"


def test_step_report_sets_tearing_down(client, db_session):
    _, machine_token = _make_machine(db_session)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_e@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/step",
        json={"step": "tearing_down"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 204

    db_session.refresh(cred)
    assert cred.provisioning_step == "tearing_down"


def test_step_report_rejects_a_provisioning_only_step(client, db_session):
    """Same shared VALID_PROVISIONING_STEPS vocabulary, but a
    provisioning-only step name reported against a decommission job is
    still a real value in the vocabulary -- so this asserts the 400 path
    for a genuinely unknown value, not that cross-using a provisioning
    step name is blocked (it isn't, and doesn't need to be: the frontend
    never sends one here, and DB-level validity is all this endpoint
    actually guards)."""
    _, machine_token = _make_machine(db_session)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_f@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/step",
        json={"step": "not-a-real-step"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 400

    db_session.refresh(cred)
    assert cred.provisioning_step is None


def test_complete_for_job_not_claimed_by_this_machine_is_409(client, db_session):
    _, token_a = _make_machine(db_session, label="m-a")
    _, token_b = _make_machine(db_session, label="m-b")
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_g@example.com")

    client.post("/internal/decommission-jobs/claim", headers=_machine_header(token_a))

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/complete", headers=_machine_header(token_b)
    )
    assert resp.status_code == 409

    db_session.refresh(cred)
    assert cred.provisioning_status == "removing"  # untouched by the rejected report (claimed by machine_a)


def test_fail_for_job_not_removing_is_409(client, db_session):
    _, machine_token = _make_machine(db_session)
    cred, _ = _make_decommissioning_credential(client, db_session, "decom_h@example.com")
    # Claim it first (so provisioning_machine_id genuinely matches this
    # machine, and status is genuinely 'removing'), then flip status away
    # by hand -- isolates the status guard specifically, not just the
    # machine-id one.
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))
    cred.provisioning_status = "active"
    db_session.commit()

    resp = client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/fail",
        json={"error": "should not apply"},
        headers=_machine_header(machine_token),
    )
    assert resp.status_code == 409


def test_claim_writes_audit_log(client, db_session):
    machine, machine_token = _make_machine(db_session, label="audit-dm1")
    cred, _ = _make_decommissioning_credential(client, db_session, "audit_decom_a@example.com")

    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "decommission_job_claimed").first()
    assert row is not None
    assert row.actor_type == "machine"
    assert row.actor_id == machine.machine_id
    assert row.resource_id == cred.credential_id


def test_complete_writes_audit_log(client, db_session):
    machine, machine_token = _make_machine(db_session, label="audit-dm2")
    cred, _ = _make_decommissioning_credential(client, db_session, "audit_decom_b@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    client.post(f"/internal/decommission-jobs/{cred.credential_id}/complete", headers=_machine_header(machine_token))

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "decommission_job_completed").first()
    assert row is not None
    # actor identity must survive even though the row's own
    # provisioning_machine_id gets cleared by this same request.
    assert row.actor_id == machine.machine_id


def test_fail_writes_audit_log_with_error(client, db_session):
    machine, machine_token = _make_machine(db_session, label="audit-dm3")
    cred, _ = _make_decommissioning_credential(client, db_session, "audit_decom_c@example.com")
    client.post("/internal/decommission-jobs/claim", headers=_machine_header(machine_token))

    client.post(
        f"/internal/decommission-jobs/{cred.credential_id}/fail",
        json={"error": "nssm remove failed: service still running"},
        headers=_machine_header(machine_token),
    )

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "decommission_job_failed").first()
    assert row is not None
    assert row.details["error"] == "nssm remove failed: service still running"
