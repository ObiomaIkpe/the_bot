"""
Machine-facing, same trust/isolation reasoning as
app/routers/internal_provisioning.py's own module docstring (kept
separate from it, not merged in, despite sharing get_current_machine --
see this module's own docstring at the bottom of that file). The other
half of the provisioning_status state machine: tears an account's real
VPS resources down instead of setting them up. Reuses the exact same
claim/step/complete/fail shape as provisioning, but as parallel
endpoints rather than overloading /internal/provisioning-jobs/* --
the payloads genuinely differ (no MT5 credentials or bridge token are
ever needed to tear an account down), and this codebase already
prefers focused, single-purpose endpoints over multiplexed ones (see
retry_provisioning/issue_bridge_token existing as separate actions
rather than an overloaded PATCH).

Only reachable at all once app/routers/broker_credentials.py's
POST /{id}/remove sets provisioning_status="decommissioning" -- see
that endpoint's docstring for when it does vs. removes a never-claimed
row immediately without ever creating a job here.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import client_ip, write_audit_log
from app.core.database import get_db
from app.models.broker_credential import VALID_PROVISIONING_STEPS, BrokerCredential
from app.models.provisioning_machine import ProvisioningMachine
from app.routers.internal_provisioning import get_current_machine
from app.schemas.provisioning import (
    DecommissionClaimOut,
    DecommissionFailIn,
    DecommissionJobOut,
    ProvisioningStepIn,
)

router = APIRouter(prefix="/internal/decommission-jobs", tags=["internal"])


@router.post("/claim", response_model=DecommissionClaimOut)
def claim_decommission_job(
    request: Request,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    """
    Same with_for_update(skip_locked=True) atomic-claim shape as
    claim_provisioning_job -- see that function's docstring. No
    max_accounts capacity check here: a machine tearing an account down
    is *freeing* capacity, not consuming it, so the provisioning claim's
    "am I already at capacity" gate doesn't apply.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.provisioning_status == "decommissioning")
        .order_by(BrokerCredential.credential_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if row is None:
        return DecommissionClaimOut(job=None, reason="none_pending")

    # Flips to "removing" (not left at "decommissioning") for the exact
    # same reason provisioning's own claim flips pending -> in_progress:
    # without a distinct claimed-state, this query's WHERE clause would
    # keep matching the same row on every subsequent claim call, since
    # nothing else would ever move it out of the way.
    row.provisioning_status = "removing"
    row.provisioning_machine_id = machine.machine_id
    row.provisioning_claimed_at = datetime.now(timezone.utc)
    write_audit_log(
        db, "decommission_job_claimed", "machine",
        actor_id=machine.machine_id, actor_label=machine.label,
        resource_type="broker_credential", resource_id=row.credential_id,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(row)

    return DecommissionClaimOut(
        job=DecommissionJobOut(
            credential_id=row.credential_id,
            account_label=row.provisioning_account_label,
        )
    )


def _claimed_decommission_row_or_409(
    db: Session, machine: ProvisioningMachine, credential_id: uuid.UUID
) -> BrokerCredential:
    """Same shape and same reasoning as internal_provisioning.py's
    _claimed_row_or_409 -- deliberately not reused directly since it
    hardcodes checking for provisioning_status == "in_progress", not
    "removing" (this claim's own equivalent claimed-state)."""
    row = db.query(BrokerCredential).filter(BrokerCredential.credential_id == credential_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")
    if row.provisioning_machine_id != machine.machine_id or row.provisioning_status != "removing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This credential is not currently a decommission job claimed by this machine",
        )
    return row


@router.post("/{credential_id}/step", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def report_decommission_step(
    credential_id: uuid.UUID,
    payload: ProvisioningStepIn,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    """In practice only ever "tearing_down" -- see
    VALID_PROVISIONING_STEPS's docstring for why decommission doesn't
    get provisioning's fine-grained per-step reporting."""
    if payload.step not in VALID_PROVISIONING_STEPS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown step: {payload.step}")
    row = _claimed_decommission_row_or_409(db, machine, credential_id)
    row.provisioning_step = payload.step
    db.commit()


@router.post("/{credential_id}/complete", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def complete_decommission_job(
    credential_id: uuid.UUID,
    request: Request,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    """Resets everything provisioning-related back to a clean slate --
    bridge_fetch_token_hash included, since a removed account's old
    bridge token must never work again. NULL is fine under that
    column's unique index; Postgres doesn't treat NULLs as duplicates."""
    row = _claimed_decommission_row_or_409(db, machine, credential_id)
    row.provisioning_status = "removed"
    row.provisioning_step = None
    row.provisioning_machine_id = None
    row.provisioning_claimed_at = None
    row.provisioning_error = None
    row.bridge_url = None
    row.bridge_fetch_token_hash = None
    # actor identity comes from `machine` (the authenticated caller),
    # not row.provisioning_machine_id -- that field is being cleared on
    # this very row above, so it can't be the source here.
    write_audit_log(
        db, "decommission_job_completed", "machine",
        actor_id=machine.machine_id, actor_label=machine.label,
        resource_type="broker_credential", resource_id=row.credential_id,
        ip_address=client_ip(request),
    )
    db.commit()


@router.post("/{credential_id}/fail", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def fail_decommission_job(
    credential_id: uuid.UUID,
    payload: DecommissionFailIn,
    request: Request,
    machine: ProvisioningMachine = Depends(get_current_machine),
    db: Session = Depends(get_db),
):
    row = _claimed_decommission_row_or_409(db, machine, credential_id)
    row.provisioning_status = "decommission_failed"
    row.provisioning_error = payload.error
    write_audit_log(
        db, "decommission_job_failed", "machine",
        actor_id=machine.machine_id, actor_label=machine.label,
        resource_type="broker_credential", resource_id=row.credential_id,
        details={"error": payload.error},
        ip_address=client_ip(request),
    )
    db.commit()
