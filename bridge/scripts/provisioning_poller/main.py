"""
Entrypoint: `python -m scripts.provisioning_poller.main`, run from
C:\\bridge (matches bridge/app/'s and bridge/scripts/'s existing
namespace-package convention -- no __init__.py anywhere under bridge/,
invoked with C:\\bridge on sys.path).

Mirrors shadow_runner/main.py's exact shape. Deployed as its own NSSM
service (MT5Provisioner) once proven against a disposable test account
-- see the Phase 1 plan's Verification section. Does NOT touch Tony's
real account or its MT5Bridge-Tony service in any way; it only ever
acts on broker_credentials rows with provisioning_status='pending',
which nothing sets automatically yet (that's Phase 2).
"""
import logging

from .admin_client import ProvisioningAdminClient
from .config import PollerConfig
from .runner import PollerRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    config = PollerConfig()
    admin = ProvisioningAdminClient(config.credential_api_url, config.machine_token)
    runner = PollerRunner(config, admin)
    runner.run_forever()


if __name__ == "__main__":
    main()
