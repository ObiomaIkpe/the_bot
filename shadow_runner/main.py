"""
Entrypoint: `python -m shadow_runner.main`

Deployed as its own container (docker-compose service `shadow_runner`,
same image as `api`, different command) -- see HANDOFF.md step 7.
"""
import logging

from app.core.database import SessionLocal
from .bridge_client import BridgeClient
from .config import ShadowRunnerConfig
from .runner import ShadowRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    config = ShadowRunnerConfig()
    bridge = BridgeClient(config.bridge_url)
    runner = ShadowRunner(config, bridge, SessionLocal)
    runner.recover_on_startup()
    runner.run_forever()


if __name__ == "__main__":
    main()