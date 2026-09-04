from app.models.audit_log import AuditLog
from app.routers import auth as auth_router


def test_register_creates_user(client):
    resp = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["is_active"] is True
    assert "user_id" in body


def test_register_duplicate_email_conflicts(client):
    payload = {"email": "dup@example.com", "password": "a-real-password"}
    client.post("/auth/register", json=payload)
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 409


def test_register_provisions_default_models_and_settings(client):
    resp = client.post(
        "/auth/register",
        json={"email": "provision_a@example.com", "password": "a-real-password"},
    )
    token = client.post(
        "/auth/login",
        data={"username": "provision_a@example.com", "password": "a-real-password"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    configs = client.get("/model-configs", headers=headers).json()
    assert {c["model_name"] for c in configs} == {"fvg", "ob", "fvg_ob"}
    assert all(c["status"] == "disabled" for c in configs), "new models must never auto-activate"
    assert len({c["magic_number"] for c in configs}) == 3, "each model gets its own magic number"

    settings_resp = client.get("/settings", headers=headers)
    assert settings_resp.status_code == 200, "a settings row must exist immediately, not 404"
    assert settings_resp.json()["is_paused"] is False


def test_register_allocates_distinct_magic_numbers_across_users(client):
    for email in ("provision_b@example.com", "provision_c@example.com"):
        client.post("/auth/register", json={"email": email, "password": "a-real-password"})

    token_b = client.post(
        "/auth/login", data={"username": "provision_b@example.com", "password": "a-real-password"}
    ).json()["access_token"]
    token_c = client.post(
        "/auth/login", data={"username": "provision_c@example.com", "password": "a-real-password"}
    ).json()["access_token"]

    magics_b = {c["magic_number"] for c in client.get("/model-configs", headers={"Authorization": f"Bearer {token_b}"}).json()}
    magics_c = {c["magic_number"] for c in client.get("/model-configs", headers={"Authorization": f"Bearer {token_c}"}).json()}

    assert magics_b.isdisjoint(magics_c), "magic numbers must be globally unique, not just per user"


def test_provision_new_user_defaults_is_idempotent(client, db_session):
    from app.core.provisioning import provision_new_user_defaults
    from app.models.model_config import ModelConfig
    from app.models.user_settings import UserSettings

    client.post(
        "/auth/register",
        json={"email": "provision_d@example.com", "password": "a-real-password"},
    )
    token = client.post(
        "/auth/login", data={"username": "provision_d@example.com", "password": "a-real-password"}
    ).json()["access_token"]
    user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["user_id"]

    # Registration already called this once -- calling it again directly
    # (as the backfill script would for an already-provisioned user)
    # must be a no-op, not a duplicate-row error.
    provision_new_user_defaults(db_session, user_id)

    assert db_session.query(ModelConfig).filter_by(user_id=user_id).count() == 3
    assert db_session.query(UserSettings).filter_by(user_id=user_id).count() == 1


def test_login_success_returns_token(client):
    client.post(
        "/auth/register",
        json={"email": "login@example.com", "password": "a-real-password"},
    )
    resp = client.post(
        "/auth/login",
        data={"username": "login@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "wrongpw@example.com", "password": "a-real-password"},
    )
    resp = client.post(
        "/auth/login",
        data={"username": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert resp.status_code == 401


def test_login_unknown_user_rejected(client):
    resp = client.post(
        "/auth/login",
        data={"username": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user_with_valid_token(client):
    client.post(
        "/auth/register",
        json={"email": "me@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "me@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


def test_change_password_requires_auth(client):
    resp = client.post(
        "/auth/change-password",
        json={"current_password": "a-real-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 401


def test_change_password_wrong_current_password_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_a@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_a@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "not-the-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 400


def test_change_password_too_short_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_b@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_b@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "a-real-password", "new_password": "short"},
    )
    assert resp.status_code == 422


def test_change_password_succeeds_and_old_password_stops_working(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_c@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "a-real-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "pwchange_c@example.com"

    old_login = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-real-password"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-new-password"},
    )
    assert new_login.status_code == 200


def test_register_writes_audit_log(client, db_session):
    resp = client.post(
        "/auth/register",
        json={"email": "audit_reg@example.com", "password": "a-real-password"},
    )
    user_id = resp.json()["user_id"]

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "user_registered").first()
    assert row is not None
    assert row.actor_type == "user"
    assert str(row.actor_id) == user_id
    assert row.actor_label == "audit_reg@example.com"


def test_register_audit_log_failure_does_not_hide_a_real_successful_registration(client, monkeypatch):
    """2026-09-04 write-path audit fix: the user account (and their
    model configs) are already durably committed before this final
    audit-log commit even runs. Proves a failure there no longer 500s
    a registration that actually succeeded."""
    def broken_write_audit_log(db, event_type, actor_type, **kwargs):
        raise Exception("simulated audit log write failure")

    monkeypatch.setattr(auth_router, "write_audit_log", broken_write_audit_log)

    resp = client.post(
        "/auth/register",
        json={"email": "audit_fail_reg@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 201, "the account itself already committed -- must not report failure"
    assert resp.json()["email"] == "audit_fail_reg@example.com"

    monkeypatch.undo()
    login_resp = client.post(
        "/auth/login", data={"username": "audit_fail_reg@example.com", "password": "a-real-password"},
    )
    assert login_resp.status_code == 200, "the account must actually be usable"


def test_login_success_writes_audit_log(client, db_session):
    client.post("/auth/register", json={"email": "audit_login@example.com", "password": "a-real-password"})
    client.post(
        "/auth/login", data={"username": "audit_login@example.com", "password": "a-real-password"},
    )

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "login_succeeded").first()
    assert row is not None
    assert row.actor_type == "user"
    assert row.actor_label == "audit_login@example.com"


def test_login_wrong_password_writes_audit_log_with_reason(client, db_session):
    client.post("/auth/register", json={"email": "audit_badpw@example.com", "password": "a-real-password"})
    client.post(
        "/auth/login", data={"username": "audit_badpw@example.com", "password": "not-the-password"},
    )

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "login_failed").first()
    assert row is not None
    assert row.actor_type == "unknown"
    assert row.actor_label == "audit_badpw@example.com"
    assert row.details["reason"] == "bad_password"
    assert row.resource_type == "user"  # a real user matched by email, even though unverified


def test_login_unknown_email_writes_audit_log_with_no_resource(client, db_session):
    client.post("/auth/login", data={"username": "nobody_audit@example.com", "password": "whatever"})

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "login_failed").first()
    assert row is not None
    assert row.details["reason"] == "unknown_email"
    assert row.resource_id is None


def test_login_rejected_inactive_writes_audit_log(client, db_session):
    from app.models.user import User

    client.post("/auth/register", json={"email": "audit_inactive@example.com", "password": "a-real-password"})
    user = db_session.query(User).filter(User.email == "audit_inactive@example.com").first()
    user.is_active = False
    db_session.commit()

    client.post(
        "/auth/login", data={"username": "audit_inactive@example.com", "password": "a-real-password"},
    )

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "login_rejected_inactive").first()
    assert row is not None
    assert row.actor_type == "user"
    assert str(row.actor_id) == str(user.user_id)


def test_change_password_writes_audit_log(client, db_session):
    client.post("/auth/register", json={"email": "audit_pwchange@example.com", "password": "a-real-password"})
    token = client.post(
        "/auth/login", data={"username": "audit_pwchange@example.com", "password": "a-real-password"},
    ).json()["access_token"]

    client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "a-real-password", "new_password": "a-new-password"},
    )

    row = db_session.query(AuditLog).filter(AuditLog.event_type == "password_changed").first()
    assert row is not None
    assert row.actor_label == "audit_pwchange@example.com"
