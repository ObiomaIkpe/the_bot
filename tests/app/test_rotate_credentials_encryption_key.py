"""
Tests for app.scripts.rotate_credentials_encryption_key -- the only
safe way to change CREDENTIALS_ENCRYPTION_KEY (see that script's own
module docstring for why a naive .env swap would brick every stored
broker credential). Encrypts fixture rows directly with a known
"old key" Fernet instance, independent of whatever
settings.credentials_encryption_key happens to be in the test
environment -- this needs precise control over which key a row is
actually encrypted under.
"""
from cryptography.fernet import Fernet

from app.models.broker_credential import BrokerCredential
from app.models.user import User
from app.scripts.rotate_credentials_encryption_key import RotationAborted, rotate

OLD_KEY = Fernet.generate_key().decode()
NEW_KEY = Fernet.generate_key().decode()


def _make_user(db_session, email):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_credential_under_key(db_session, user, key, login, password):
    fernet = Fernet(key.encode())
    cred = BrokerCredential(
        user_id=user.user_id, broker_name="b", server="s", account_type="demo", is_active=True,
    )
    # Raw column assignment, bypassing the .account_login/.account_password
    # properties -- those always encrypt under whatever
    # settings.credentials_encryption_key is right now, not the specific
    # `key` this test needs.
    cred._account_login_enc = fernet.encrypt(login.encode()).decode()
    cred._account_password_enc = fernet.encrypt(password.encode()).decode()
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)
    return cred


def test_rotate_re_encrypts_under_new_key(db_session):
    user = _make_user(db_session, "rotate_a@example.com")
    cred = _make_credential_under_key(db_session, user, OLD_KEY, "12345", "hunter2")

    count = rotate(db_session, OLD_KEY, NEW_KEY)
    assert count == 1

    db_session.refresh(cred)
    new_fernet = Fernet(NEW_KEY.encode())
    assert new_fernet.decrypt(cred._account_login_enc.encode()).decode() == "12345"
    assert new_fernet.decrypt(cred._account_password_enc.encode()).decode() == "hunter2"

    # The old key must no longer work -- proves this really re-encrypted,
    # not just left the old ciphertext in place.
    old_fernet = Fernet(OLD_KEY.encode())
    try:
        old_fernet.decrypt(cred._account_login_enc.encode())
        assert False, "old key should no longer decrypt this row"
    except Exception:
        pass


def test_rotate_handles_multiple_rows(db_session):
    # Two different users -- uq_broker_credentials_one_active_per_user
    # allows only one *active* credential per user, and this rotation
    # script doesn't care about active/inactive either way.
    user_a = _make_user(db_session, "rotate_b1@example.com")
    user_b = _make_user(db_session, "rotate_b2@example.com")
    _make_credential_under_key(db_session, user_a, OLD_KEY, "111", "pw1")
    _make_credential_under_key(db_session, user_b, OLD_KEY, "222", "pw2")

    count = rotate(db_session, OLD_KEY, NEW_KEY)
    assert count == 2


def test_dry_run_rotates_nothing_for_real(db_session):
    user = _make_user(db_session, "rotate_c@example.com")
    cred = _make_credential_under_key(db_session, user, OLD_KEY, "999", "pw")

    count = rotate(db_session, OLD_KEY, NEW_KEY, dry_run=True)
    assert count == 1

    db_session.refresh(cred)
    # Still decryptable under the OLD key -- nothing was actually committed.
    old_fernet = Fernet(OLD_KEY.encode())
    assert old_fernet.decrypt(cred._account_login_enc.encode()).decode() == "999"


def test_wrong_old_key_aborts_without_committing_anything(db_session):
    user = _make_user(db_session, "rotate_d@example.com")
    cred = _make_credential_under_key(db_session, user, OLD_KEY, "1", "pw")

    wrong_key = Fernet.generate_key().decode()
    try:
        rotate(db_session, wrong_key, NEW_KEY)
        assert False, "should have raised RotationAborted"
    except RotationAborted:
        pass

    db_session.refresh(cred)
    # Untouched -- still decryptable under the real old key.
    old_fernet = Fernet(OLD_KEY.encode())
    assert old_fernet.decrypt(cred._account_login_enc.encode()).decode() == "1"


def test_one_bad_row_aborts_the_whole_batch(db_session):
    """A row already on a different key (e.g. a half-finished prior
    rotation attempt) must abort everything, not silently skip just
    that row -- partial rotation is exactly the dangerous state this
    script exists to prevent."""
    user_a = _make_user(db_session, "rotate_e1@example.com")
    user_b = _make_user(db_session, "rotate_e2@example.com")
    _make_credential_under_key(db_session, user_a, OLD_KEY, "good", "pw")
    other_key = Fernet.generate_key().decode()
    bad_cred = _make_credential_under_key(db_session, user_b, other_key, "bad", "pw")

    try:
        rotate(db_session, OLD_KEY, NEW_KEY)
        assert False, "should have raised RotationAborted"
    except RotationAborted:
        pass

    db_session.refresh(bad_cred)
    other_fernet = Fernet(other_key.encode())
    assert other_fernet.decrypt(bad_cred._account_login_enc.encode()).decode() == "bad"
