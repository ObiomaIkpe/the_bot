"""
Account removal -- the teardown half of provisioner.py's job. Needs no
new Windows-side logic: _cleanup_prior_attempt() already is a complete,
already-battle-tested "remove everything for this account label"
function (stop+remove its NSSM service, kill its scoped terminal64.exe
and remove its folder, remove its accounts/<label> config dir, remove
its firewall rule), built for provisioning's own retry path. This
module just drives that same function from a different trigger.

Deliberately a single reported step ("tearing_down"), not fine-grained
sub-steps the way provisioning reports 8 -- decommission normally
finishes in a few seconds, so per-substep live progress isn't worth
the complexity here (see VALID_PROVISIONING_STEPS's docstring in
app/models/broker_credential.py).

# sync-gap-fix-verification-marker: if this line is visible in
# C:\bridge\bridge after a plain `git pull`, the fix worked.
"""
import logging
from pathlib import Path

from .admin_client import AdminApiError, ProvisioningAdminClient
from .config import PollerConfig
from .provisioner import _cleanup_prior_attempt

log = logging.getLogger("provisioning_poller.decommissioner")


def _report_step(admin: ProvisioningAdminClient, credential_id: str, step: str) -> None:
    """Same non-fatal wrapper as provisioner.py's own -- a step-report
    failure must never abort real teardown work over a progress ping."""
    try:
        admin.report_decommission_step(credential_id, step)
    except AdminApiError as e:
        log.warning("Decommission step-report '%s' failed (continuing regardless): %s", step, e)


def decommission_account(job: dict, config: PollerConfig, admin: ProvisioningAdminClient) -> None:
    """Tears down every VPS-side resource for one account. Raises on a
    genuine, unexpected failure (e.g. nssm itself erroring) -- runner.py
    catches that and reports it back as decommission_failed. Does NOT
    raise just because _cleanup_prior_attempt logged a warning about a
    stuck/locked file; that function already tolerates its own partial
    failures (see its own docstring) and this job should still be
    considered complete in that case -- a rare leftover locked file is
    a much smaller problem than leaving the row stuck in
    'decommissioning' forever."""
    label = job["account_label"]
    credential_id = job["credential_id"]
    mt5_dest = Path(rf"C:\MT5-{label}")
    accounts_dir = Path(config.bridge_root) / "accounts" / label
    service_name = f"bridge-{label}"

    _report_step(admin, credential_id, "tearing_down")
    _cleanup_prior_attempt(mt5_dest, accounts_dir, service_name, label, config)
