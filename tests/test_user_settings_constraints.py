"""
Closes the second unverified item from the Phase 0 README: does
`CheckConstraint("NOT (live_model = ANY(shadow_models))")` actually get
created and enforced by real Postgres, or does it silently fail to apply
(e.g. if the ANY(array_column) syntax weren't valid in a CHECK context).

Same fixture assumption as test_broker_credential_encryption.py: a `db_session`
fixture yielding a Session against a real, migrated Postgres DB. Rename
if yours differs.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.user_settings import UserSettings


def _make_user(db_session) -> User:
    user = User(
        email=f"constrainttest-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash-this-user-is-only-for-fk-purposes",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_live_model_in_shadow_models_is_rejected(db_session):
    user = _make_user(db_session)

    settings = UserSettings(
        user_id=user.user_id,
        live_model="fvg",
        shadow_models=["fvg", "ob"],  # invalid: 'fvg' is both live AND shadow
        risk_pct=1.0,
        max_daily_loss_pct=5.0,
    )
    db_session.add(settings)

    # Use a SAVEPOINT so the expected failure doesn't poison the outer
    # per-test transaction that the `db_session` fixture is going to roll back
    # anyway.
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()

    # begin_nested()'s context manager already rolled back to the
    # SAVEPOINT on the IntegrityError above -- db_session is usable again here,
    # still inside the outer per-test transaction the `db_session` fixture owns.


def test_live_model_not_in_shadow_models_is_accepted(db_session):
    """The negative test above is only meaningful if the valid case still
    passes -- otherwise the constraint could be rejecting everything."""
    user = _make_user(db_session)

    settings = UserSettings(
        user_id=user.user_id,
        live_model="fvg",
        shadow_models=["ob", "fvg_ob"],  # valid: disjoint from live_model
        risk_pct=1.0,
        max_daily_loss_pct=5.0,
    )
    db_session.add(settings)
    db_session.flush()  # should not raise

    db_session.expire_all()
    reloaded = db_session.get(UserSettings, settings.setting_id)
    assert reloaded is not None
    assert reloaded.live_model == "fvg"
    assert set(reloaded.shadow_models) == {"ob", "fvg_ob"}


def test_invalid_live_model_value_is_rejected(db_session):
    """Sanity check on the other CHECK constraint on this table while
    we're in here -- live_model must be one of the three real models."""
    user = _make_user(db_session)

    settings = UserSettings(
        user_id=user.user_id,
        live_model="not_a_real_model",
        shadow_models=[],
        risk_pct=1.0,
        max_daily_loss_pct=5.0,
    )
    db_session.add(settings)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()
