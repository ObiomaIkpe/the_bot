import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.audit import write_audit_log
from app.models.audit_log import AuditLog


def test_write_audit_log_persists_all_fields(db_session):
    user_id = uuid.uuid4()
    resource_id = uuid.uuid4()

    row = write_audit_log(
        db_session, "user_registered", "user",
        actor_id=user_id, actor_label="a@example.com",
        resource_type="user", resource_id=resource_id,
        details={"foo": "bar"}, ip_address="203.0.113.5",
    )
    db_session.commit()

    fetched = db_session.query(AuditLog).filter(AuditLog.audit_id == row.audit_id).first()
    assert fetched is not None
    assert fetched.event_type == "user_registered"
    assert fetched.actor_type == "user"
    assert fetched.actor_id == user_id
    assert fetched.actor_label == "a@example.com"
    assert fetched.resource_type == "user"
    assert fetched.resource_id == resource_id
    assert fetched.details == {"foo": "bar"}
    assert fetched.ip_address == "203.0.113.5"
    assert fetched.timestamp is not None


def test_write_audit_log_defaults_details_to_empty_dict(db_session):
    row = write_audit_log(db_session, "login_succeeded", "user", actor_id=uuid.uuid4())
    db_session.commit()

    fetched = db_session.query(AuditLog).filter(AuditLog.audit_id == row.audit_id).first()
    assert fetched.details == {}
    assert fetched.actor_label is None
    assert fetched.resource_type is None


def test_actor_type_check_constraint_rejects_bogus_value(db_session):
    row = AuditLog(audit_id=uuid.uuid4(), actor_type="not-a-real-actor-type", event_type="user_registered")
    db_session.add(row)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_actor_type_allows_unknown_with_no_actor_id(db_session):
    """The one actor_type that legitimately has no actor_id -- an
    unverified identity (e.g. a failed login's attempted email)."""
    row = write_audit_log(db_session, "login_failed", "unknown", actor_label="nobody@example.com")
    db_session.commit()

    fetched = db_session.query(AuditLog).filter(AuditLog.audit_id == row.audit_id).first()
    assert fetched.actor_type == "unknown"
    assert fetched.actor_id is None
    assert fetched.actor_label == "nobody@example.com"
