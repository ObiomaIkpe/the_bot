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
import logging.handlers
import os

from .admin_client import ProvisioningAdminClient
from .config import PollerConfig
from .runner import PollerRunner

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# 10MB x 5 backups = 50MB cap -- same policy as docker-compose.yml's
# services (see that file's `x-logging` anchor). Logging/audit review,
# part 2: previously this process only ever logged to stderr via
# logging.basicConfig(), with no filename -- whatever captured that
# (NSSM's AppStderr redirect, a Scheduled Task wrapper, or nothing at
# all if run non-interactively) never rotated it. Writing our own
# bounded, rotating file here means this process manages its own log
# retention directly, independent of whatever external mechanism (if
# any) also happens to be capturing its stdout/stderr.
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


def _configure_logging(bridge_root: str) -> None:
    """Deliberately takes bridge_root as a plain argument, not reading
    PollerConfig() itself -- keeps this testable in isolation (see
    tests/bridge/test_provisioning_poller_main.py) without needing the
    machine token / API URL env vars PollerConfig otherwise requires."""
    logs_dir = os.path.join(bridge_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(logs_dir, "poller.log"), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
    )
    file_handler.setFormatter(formatter)

    # force=True: guarantees these handlers actually get installed even
    # if the root logger was already configured by something else
    # (basicConfig() is normally a silent no-op in that case) --
    # matters both for real-world determinism and for this function
    # being callable more than once in the same process (e.g. across
    # tests, see tests/bridge/test_provisioning_poller_main.py).
    logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler], force=True)


def main():
    config = PollerConfig()
    # Logging is configured here, after PollerConfig() exists, not at
    # module import time -- it needs config.bridge_root to know where
    # to put the rotating log file.
    _configure_logging(config.bridge_root)
    admin = ProvisioningAdminClient(config.credential_api_url, config.machine_token)
    runner = PollerRunner(config, admin)
    runner.run_forever()


if __name__ == "__main__":
    main()
