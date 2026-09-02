"""
Re-encrypts every broker_credentials row from an old
CREDENTIALS_ENCRYPTION_KEY to a new one -- the only safe way to rotate
this key.

A naive .env swap is NOT safe here, unlike JWT_SECRET_KEY or the
Postgres password: Fernet raises on the wrong key, with no partial or
fallback decode, so switching the key with no migration makes every
already-stored account_login_enc/account_password_enc permanently
undecryptable the instant the app next tries to read them -- including
the real live account's credentials.

Deliberately takes both keys as explicit arguments, not via
app.core.config.Settings -- Settings only ever holds ONE key at a time
(whatever's currently in .env), but this needs both simultaneously:
decrypt with the old, re-encrypt with the new. Everything runs inside a
single DB transaction -- if decryption fails on ANY row (wrong
--old-key, or a row somehow already on a different key), the whole
thing rolls back rather than leaving some rows on the old key and some
on the new.

Usage -- generate a new key first (must be real Fernet.generate_key()
output, NOT arbitrary random bytes/hex):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Dry-run first (decrypts/re-encrypts everything, never commits -- proves
--old-key is correct for every row before touching anything for real):
    python -m app.scripts.rotate_credentials_encryption_key \
        --old-key '<current CREDENTIALS_ENCRYPTION_KEY>' \
        --new-key '<freshly generated key>' \
        --dry-run

Then for real, same command minus --dry-run. Only AFTER this prints
success should CREDENTIALS_ENCRYPTION_KEY in .env be updated and `api`
restarted -- confirmed nothing else needs restarting for this specific
rotation: shadow_runner/bridge never decrypt credentials locally, the
bridge fetches plaintext once over HTTP via internal_bridge.py instead.
"""
import argparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.broker_credential import BrokerCredential


class RotationAborted(Exception):
    """Raised (and caught by main()) when --old-key fails to decrypt a
    row -- lets rotate() report exactly which row without the caller
    needing to inspect exception internals."""


def rotate(db: Session, old_key: str, new_key: str, dry_run: bool = False) -> int:
    """Returns the number of rows rotated. Raises RotationAborted (and
    rolls back, committing nothing) if any row fails to decrypt under
    old_key. Never logs/prints plaintext -- that's main()'s job to avoid
    entirely, not this function's.
    """
    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())

    rows = db.query(BrokerCredential).all()
    rotated = 0
    for row in rows:
        try:
            # Deliberately bypass the .account_login/.account_password
            # properties -- those decrypt via app.core.security's module-
            # level _fernet singleton, which is locked to whatever
            # settings.credentials_encryption_key currently is (the OLD
            # key, since .env hasn't been touched yet at this point).
            # Raw column access lets this function supply its own two
            # explicit Fernet instances instead.
            login = old_fernet.decrypt(row._account_login_enc.encode()).decode()
            password = old_fernet.decrypt(row._account_password_enc.encode()).decode()
        except InvalidToken:
            db.rollback()
            raise RotationAborted(str(row.credential_id))

        row._account_login_enc = new_fernet.encrypt(login.encode()).decode()
        row._account_password_enc = new_fernet.encrypt(password.encode()).decode()
        rotated += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return rotated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old-key", required=True, help="Current CREDENTIALS_ENCRYPTION_KEY")
    parser.add_argument("--new-key", required=True, help="New key to rotate to (Fernet.generate_key() output)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Decrypt/re-encrypt everything but never commit -- verifies --old-key works for every row first",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        try:
            count = rotate(db, args.old_key, args.new_key, dry_run=args.dry_run)
        except RotationAborted as e:
            print(
                f"ABORTED, nothing committed: credential_id={e} failed to decrypt "
                "with --old-key. Either --old-key is wrong, or this row is already "
                "on a different key. Fix and retry -- no partial rotation was written."
            )
            return

        if args.dry_run:
            print(f"DRY RUN: --old-key correctly decrypted all {count} row(s). Nothing was committed.")
        else:
            print(
                f"Rotated {count} broker_credentials row(s) to the new key. "
                "Now update CREDENTIALS_ENCRYPTION_KEY in .env and restart api."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
