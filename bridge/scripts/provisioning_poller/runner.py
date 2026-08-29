"""
Poll loop -- mirrors shadow_runner/runner.py's run_forever() shape
(two-tier exception handling, unconditional sleep at the bottom
regardless of outcome, never exits on its own), plus one addition
shadow_runner doesn't need: a provisioning failure must NEVER silently
skip reporting back to the admin API -- that's how a job gets stuck
'in_progress' forever with no trace. See poll_once()'s inner try/except
around the provisioning step, separate from run_forever()'s outer
catch-all.
"""
import logging
import time

from .admin_client import AdminApiError, ProvisioningAdminClient
from .config import PollerConfig
from .provisioner import provision_account

log = logging.getLogger("provisioning_poller.runner")


class PollerRunner:
    def __init__(self, config: PollerConfig, admin: ProvisioningAdminClient):
        self.config = config
        self.admin = admin

    def poll_once(self) -> None:
        try:
            job, reason = self.admin.claim_job()
        except AdminApiError as e:
            log.warning("Could not reach admin API to claim a job, will retry: %s", e)
            return
        if job is None:
            log.debug("Nothing claimed this poll (reason=%s)", reason)
            return

        log.info("Claimed job: credential_id=%s account_label=%s", job["credential_id"], job["account_label"])
        try:
            bridge_url = provision_account(job, self.config, self.admin)
        except Exception as e:
            log.exception("Provisioning failed for %s", job["account_label"])
            self._report_failure(job, str(e))
            return

        try:
            self.admin.complete_job(job["credential_id"], bridge_url)
            log.info("Provisioning complete for %s -> %s", job["account_label"], bridge_url)
        except AdminApiError as e:
            log.error(
                "Provisioning SUCCEEDED for %s (%s is really running) but reporting "
                "completion failed (%s) -- job stays 'in_progress' until this is fixed "
                "manually.", job["account_label"], bridge_url, e,
            )

    def _report_failure(self, job: dict, error_message: str) -> None:
        try:
            self.admin.fail_job(job["credential_id"], error_message)
        except AdminApiError as e:
            log.error(
                "Provisioning failed for %s (%s) AND could not report it to the admin "
                "API (%s) -- job stays 'in_progress'; needs manual intervention.",
                job["account_label"], error_message, e,
            )

    def run_forever(self) -> None:
        log.info("Provisioning poller starting: poll_interval=%ds", self.config.poll_interval_seconds)
        while True:
            try:
                self.poll_once()
            except Exception:
                log.exception("Unexpected error in poll_once -- continuing to next poll")
            time.sleep(self.config.poll_interval_seconds)
