"""
Machine-facing, NOT a user endpoint and NOT the same trust level as
app/routers/internal_bridge.py's per-credential bridge token. A "machine
token" here can claim jobs for ANY pending broker_credentials row
targeting it -- meaning a poller holding one sees multiple users'
plaintext MT5 passwords in flight, not just one account's. Kept in its
own file, with its own auth dependency, so that boundary stays visually
obvious (same reasoning internal_bridge.py's own docstring gives for
being separate from the ordinary user-facing broker_credentials router).

Minting a machine token has NO HTTP endpoint anywhere, on purpose -- see
app/models/provisioning_machine.py and
app/scripts/register_provisioning_machine.py. User has no role/admin
concept today, so there's no correct way to gate that safely yet.

Phase 0 only: these endpoints exist and are fully functional, but
nothing in the normal user-facing flow (POST /broker-credentials) sets a
row to "pending" yet, so nothing calls this in production until a later
phase deliberately flips that. Exercised today only by hand (see the
Phase 0 plan's Verification section) or, once it exists, by the VPS-side
poller (Phase 1, not built yet).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.bridge_provisioning import mint_bridge_token
from app.core.database import get_db
from app.core.security import hash_service_token
from app.models.broker_credential import VALID_PROVISIONING_STEPS, BrokerCredential
from app.models.model_config import ModelConfig
from app.models.provisioning_machine import ProvisioningMachine
from app.schemas.provisioning import (
    ProvisioningClaimOut,
    ProvisioningCompleteIn,
    ProvisioningFailIn,
    ProvisioningJobOut,
    ProvisioningStepIn,
)

router = APIRouter(prefix="/internal/provisioning-jobs", tags=["internal"])


def get_current_machine(
    x_machine_token: str = Header(..., alias="X-Machine-Token"),
    db: Session = Depends(get_db),
) -> ProvisioningMachine:
    """Same 401-covers-both-cases shape as internal_bridge.py's bridge-token
    check, for the same reason: "no such token" and "deactivated machine"
    get an identical response so this endpoint never leaks which case
    occurred to whoever's holding a bad/revoked token."""
    row = (
        db.query(ProvisioningMachine)
        .filter(ProvisioningMachine.machine_token_hash == hash_service_token(x_machine_token))
        .first()
    )
    if row is None or not row.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive machine token")
    return row


@router.post("/claim", response_model=ProvisioningClaimOut)
def claim_provisioning_job(
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    """
    Capacity is checked against THIS machine's own in-flight+active count
    before claiming anything -- a machine at capacity gets reason
    "at_capacity" rather than an empty queue's reason "none", so a
    poller can distinguish "nothing to do" from "there's work, but not
    for me right now" in its own logs.

    Claiming is a single atomic UPDATE ... WHERE ... RETURNING-shaped
    operation (via with_for_update(skip_locked=True) + explicit status
    check before the write) so two concurrent claims -- from this machine
    polling twice in a race, or a future second machine -- can never both
    claim the same row.
    """
    in_flight_count = (
        db.query(BrokerCredential)
        .filter(
            BrokerCredential.provisioning_machine_id == machine.machine_id,
            BrokerCredential.provisioning_status.in_(("in_progress", "active")),
        )
        .count()
    )
    if in_flight_count >= machine.max_accounts:
        return ProvisioningClaimOut(job=None, reason="at_capacity")

    # order_by(credential_id) is a deterministic tiebreak, NOT real FIFO --
    # there's no "requested at" timestamp on this row (provisioning_status
    # flips straight from not_requested to pending with nothing recording
    # when), so which of several simultaneously-pending jobs gets served
    # first is arbitrary. Same class of gap, same "arbitrary but
    # deterministic, not fixed here" call as migration 0010's own
    # data-cleanup tiebreak -- acceptable while nothing sets 'pending'
    # automatically yet (Phase 0); worth a real requested-at column before
    # Phase 2 makes this a genuine multi-user queue.
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.provisioning_status == "pending")
        .order_by(BrokerCredential.credential_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        return ProvisioningClaimOut(job=None, reason="none_pending")

    row.provisioning_status = "in_progress"
    row.provisioning_machine_id = machine.machine_id
    row.provisioning_claimed_at = datetime.now(timezone.utc)
    if not row.provisioning_account_label:
        row.provisioning_account_label = str(row.credential_id)[:8]

    bridge_token = mint_bridge_token(row)

    magic_numbers = [
        m.magic_number
        for m in db.query(ModelConfig)
        .filter(ModelConfig.user_id == row.user_id)
        .order_by(ModelConfig.magic_number)
        .all()
    ]

    db.commit()
    db.refresh(row)

    return ProvisioningClaimOut(
        job=ProvisioningJobOut(
            credential_id=row.credential_id,
            account_label=row.provisioning_account_label,
            account_login=row.account_login,
            account_password=row.account_password,
            server=row.server,
            magic_numbers=magic_numbers,
            bridge_token=bridge_token,
        )
    )


def _claimed_row_or_409(db: Session, machine: ProvisioningMachine, credential_id: uuid.UUID) -> BrokerCredential:
    row = db.query(BrokerCredential).filter(BrokerCredential.credential_id == credential_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")
    if row.provisioning_machine_id != machine.machine_id or row.provisioning_status != "in_progress":
        # Covers: wrong machine reporting on someone else's job, a stale
        # poller reporting after a retry already reset the row, or
        # reporting twice for the same job -- all genuinely different
        # bugs, but all equally "this report doesn't apply," so 409
        # rather than pretending it succeeded.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This credential is not currently an in-progress job claimed by this machine",
        )
    return row


@router.post("/{credential_id}/step", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def report_provisioning_step(
    credential_id: uuid.UUID,
    payload: ProvisioningStepIn,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    """
    Called by the poller right before starting each real step in
    provision_account() (bridge/scripts/provisioning_poller/provisioner.py)
    -- purely informational, lets the frontend show live progress
    (Phase 2). Reporting a step failing must never abort real
    provisioning work; that's enforced on the poller side
    (admin_client.py's report_step() + provisioner.py's _report_step()
    swallow/log any failure here), not this endpoint's problem.
    """
    if payload.step not in VALID_PROVISIONING_STEPS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown step: {payload.step}")
    row = _claimed_row_or_409(db, machine, credential_id)
    row.provisioning_step = payload.step
    db.commit()


@router.post("/{credential_id}/complete", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def complete_provisioning_job(
    credential_id: uuid.UUID,
    payload: ProvisioningCompleteIn,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    row = _claimed_row_or_409(db, machine, credential_id)
    row.provisioning_status = "active"
    row.bridge_url = payload.bridge_url
    row.provisioning_error = None
    db.commit()


@router.post("/{credential_id}/fail", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def fail_provisioning_job(
    credential_id: uuid.UUID,
    payload: ProvisioningFailIn,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    row = _claimed_row_or_409(db, machine, credential_id)
    row.provisioning_status = "failed"
    row.provisioning_error = payload.error
    db.commit()
