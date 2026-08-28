"""
Registers a new ProvisioningMachine and mints its machine token -- the
one credential that lets a poller claim self-service provisioning jobs
(see app/routers/internal_provisioning.py). Deliberately a manually-run
script, not an HTTP endpoint: User has no role/admin concept today, so
there's no correct way to gate "who may create something that can see
multiple users' plaintext MT5 passwords in flight" behind a JWT yet.
Mirrors app/scripts/backfill_user_defaults.py's exact pattern (a plain
`python -m` entrypoint, no web exposure).

The printed token is shown exactly ONCE, same convention as
issue_bridge_token() in app/routers/broker_credentials.py -- only its
hash is ever stored. Save it immediately; there is no way to retrieve it
again (re-running this script for the same --label fails with a unique-
constraint error rather than silently rotating it -- use
--rotate-token explicitly to get a fresh one for an existing machine).

Run manually:
    python -m app.scripts.register_provisioning_machine --label vps-1 --max-accounts 5
    python -m app.scripts.register_provisioning_machine --label vps-1 --rotate-token
"""
import argparse
import secrets

from app.core.database import SessionLocal
from app.core.security import hash_service_token
from app.models.provisioning_machine import ProvisioningMachine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Human-readable machine name, e.g. 'vps-1'")
    parser.add_argument(
        "--max-accounts", type=int, help="Capacity guard: max simultaneous provisioned accounts on this machine"
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="Mint a fresh token for an existing machine instead of creating a new one (invalidates the old token)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(ProvisioningMachine).filter(ProvisioningMachine.label == args.label).first()

        if args.rotate_token:
            if existing is None:
                raise SystemExit(f"No machine registered with label '{args.label}' -- nothing to rotate.")
            machine = existing
        else:
            if existing is not None:
                raise SystemExit(
                    f"Machine '{args.label}' already exists. Use --rotate-token to mint it a fresh token, "
                    f"or pick a different --label."
                )
            if args.max_accounts is None:
                raise SystemExit("--max-accounts is required when registering a new machine.")
            machine = ProvisioningMachine(label=args.label, max_accounts=args.max_accounts)
            db.add(machine)

        token = secrets.token_urlsafe(32)
        machine.machine_token_hash = hash_service_token(token)
        db.commit()
        db.refresh(machine)

        print(f"Machine '{machine.label}' (max_accounts={machine.max_accounts}) -- machine_id={machine.machine_id}")
        print(f"Machine token (save this now -- shown only once, never recoverable): {token}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
