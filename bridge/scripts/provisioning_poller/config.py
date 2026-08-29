"""
Provisioning poller configuration. Plain __init__-based class, matching
shadow_runner/config.py's idiom -- not bridge/app/config.py's pydantic,
since this program never touches MT5 credentials directly the way a
bridge worker's own config does (this reads job payloads over HTTP, at
runtime, per job -- there's no local secret-bearing config.json for the
poller itself to validate against a schema).

Required vars use bare os.environ[...] (hard KeyError if unset, no
default, no silent guessing) -- same convention shadow_runner/config.py
documents: "must be set explicitly, never silently guessed."
"""
import os


class PollerConfig:
    def __init__(self):
        self.machine_token = os.environ["MACHINE_TOKEN"]
        # Minted via python -m app.scripts.register_provisioning_machine
        # (operator-only, no HTTP endpoint -- see that script and
        # app/routers/internal_provisioning.py's module docstring for why).

        self.credential_api_url = os.environ["CREDENTIAL_API_URL"].rstrip("/")

        self.public_host = os.environ["PROVISIONING_PUBLIC_HOST"]
        # This machine's externally-reachable IP, used to build bridge_url
        # at completion (http://<public_host>:<port>). Deliberately NOT
        # auto-detected -- an auto-detected address (UPnP/STUN/external-IP
        # lookup) could silently pick up a NAT/loopback/VPN-adapter
        # address or transiently fail, corrupting bridge_url for a real
        # account with no obvious symptom until the admin API can't reach
        # it. One-time, reviewed operator decision instead.

        self.firewall_remote_ip = os.environ["FIREWALL_REMOTE_IP"]
        # The Hetzner box's IP -- scopes the per-account inbound firewall
        # rule this poller adds (see provisioner.py's _open_firewall_port).
        # No default: PHASE2_VALIDATION.md already documents once, by
        # hand, that port 8001 needed exactly this kind of rule before it
        # was reachable at all -- must never be silently skipped for a
        # new account's port.

        self.bridge_root = os.environ.get("BRIDGE_ROOT", r"C:\bridge")
        self.source_mt5_path = os.environ.get("SOURCE_MT5_PATH", r"C:\MT5-Tony")
        self.nssm_path = os.environ.get("NSSM_PATH", r"C:\nssm\nssm.exe")
        self.default_symbol = os.environ.get("DEFAULT_SYMBOL", "EURUSDm")
        self.poll_interval_seconds = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))

        # 8001 is Tony's (config.example.json) -- scan starts above it.
        self.provisioning_base_port = int(os.environ.get("PROVISIONING_BASE_PORT", "8002"))

        # Covers the ENTIRE cold launch+login+connect done inside
        # mt5.initialize() itself (see provisioner.py's _verify_login
        # docstring for why there's no separate pre-launch anymore) --
        # bumped from 30000 since this one call now does what used to
        # be a 15s pre-launch wait plus a 30s verify timeout.
        self.mt5_verify_timeout_ms = int(os.environ.get("MT5_VERIFY_TIMEOUT_MS", "45000"))
        self.health_check_max_attempts = int(os.environ.get("HEALTH_CHECK_MAX_ATTEMPTS", "10"))
        self.health_check_interval_seconds = int(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", "3"))

        self.venv_python = os.path.join(self.bridge_root, "venv", "Scripts", "python.exe")
