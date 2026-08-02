"""
Per-worker configuration.

Each account gets its own process, its own port, and its own config file.
v1 reads a local JSON file (C:\\bridge\\config.json by default). The Postgres-
driven credential flow is deferred until the Linux app integrates — see
HANDOFF.md in the main repo.

Set BRIDGE_CONFIG_PATH env var to point at a different file, so the same
codebase can run as N single-purpose workers without any code changes —
just a different config.json and a different --env-file / env var per
account (e.g. C:\\bridge\\accounts\\tony\\config.json, ...\\friend\\config.json).
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class BridgeConfig(BaseModel):
    account_label: str = Field(..., description="Human-readable label, e.g. 'tony' or 'friend'. Used only in logs/health output.")
    login: int = Field(..., description="MT5 account number, e.g. 476123801")
    password: str = Field(..., description="MT5 account password (plaintext in v1 local file — do not commit this file)")
    server: str = Field(..., description="MT5 server name, e.g. 'Exness-MT5Trial9'")
    mt5_terminal_path: str = Field(..., description=r"Path to terminal64.exe for this account's portable install, e.g. C:\MT5-Tony\terminal64.exe")
    default_symbol: str = Field(default="EURUSDm")
    port: int = Field(..., description="Port this worker binds to, e.g. 8001")

    # Phase 4 additions -- both default to the SAFE state, so an existing
    # config.json (copied verbatim to a new account/worker) never
    # accidentally enables order placement just by omission.
    orders_enabled: bool = Field(
        default=False,
        description=(
            "Config-level kill switch, independent of any code deploy. "
            "Must be explicitly set true in config.json for /orders or "
            "/positions/{ticket}/close to do anything other than return "
            "403. Flip to false and restart the bridge to stop order "
            "placement immediately, without touching code or the running "
            "live-trading container."
        ),
    )
    magic_number: int = Field(
        default=900001,
        description=(
            "MT5 'magic number' tag applied to every order this bridge "
            "places -- lets /positions (and this account's trade history "
            "in the MT5 terminal itself) distinguish orders placed by "
            "this system from anything placed manually or by another "
            "tool on the same account."
        ),
    )

    @field_validator("mt5_terminal_path")
    @classmethod
    def _path_exists(cls, v: str) -> str:
        # Soft check only — don't hard-fail config loading if the path is
        # temporarily wrong; mt5_client.initialize() will surface the real error.
        if not Path(v).exists():
            import logging
            logging.getLogger("bridge.config").warning(
                "mt5_terminal_path does not exist on disk: %s", v
            )
        return v


DEFAULT_CONFIG_PATH = r"C:\bridge\config.json"


@lru_cache(maxsize=1)
def get_config() -> BridgeConfig:
    path = os.environ.get("BRIDGE_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Bridge config not found at {path}. Copy config.example.json to "
            f"this path (or set BRIDGE_CONFIG_PATH) and fill in real credentials."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    return BridgeConfig(**data)