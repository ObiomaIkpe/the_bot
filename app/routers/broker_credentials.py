import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import client_ip, write_audit_log
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
    # "removed" rows are excluded from the default list, not hard-deleted
    # -- see remove_broker_credential's docstring. A removed account
    # should disappear from "Your accounts," while the row itself
    # persists for audit/history (this codebase has no hard-delete
    # anywhere else either).
    rows = (
        db.query(BrokerCredential)
        .filter(
            BrokerCredential.user_id == current_user.user_id,
            BrokerCredential.provisioning_status != "removed",
        )
        .all()
    )
    return [BrokerCredentialOut.from_model(r) for r in rows]


@router.post("", response_model=BrokerCredentialOut, status_code=status.HTTP_201_CREATED)
def create_broker_credential(
    payload: BrokerCredentialCreate,
    request: Request,
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
    # Flush (not commit) to populate cred.credential_id -- its
    # default=uuid.uuid4 is Python-side but only assigned to the ORM
    # instance on flush, and the audit row below needs it as resource_id.
    db.flush()
    write_audit_log(
        db, "broker_credential_created", "user",
        actor_id=current_user.user_id, actor_label=current_user.email,
        resource_type="broker_credential", resource_id=cred.credential_id,
        details={"broker_name": cred.broker_name, "account_type": cred.account_type, "server": cred.server},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(cred)
    return BrokerCredentialOut.from_model(cred)


@router.patch("/{credential_id}", response_model=BrokerCredentialOut)
def update_broker_credential(
    credential_id: uuid.UUID,
    payload: BrokerCredentialUpdate,
    request: Request,
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
    # `changes` is safe to log verbatim as-is: BrokerCredentialUpdate
    # today only exposes `is_active`. If that schema ever grows a field
    # carrying anything sensitive, this call site must be revisited to
    # scrub `details` before logging it.
    write_audit_log(
        db, "broker_credential_updated", "user",
        actor_id=current_user.user_id, actor_label=current_user.email,
        resource_type="broker_credential", resource_id=row.credential_id,
        details={"changes": changes},
        ip_address=client_ip(request),
    )
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


@router.post("/{credential_id}/remove", response_model=BrokerCredentialOut)
def remove_broker_credential(
    credential_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Self-service account removal -- the teardown half of the
    provisioning state machine (see app/models/broker_credential.py's
    VALID_PROVISIONING_STATUSES docstring). Deliberately an action
    endpoint, not a DELETE verb or a hard row delete: this codebase has
    no hard-delete precedent anywhere, and a removed row stays around
    (provisioning_status='removed', excluded from list_broker_credentials
    above) for audit/history.

    provisioning_account_label is set once, at first claim, and never
    cleared by a retry -- it's already this codebase's existing signal
    for "has this account ever actually touched a VPS":
      - Label unset (still 'not_requested' or 'pending', nobody has
        claimed it yet): nothing exists anywhere to tear down. Removal
        is immediate and synchronous.
      - Label set ('active', 'failed', or a previous 'decommission_failed'):
        a real MT5 terminal/service/firewall rule may exist on some
        machine. Needs a real machine to claim and run the teardown job
        (app/routers/internal_decommission.py) -- same conditional-
        trigger safety create_broker_credential already uses for the
        forward direction, so this never gets stuck in 'decommissioning'
        forever with nothing able to claim it.

    Not allowed while 'in_progress' or already 'decommissioning'/
    'removing' -- the row is already claimed by (or queued for) a
    machine actively working on it.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")
    if row.provisioning_status in ("in_progress", "decommissioning", "removing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is currently being worked on and can't be removed yet",
        )

    row.is_active = False

    if not row.provisioning_account_label:
        row.provisioning_status = "removed"
        event_type = "broker_credential_removed"
    else:
        has_active_machine = (
            db.query(ProvisioningMachine).filter(ProvisioningMachine.is_active.is_(True)).first() is not None
        )
        if not has_active_machine:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No provisioning machine is available to remove this account right now",
            )
        row.provisioning_status = "decommissioning"
        event_type = "broker_credential_decommission_requested"

    # Two distinct event types, not one -- mirrors the state machine's
    # own real distinction (immediate removal vs. a queued teardown a
    # machine must still run) rather than collapsing it.
    write_audit_log(
        db, event_type, "user",
        actor_id=current_user.user_id, actor_label=current_user.email,
        resource_type="broker_credential", resource_id=row.credential_id,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(row)
    return BrokerCredentialOut.from_model(row)


@router.post("/{credential_id}/bridge-token", response_model=BridgeTokenIssueOut)
def issue_bridge_token(
    credential_id: uuid.UUID,
    request: Request,
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

    # Captured BEFORE mint_bridge_token() overwrites the hash -- this is
    # the only way to tell "first issue" from "rotation" after the fact.
    was_rotation = row.bridge_fetch_token_hash is not None
    token = mint_bridge_token(row)
    write_audit_log(
        db, "bridge_token_issued", "user",
        actor_id=current_user.user_id, actor_label=current_user.email,
        resource_type="broker_credential", resource_id=row.credential_id,
        details={"rotated": was_rotation},
        ip_address=client_ip(request),
    )
    db.commit()

    return BridgeTokenIssueOut(bridge_token=token)
