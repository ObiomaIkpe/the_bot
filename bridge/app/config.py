"""
Per-worker configuration.

Each account gets its own process, its own port, and its own config file.
config.json holds only local/machine-specific, non-secret fields.
login/password/server are NOT in this file (they used to be, in v1) --
they're fetched once, at startup, from the Linux api service's
GET /internal/bridge-credentials, authenticated by BRIDGE_TOKEN (env
var). This is the "Postgres-driven credential flow" this docstring used
to say was deferred -- it's implemented now, see fetch_credential()
below. Postgres's broker_credentials row is the only persisted copy of
the actual password; this process holds the fetched value in memory
only, for its own lifetime, and never writes it to disk.

Set BRIDGE_CONFIG_PATH env var to point at a different local config
file, so the same codebase can run as N single-purpose workers without
any code changes -- just a different config.json (+ its own BRIDGE_TOKEN)
per account (e.g. C:\\bridge\\accounts\\tony\\config.json, ...\\friend\\config.json).
"""
import json
import os
from functools import lru_cache
from pathlib import Path

import requests
from pydantic import BaseModel, Field, field_validator


class BridgeConfig(BaseModel):
    account_label: str = Field(..., description="Human-readable label, e.g. 'tony' or 'friend'. Used only in logs/health output.")
    # login/password/server: NOT read from config.json -- fetched once at
    # startup via fetch_credential() below and merged in by get_config().
    # Kept as real fields here (not a separate object) so mt5_client.py
    # and every route handler read config.login/.password/.server exactly
    # as before -- this change is invisible to every caller of get_config().
    login: int = Field(..., description="MT5 account number, e.g. 476123801 -- fetched from the api service, never stored locally")
    password: str = Field(..., description="MT5 account password -- fetched from the api service at startup, held in memory only, never written to disk")
    server: str = Field(..., description="MT5 server name, e.g. 'Exness-MT5Trial9' -- fetched from the api service")
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


def fetch_credential() -> dict:
    """
    Called once, at process startup (via get_config(), itself
    @lru_cache'd -- see mt5_client.py's module docstring: one
    mt5.initialize() per process, one credential for the process's whole
    life, no re-fetch on reconnect). Requires two env vars:

      BRIDGE_TOKEN       -- this account's per-credential token, minted
                             via POST /broker-credentials/{id}/bridge-token
                             on the api service. Identifies AND
                             authenticates in one value -- see
                             app/routers/internal_bridge.py.
      CREDENTIAL_API_URL  -- base URL of the api service, e.g.
                             https://api.ihusale.com.ng (HTTPS -- this
                             call sends the token and receives the
                             plaintext password over the wire; see this
                             project's TLS setup for why that's not
                             plain HTTP).

    No retry loop, no fallback to a local file: a worker that can't reach
    the api service simply doesn't start, exactly like a worker with a
    missing/bad config.json didn't start before this change. A silent
    retry or a stale-cache fallback would just reintroduce a smaller
    version of the "duplicate, possibly-stale secret" problem this
    replaces.
    """
    token = os.environ["BRIDGE_TOKEN"]
    api_url = os.environ["CREDENTIAL_API_URL"].rstrip("/")
    resp = requests.get(
        f"{api_url}/internal/bridge-credentials",
        headers={"X-Bridge-Token": token},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch broker credential from {api_url}/internal/bridge-credentials: "
            f"HTTP {resp.status_code} {resp.text}. Check BRIDGE_TOKEN and CREDENTIAL_API_URL "
            f"are set correctly and the api service is reachable from this VPS."
        )
    data = resp.json()
    return {"login": int(data["login"]), "password": data["password"], "server": data["server"]}


@lru_cache(maxsize=1)
def get_config() -> BridgeConfig:
    path = os.environ.get("BRIDGE_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Bridge config not found at {path}. Copy config.example.json to "
            f"this path (or set BRIDGE_CONFIG_PATH) and fill in the local fields."
        )
    local_fields = json.loads(p.read_text(encoding="utf-8"))
    return BridgeConfig(**local_fields, **fetch_credential())