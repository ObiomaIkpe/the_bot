"""
Entrypoint: `python -m shadow_runner.main`

Deployed as its own container (docker-compose service `shadow_runner`,
same image as `api`, different command) -- see HANDOFF.md step 7.
"""
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from .bridge_client import BridgeClient
from .config import ShadowRunnerConfig
from .runner import ShadowRunner

# Logging/audit review, part 3: was its own ad hoc logging.basicConfig()
# call, always plain text regardless of LOG_FORMAT -- api was the only
# service that could emit structured JSON logs. shadow_runner runs in
# the same image/package as api (just a different `command:` in
# docker-compose.yml), so it can reuse app.core.logging directly rather
# than duplicating a formatter. (bridge/app/main.py and the provisioning
# poller can't do this -- they're deployed standalone on a separate
# Windows box with no access to the app/ package at all.)
configure_logging()


def main():
    config = ShadowRunnerConfig()
    bridge = BridgeClient(config.bridge_url)
    runner = ShadowRunner(config, bridge, SessionLocal)
    runner.recover_on_startup()
    runner.run_forever()


if __name__ == "__main__":
    main()