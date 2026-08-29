import uuid

from pydantic import BaseModel


class ProvisioningJobOut(BaseModel):
    """Returned to a machine's poller by POST /internal/provisioning-jobs/claim.
    Includes the plaintext account_password and bridge_token -- the one
    place either crosses the wire automatically instead of a human typing
    them into bridge/scripts/provision_account.ps1's parameters by hand.
    Requires HTTPS (already enforced by this deployment's TLS setup) and
    the receiving poller must never write account_password to disk."""
    credential_id: uuid.UUID
    account_label: str
    account_login: str
    account_password: str
    server: str
    magic_numbers: list[int]
    bridge_token: str


class ProvisioningClaimOut(BaseModel):
    """job is None when there's nothing pending, or the claiming machine
    is already at its own max_accounts -- reason distinguishes the two
    so a poller can log/back off appropriately instead of guessing."""
    job: ProvisioningJobOut | None
    reason: str | None = None


class ProvisioningCompleteIn(BaseModel):
    bridge_url: str


class ProvisioningFailIn(BaseModel):
    error: str


class ProvisioningStepIn(BaseModel):
    step: str


class DecommissionJobOut(BaseModel):
    """Returned to a machine's poller by POST
    /internal/decommission-jobs/claim. Deliberately much smaller than
    ProvisioningJobOut -- tearing down an account needs only its label
    (to derive the same C:\\MT5-<label> / accounts/<label> / firewall
    rule paths _cleanup_prior_attempt already knows how to build), never
    the MT5 login/password or a bridge token."""
    credential_id: uuid.UUID
    account_label: str


class DecommissionClaimOut(BaseModel):
    """Same job/reason shape as ProvisioningClaimOut -- kept as a
    separate class rather than a shared generic since the two `job`
    payloads differ (see DecommissionJobOut)."""
    job: DecommissionJobOut | None
    reason: str | None = None


class DecommissionFailIn(BaseModel):
    error: str
