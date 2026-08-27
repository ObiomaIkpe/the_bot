"""
One-time (but safe to re-run) backfill: gives every existing user their
default model_configs + user_settings rows, via the exact same
idempotent provision_new_user_defaults() that registration now calls
automatically for new signups. Needed because a registration-time hook
alone never reaches anyone who registered before it existed.

Run manually:
    python -m app.scripts.backfill_user_defaults
"""
from app.core.database import SessionLocal
from app.core.provisioning import provision_new_user_defaults
from app.models.user import User


def main() -> None:
    db = SessionLocal()
    try:
        users = db.query(User).all()
        for user in users:
            provision_new_user_defaults(db, user.user_id)
            print(f"Provisioned defaults for {user.email} ({user.user_id})")
        print(f"Done -- checked {len(users)} user(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
