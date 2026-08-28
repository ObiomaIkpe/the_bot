"""
Thin HTTP client for the admin API's internal provisioning-job endpoints
(app/routers/internal_provisioning.py). Mirrors
shadow_runner/bridge_client.py's exact shape (try/raise_for_status/
except requests.RequestException/wrap in a domain exception) -- same
idiom, different service.
"""
import logging

import requests

log = logging.getLogger("provisioning_poller.admin_client")


class AdminApiError(Exception):
    """Raised on any admin-API call failure (network error, non-2xx, or
    malformed response)."""


class ProvisioningAdminClient:
    def __init__(self, base_url: str, machine_token: str, timeout_seconds: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._headers = {"X-Machine-Token": machine_token}

    def claim_job(self) -> tuple[dict | None, str | None]:
        """Returns (job, reason). job is None (with reason "at_capacity"
        or "none_pending") when there's nothing to actually do this poll
        -- that's a normal outcome, not an error."""
        try:
            resp = requests.post(
                f"{self.base_url}/internal/provisioning-jobs/claim",
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise AdminApiError(f"POST /internal/provisioning-jobs/claim failed: {e}") from e
        return data.get("job"), data.get("reason")

    def complete_job(self, credential_id: str, bridge_url: str) -> None:
        try:
            resp = requests.post(
                f"{self.base_url}/internal/provisioning-jobs/{credential_id}/complete",
                json={"bridge_url": bridge_url},
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise AdminApiError(
                f"POST /internal/provisioning-jobs/{credential_id}/complete failed: {detail}"
            ) from e

    def fail_job(self, credential_id: str, error: str) -> None:
        try:
            resp = requests.post(
                f"{self.base_url}/internal/provisioning-jobs/{credential_id}/fail",
                json={"error": error},
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            detail = e.response.text if getattr(e, "response", None) is not None else str(e)
            raise AdminApiError(
                f"POST /internal/provisioning-jobs/{credential_id}/fail failed: {detail}"
            ) from e
