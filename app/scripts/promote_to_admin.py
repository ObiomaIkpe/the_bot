"""
Grants (or revokes) is_admin on an existing user -- the one thing that
unlocks app/routers/admin.py's cross-user views in the React frontend.

Every user defaults to is_admin=True as of migration 0019 (2026-09-02
policy change -- see that migration's docstring), so this script is
mainly for --revoke now: taking a specific account back out of the
cross-user admin views without touching anyone else's. Still
deliberately a manually-run script, not an HTTP endpoint -- same
reasoning as app/scripts/register_provisioning_machine.py's own
docstring: there is no correct way to let a JWT-holder grant itself (or
anyone else) the ability to see every other user's data.

Run manually:
    python -m app.scripts.promote_to_admin --email you@example.com
    python -m app.scripts.promote_to_admin --email you@example.com --revoke
"""
import argparse

from app.core.database import SessionLocal
from app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Email of the existing user to promote/demote")
    parser.add_argument(
        "--revoke", action="store_true", help="Set is_admin=False instead of True"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if user is None:
            print(f"No user found with email {args.email!r}")
            return

        user.is_admin = not args.revoke
        db.commit()
        print(f"{args.email}: is_admin={user.is_admin}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
