"""
Closes the first unverified item from the Phase 0 README: does
BrokerCredential's encrypt/decrypt round-trip actually work end to end
against a real Postgres row (not just in-memory), and -- just as
important -- is the value genuinely encrypted at rest, not merely passed
through unchanged.

ASSUMPTION: this uses a `db_session` fixture (a SQLAlchemy Session scoped to a
real Postgres DB via Alembic migrations, wrapped in a rolled-back
transaction per your conftest.py description). If your conftest.py names
this fixture something else, rename the parameter below to match --
everything else should work unchanged.
"""
import uuid

import pytest

from app.core.security import decrypt_secret
from app.models.broker_credential import BrokerCredential
from app.models.user import User


def _make_user(db_session) -> User:
    user = User(
        email=f"credtest-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="not-a-real-hash-this-user-is-only-for-fk-purposes",
    )
    db_session.add(user)
    db_session.flush()  # get user.user_id without committing
    return user


def test_round_trip_returns_original_plaintext(db_session):
    user = _make_user(db_session)

    plaintext_login = "1234567"
    plaintext_password = "S0meReal-ish-Password!"

    cred = BrokerCredential(
        user_id=user.user_id,
        broker_name="forex.com",
        server="FOREXcom-Demo",
        account_type="demo",
    )
    # Set via the properties, not the raw _*_enc columns -- this is the
    # actual code path every caller is supposed to use.
    cred.account_login = plaintext_login
    cred.account_password = plaintext_password

    db_session.add(cred)
    db_session.flush()

    # Force a real round-trip through Postgres: clear the identity map so
    # the next access re-fetches the row rather than returning the same
    # Python object we just built in memory.
    db_session.expire_all()

    reloaded = db_session.get(BrokerCredential, cred.credential_id)
    assert reloaded is not None

    assert reloaded.account_login == plaintext_login
    assert reloaded.account_password == plaintext_password


def test_stored_value_is_not_plaintext(db_session):
    """
    A round-trip that "works" because encryption silently no-ops would
    still pass test_round_trip_returns_original_plaintext above -- this
    test exists specifically to catch that failure mode by inspecting the
    raw encrypted column.
    """
    user = _make_user(db_session)

    plaintext_password = "AnotherRealPassword!42"

    cred = BrokerCredential(
        user_id=user.user_id,
        broker_name="forex.com",
        server="FOREXcom-Demo",
        account_type="demo",
    )
    cred.account_login = "7654321"
    cred.account_password = plaintext_password

    db_session.add(cred)
    db_session.flush()
    db_session.expire_all()

    reloaded = db_session.get(BrokerCredential, cred.credential_id)

    # The raw encrypted column must NOT equal the plaintext...
    assert reloaded._account_password_enc != plaintext_password
    # ...and it must actually decrypt back to it via the real decrypt path
    # (belt-and-suspenders vs. calling the .account_password property,
    # which is what test_round_trip already exercises).
    assert decrypt_secret(reloaded._account_password_enc) == plaintext_password


def test_two_credentials_with_same_password_have_different_ciphertext(db_session):
    """
    Fernet includes a random IV per encryption, so encrypting the same
    plaintext twice should never produce identical ciphertext. Not
    critical to correctness, but a cheap check against an accidental
    switch to a deterministic cipher mode later.
    """
    user = _make_user(db_session)

    cred_a = BrokerCredential(
        user_id=user.user_id, broker_name="forex.com", server="FOREXcom-Demo", account_type="demo"
    )
    cred_a.account_login = "111"
    cred_a.account_password = "SamePassword123"

    cred_b = BrokerCredential(
        user_id=user.user_id, broker_name="forex.com", server="FOREXcom-Demo", account_type="demo"
    )
    cred_b.account_login = "222"
    cred_b.account_password = "SamePassword123"

    db_session.add_all([cred_a, cred_b])
    db_session.flush()

    assert cred_a._account_password_enc != cred_b._account_password_enc
