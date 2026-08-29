import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.bridge_provisioning import mint_bridge_token
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.broker_credential import BrokerCredential
from app.models.provisioning_machine import ProvisioningMachine
from app.models.user import User
from app.schemas.broker_credentials import (
    BridgeTokenIssueOut,
    BrokerCredentialCreate,
    BrokerCredentialOut,
    BrokerCredentialUpdate,
)

router = APIRouter(prefix="/broker-credentials", tags=["broker-credentials"])


@router.get("", response_model=list[BrokerCredentialOut])
def list_broker_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(BrokerCredential).filter(BrokerCredential.user_id == current_user.user_id).all()
    return [BrokerCredentialOut.from_model(r) for r in rows]


@router.post("", response_model=BrokerCredentialOut, status_code=status.HTTP_201_CREATED)
def create_broker_credential(
    payload: BrokerCredentialCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # A new credential still defaults to is_active=True (unchanged --
    # preserves the single-account case exactly as before). Deactivating
    # any other active one first means connecting a new account
    # naturally becomes "the" active one instead of silently leaving two
    # active at once (see migration 0010's DB-level backstop for this).
    db.query(BrokerCredential).filter(
        BrokerCredential.user_id == current_user.user_id,
        BrokerCredential.is_active.is_(True),
    ).update({"is_active": False})

    # Self-service provisioning, Phase 2: only actually queue a job if
    # there's a machine that could ever claim it -- a "pending" row with
    # no active machine would sit unclaimable forever, showing the user
    # a permanent "provisioning..." with no path forward. That's worse
    # than the honest "not_requested" default, which is what a fresh
    # row still gets if this check fails.
    has_active_machine = (
        db.query(ProvisioningMachine).filter(ProvisioningMachine.is_active.is_(True)).first() is not None
    )

    cred = BrokerCredential(
        user_id=current_user.user_id,
        broker_name=payload.broker_name,
        server=payload.server,
        account_type=payload.account_type,
        provisioning_status="pending" if has_active_machine else "not_requested",
    )
    cred.account_login = payload.account_login
    cred.account_password = payload.account_password
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return BrokerCredentialOut.from_model(cred)


@router.patch("/{credential_id}", response_model=BrokerCredentialOut)
def update_broker_credential(
    credential_id: uuid.UUID,
    payload: BrokerCredentialUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided to update")

    if changes.get("is_active") is True:
        # Same radio-button behavior as create -- explicitly switching
        # which account is active deactivates whichever one was active
        # before, rather than ever leaving two active at once.
        db.query(BrokerCredential).filter(
            BrokerCredential.user_id == current_user.user_id,
            BrokerCredential.credential_id != credential_id,
            BrokerCredential.is_active.is_(True),
        ).update({"is_active": False})

    for field, value in changes.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return BrokerCredentialOut.from_model(row)


@router.post("/{credential_id}/retry-provisioning", response_model=BrokerCredentialOut)
def retry_provisioning(
    credential_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Self-service recovery for a job that ended up provisioning_status
    'failed' -- resets it to 'pending' so a machine's poller can claim
    it again. Only allowed from 'failed': retrying an already-pending/
    in-progress/active row would either be a no-op or would race the
    poller currently working on it.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")
    if row.provisioning_status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a failed provisioning job can be retried"
        )

    row.provisioning_status = "pending"
    row.provisioning_error = None
    row.provisioning_step = None
    row.provisioning_machine_id = None
    row.provisioning_claimed_at = None
    # provisioning_account_label deliberately NOT cleared -- it's stable
    # across retries by design (see its own docstring on the model), so
    # the poller's _cleanup_prior_attempt recognizes and safely replaces
    # the same folder/service name instead of orphaning it under a new one.
    db.commit()
    db.refresh(row)
    return BrokerCredentialOut.from_model(row)


@router.post("/{credential_id}/bridge-token", response_model=BridgeTokenIssueOut)
def issue_bridge_token(
    credential_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Mints a new token letting this credential's bridge worker fetch its
    decrypted login/password/server (see app/routers/internal_bridge.py)
    instead of reading them from a local plaintext config.json. Shown
    ONLY here, ONLY once -- never persisted in plaintext, never
    retrievable again (same convention as any API-key-issuance endpoint).

    Calling this again for the same credential ROTATES it: the previous
    token's hash is overwritten, so it stops working immediately. No
    separate revoke endpoint needed.

    Also called directly by bridge/scripts/provision_account.ps1 (not
    just the admin UI) -- check that script before changing this
    endpoint's request/response shape or auth requirement.

    Token generation itself lives in app.core.bridge_provisioning.mint_bridge_token
    -- shared with the internal, machine-facing claim endpoint
    (app/routers/internal_provisioning.py), which mints a token the same
    way as part of automated provisioning.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")

    token = mint_bridge_token(row)
    db.commit()

    return BridgeTokenIssueOut(bridge_token=token)
