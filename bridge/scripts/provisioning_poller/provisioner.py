"""
Per-job provisioning steps -- the automated equivalent of what a human
did by hand twice this session: bridge/scripts/provision_account.ps1
(the original credential-flow cutover) and tonight's manual NSSM setup
for MT5Bridge-Tony. See this module's functions for exactly which steps
come from which precedent, and where this deliberately deviates.

This process NEVER imports MetaTrader5 directly -- only
verify_mt5_login.py (run as a subprocess, unmodified) does. MT5's
Python package holds process-global connection state, so keeping that
entirely in a short-lived subprocess is what guarantees one job's MT5
session can never bleed into this poller's own long-running process, or
into a concurrent job (moot today since jobs are processed one at a
time -- see runner.py -- but still the right boundary regardless).
"""
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import requests

from .config import PollerConfig

log = logging.getLogger("provisioning_poller.provisioner")


class ProvisioningError(Exception):
    """str(e) goes verbatim into broker_credentials.provisioning_error --
    must be self-contained and readable by a human debugging it, not a
    bare exception repr."""


def provision_account(job: dict, config: PollerConfig) -> str:
    """Runs every step for one job, in order. Returns the bridge_url to
    report back on success; raises ProvisioningError (or lets a genuine
    bug's exception propagate) on any failure -- runner.py is
    responsible for catching and reporting either case back to the
    admin API.

    Cleans up after itself on BOTH ends of a failed attempt: once at the
    start (handles a job that was already tried and failed before --
    see _cleanup_prior_attempt) and once more if THIS attempt itself
    fails partway through, so a bad password/server doesn't leave a
    half-provisioned MT5 folder, orphaned NSSM service, or open
    firewall rule sitting on disk indefinitely just because nobody's
    retried the job yet. Either way, the original exception is
    re-raised unchanged -- cleanup must never mask or replace the real
    failure reason reported back to the admin API."""
    label = job["account_label"]
    mt5_dest = Path(rf"C:\MT5-{label}")
    accounts_dir = Path(config.bridge_root) / "accounts" / label
    config_path = accounts_dir / "config.json"
    service_name = f"bridge-{label}"

    _cleanup_prior_attempt(mt5_dest, accounts_dir, service_name, label, config)
    try:
        _copy_mt5_install(config.source_mt5_path, mt5_dest)
        terminal_path = mt5_dest / "terminal64.exe"
        _launch_and_login(terminal_path, job, config)
        _verify_login(terminal_path, job, config)
        port = _next_free_port(config)
        _write_config_json(config_path, accounts_dir, label, terminal_path, port, job, config)
        _install_and_start_service(service_name, config_path, job, port, config)
        _open_firewall_port(service_name, label, port, config)
        _wait_for_health(port, service_name, config)
    except Exception:
        log.info("Provisioning failed for %s -- cleaning up before reporting failure", label)
        _cleanup_prior_attempt(mt5_dest, accounts_dir, service_name, label, config)
        raise
    return f"http://{config.public_host}:{port}"


def _run_nssm(config: PollerConfig, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([config.nssm_path, *args], capture_output=True, text=True)


def _cleanup_prior_attempt(
    mt5_dest: Path, accounts_dir: Path, service_name: str, label: str, config: PollerConfig
) -> None:
    """Removes everything a previous attempt at this same job (whether
    it failed just now, or failed on an earlier try and is only being
    retried later) may have left behind: the NSSM service, the copied
    MT5 folder (and, first, the specific terminal64.exe process running
    from it), the account's config directory, and its firewall rule.
    Called both at the START of every attempt (a no-op on a genuine
    first attempt, since none of this exists yet) and again if THIS
    attempt fails partway through -- see provision_account()'s
    docstring.

    Deliberate deviation from provision_account.ps1's step 0, which
    REFUSES if C:\\MT5-<label> already exists -- correct for a human
    doing first-time setup, wrong here since account_label is stable
    across retries (set once at first claim, see
    BrokerCredential.provisioning_account_label's own docstring)."""
    status = _run_nssm(config, "status", service_name)
    if status.returncode == 0:
        log.info("Prior attempt left service %s behind -- removing it", service_name)
        _run_nssm(config, "stop", service_name)  # ignore failure -- may already be stopped/paused
        _run_nssm(config, "remove", service_name, "confirm")

    if mt5_dest.exists():
        log.info("Prior attempt left %s behind -- cleaning up", mt5_dest)
        # Kill only the terminal64.exe running from THIS EXACT path.
        # Never a bare `taskkill /IM terminal64.exe` -- every account's
        # MT5 copy shares that same image name, so an unscoped kill
        # would also kill Tony's real, unrelated C:\MT5-Tony\terminal64.exe.
        exe_path = str(mt5_dest / "terminal64.exe")
        ps_kill = (
            "Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" | "
            f"Where-Object {{ $_.ExecutablePath -eq '{exe_path}' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        subprocess.run(["powershell", "-Command", ps_kill], capture_output=True, text=True)
        shutil.rmtree(mt5_dest, ignore_errors=True)

    if accounts_dir.exists():
        shutil.rmtree(accounts_dir, ignore_errors=True)

    # Idempotent -- succeeds (as a no-op) whether or not a rule from a
    # prior attempt actually exists. A failed job shouldn't leave an
    # open inbound port rule for a service that no longer exists.
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name=MT5 Bridge {label}"],
        capture_output=True, text=True,
    )


def _ignore_volatile_mt5_dirs(_dir_path: str, names: list[str]) -> list[str]:
    """Skips temp/, logs/, and cached history/ subdirectories wherever
    they appear in the tree. Found live, on the very first real VPS
    test run: copying from an ACTIVELY RUNNING source terminal
    (Tony's real C:\\MT5-Tony) hits WinError 32 (file in use) on
    exactly these -- MT5's embedded browser cache
    (temp\\EBWebView\\...\\Cookies) and per-symbol history cache files
    (Bases\\<server>\\history\\<symbol>\\*.hcc) are both actively held
    open by a live terminal. None of them matter for a fresh install
    anyway: MT5 re-downloads price history itself on first connect,
    and temp/logs are per-instance runtime scratch data, not
    configuration -- skipping them is strictly better than copying
    them even when the source terminal ISN'T running."""
    return [n for n in names if n.lower() in ("temp", "logs", "history")]


def _copy_mt5_install(source_path: str, mt5_dest: Path) -> None:
    """Mirrors provision_account.ps1 step 1, minus the volatile
    directories _ignore_volatile_mt5_dirs skips -- see that function's
    docstring for why."""
    shutil.copytree(source_path, mt5_dest, ignore=_ignore_volatile_mt5_dirs)
    if not (mt5_dest / "terminal64.exe").exists():
        raise ProvisioningError(f"Copy completed but terminal64.exe is missing at {mt5_dest}")


def _launch_and_login(terminal_path: Path, job: dict, config: PollerConfig) -> None:
    """Mirrors provision_account.ps1 step 2 exactly -- same /portable
    launch flags, same 15s default wait for MT5 to actually connect
    before verification is attempted."""
    subprocess.Popen([
        str(terminal_path),
        "/portable",
        f"/login:{job['account_login']}",
        f"/password:{job['account_password']}",
        f"/server:{job['server']}",
    ])
    time.sleep(config.mt5_launch_wait_seconds)


def _verify_login(terminal_path: Path, job: dict, config: PollerConfig) -> None:
    """Calls the UNMODIFIED bridge/scripts/verify_mt5_login.py as a
    subprocess -- exactly how provision_account.ps1 step 3 already
    does it. Never imported in-process; see this module's own docstring
    for why."""
    verify_script = Path(__file__).resolve().parent.parent / "verify_mt5_login.py"
    python_exe = config.venv_python if Path(config.venv_python).exists() else "python"

    cmd = [
        python_exe, str(verify_script),
        "--path", str(terminal_path),
        "--login", str(job["account_login"]),
        "--password", job["account_password"],
        "--server", job["server"],
        "--timeout-ms", str(config.mt5_verify_timeout_ms),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=config.mt5_verify_timeout_ms / 1000 + 15,
    )
    log.info("verify_mt5_login output: %s", (result.stdout or "").strip())
    if result.returncode != 0:
        raise ProvisioningError(f"MT5 login verification failed: {result.stdout}{result.stderr}")


def _next_free_port(config: PollerConfig) -> int:
    """Scans this machine's own config.json files for used ports -- no
    new admin-API state needed, since a machine already knows what's
    running on itself. Unreadable/malformed files are skipped with a
    logged warning rather than crashing the whole job over one bad file."""
    used_ports: set[int] = set()

    bridge_root = Path(config.bridge_root)
    candidate_paths = [bridge_root / "config.json"]
    accounts_dir = bridge_root / "accounts"
    if accounts_dir.exists():
        candidate_paths.extend(accounts_dir.glob("*/config.json"))

    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            used_ports.add(int(data["port"]))
        except (OSError, ValueError, KeyError) as e:
            log.warning("Could not read port from %s, skipping: %s", path, e)

    port = config.provisioning_base_port
    while port in used_ports:
        port += 1
    return port


def _write_config_json(
    config_path: Path, accounts_dir: Path, label: str, terminal_path: Path,
    port: int, job: dict, config: PollerConfig,
) -> None:
    """Exact shape from provision_account.ps1 step 6 -- no login/
    password/server (bridge/app/config.py's fetch_credential() gets
    those from the admin API at the worker's own startup, never from
    this file). Uses magic_numbers/bridge_token already returned by the
    claim response -- no separate GET /model-configs or
    POST .../bridge-token calls needed here, unlike the .ps1 script,
    which had to fetch them itself since a human hadn't already."""
    magic_numbers = sorted(job["magic_numbers"])
    cfg = {
        "account_label": label,
        "mt5_terminal_path": str(terminal_path),
        "default_symbol": config.default_symbol,
        "port": port,
        "orders_enabled": False,
        "magic_number": magic_numbers[0],
        "magic_numbers": magic_numbers,
    }
    accounts_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _install_and_start_service(
    service_name: str, config_path: Path, job: dict, port: int, config: PollerConfig,
) -> None:
    """The exact NSSM sequence confirmed working tonight against the
    real MT5Bridge-Tony service. --workers 1 is non-negotiable (see
    bridge/app/main.py's own docstring -- mt5.initialize() holds one
    connection per OS process)."""
    uvicorn_args = [
        "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port), "--workers", "1",
    ]
    install = _run_nssm(config, "install", service_name, config.venv_python, *uvicorn_args)
    if install.returncode != 0:
        raise ProvisioningError(f"nssm install failed: {install.stdout}{install.stderr}")

    _run_nssm(config, "set", service_name, "AppDirectory", config.bridge_root)

    # Never log this call's arguments verbatim -- it carries the
    # plaintext bridge token. Its only unavoidable transient exposure is
    # the OS process listing during this one subprocess.run call, same
    # tradeoff already accepted for provision_account.ps1's password arg.
    env_result = _run_nssm(
        config, "set", service_name, "AppEnvironmentExtra",
        f"BRIDGE_CONFIG_PATH={config_path}",
        f"BRIDGE_TOKEN={job['bridge_token']}",
        f"CREDENTIAL_API_URL={config.credential_api_url}",
    )
    log.info("AppEnvironmentExtra set for %s (token redacted, exit=%d)", service_name, env_result.returncode)
    if env_result.returncode != 0:
        raise ProvisioningError(f"nssm set AppEnvironmentExtra failed (exit {env_result.returncode})")

    logs_dir = Path(config.bridge_root) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    _run_nssm(config, "set", service_name, "AppStdout", str(logs_dir / f"{job['account_label']}-stdout.log"))
    _run_nssm(config, "set", service_name, "AppStderr", str(logs_dir / f"{job['account_label']}-stderr.log"))
    _run_nssm(config, "set", service_name, "Start", "SERVICE_AUTO_START")

    start = _run_nssm(config, "start", service_name)
    if start.returncode != 0:
        raise ProvisioningError(f"nssm start failed: {start.stdout}{start.stderr}")


def _open_firewall_port(service_name: str, label: str, port: int, config: PollerConfig) -> None:
    """New step, not in provision_account.ps1 -- added because of a
    real incident PHASE2_VALIDATION.md already documents once by hand:
    port 8001 needed an explicit Windows Firewall inbound rule scoped
    to the Hetzner box's IP before it was reachable at all. Idempotent
    delete-then-add so a retry never ends up with two overlapping
    rules for the same account."""
    rule_name = f"MT5 Bridge {label}"
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
        capture_output=True, text=True,
    )
    add = subprocess.run(
        [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}", "dir=in", "action=allow", "protocol=TCP",
            f"localport={port}", f"remoteip={config.firewall_remote_ip}",
        ],
        capture_output=True, text=True,
    )
    if add.returncode != 0:
        raise ProvisioningError(f"Failed to open firewall port {port}: {add.stdout}{add.stderr}")


def _wait_for_health(port: int, service_name: str, config: PollerConfig) -> None:
    """Deliberately checks localhost, not public_host -- this runs on
    the VPS itself, so it doesn't depend on the firewall rule just
    added, avoiding a chicken-and-egg where a firewall misconfiguration
    would block even this local sanity check."""
    last_error = "no attempts made"
    for attempt in range(1, config.health_check_max_attempts + 1):
        try:
            resp = requests.get(f"http://localhost:{port}/health", timeout=5)
            if resp.status_code == 200 and resp.json().get("connected") is True:
                return
            last_error = f"HTTP {resp.status_code}: {resp.text}"
        except requests.RequestException as e:
            last_error = str(e)
        log.info("Health check attempt %d/%d for %s: %s", attempt, config.health_check_max_attempts, service_name, last_error)
        time.sleep(config.health_check_interval_seconds)

    # Distinguishes "still starting" from "crash-looped and NSSM
    # auto-paused it" -- the exact failure mode hit tonight with
    # MT5Bridge-Tony's missing BRIDGE_TOKEN.
    status = _run_nssm(config, "status", service_name)
    raise ProvisioningError(
        f"Worker never became healthy after {config.health_check_max_attempts} attempts "
        f"(last: {last_error}). Service status: {status.stdout.strip() or status.stderr.strip()}"
    )
