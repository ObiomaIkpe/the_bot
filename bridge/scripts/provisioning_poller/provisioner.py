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

from .admin_client import AdminApiError, ProvisioningAdminClient
from .config import PollerConfig

log = logging.getLogger("provisioning_poller.provisioner")


class ProvisioningError(Exception):
    """str(e) goes verbatim into broker_credentials.provisioning_error --
    must be self-contained and readable by a human debugging it, not a
    bare exception repr."""


def _report_step(admin: ProvisioningAdminClient, credential_id: str, step: str) -> None:
    """Purely informational (Phase 2 live-progress UI) -- MUST be
    non-fatal. A step-report failure (admin API briefly unreachable,
    etc) is logged and swallowed here, never allowed to abort real
    provisioning work over a progress ping."""
    try:
        admin.report_step(credential_id, step)
    except AdminApiError as e:
        log.warning("Step-report '%s' failed (continuing provisioning regardless): %s", step, e)


def provision_account(job: dict, config: PollerConfig, admin: ProvisioningAdminClient) -> str:
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
    credential_id = job["credential_id"]
    mt5_dest = Path(rf"C:\MT5-{label}")
    accounts_dir = Path(config.bridge_root) / "accounts" / label
    config_path = accounts_dir / "config.json"
    service_name = f"bridge-{label}"

    _report_step(admin, credential_id, "cleaning_up")
    _cleanup_prior_attempt(mt5_dest, accounts_dir, service_name, label, config)
    try:
        _report_step(admin, credential_id, "copying_terminal")
        _copy_mt5_install(config.source_mt5_path, mt5_dest)
        terminal_path = mt5_dest / "terminal64.exe"

        _report_step(admin, credential_id, "launching_and_logging_in")
        _report_step(admin, credential_id, "verifying_login")
        _verify_login(terminal_path, job, config)

        _report_step(admin, credential_id, "configuring_worker")
        port = _next_free_port(config)
        _write_config_json(config_path, accounts_dir, label, terminal_path, port, job, config)

        _report_step(admin, credential_id, "installing_service")
        _install_and_start_service(service_name, config_path, job, port, config)

        _report_step(admin, credential_id, "opening_firewall")
        _open_firewall_port(label, port, config)

        _report_step(admin, credential_id, "waiting_for_health")
        _wait_for_health(port, service_name, config)
    except Exception:
        log.info("Provisioning failed for %s -- cleaning up before reporting failure", label)
        _cleanup_prior_attempt(mt5_dest, accounts_dir, service_name, label, config)
        raise
    return f"http://{config.public_host}:{port}"


def _run_nssm(config: PollerConfig, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([config.nssm_path, *args], capture_output=True, text=True)


def _rmtree_with_retry(path: Path, attempts: int = 5, delay_seconds: float = 1.0) -> None:
    """Found live, on the second real VPS test run: a just-exited MT5
    terminal process can hold Windows file handles open for a brief
    moment even after the process itself is already gone -- a bare
    shutil.rmtree(ignore_errors=True) run immediately afterward can
    silently leave the folder (or part of it) behind with ZERO
    indication anything went wrong, since ignore_errors swallows the
    failure entirely. Retries with a short delay instead of giving up
    (or silently ignoring) on the first attempt; logs a clear warning
    if it still can't be fully removed, rather than pretending it
    worked."""
    for attempt in range(1, attempts + 1):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        if attempt < attempts:
            time.sleep(delay_seconds)
    log.warning("Could not fully remove %s after %d attempts -- some files may still be locked", path, attempts)


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
        _rmtree_with_retry(mt5_dest)

    if accounts_dir.exists():
        _rmtree_with_retry(accounts_dir)

    # Idempotent -- succeeds (as a no-op) whether or not a rule from a
    # prior attempt actually exists. A failed job shouldn't leave an
    # open inbound port rule for a service that no longer exists.
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name=MT5 Bridge {label}"],
        capture_output=True, text=True,
    )


def _copy_mt5_install(source_path: str, mt5_dest: Path) -> None:
    """Copies the whole portable MT5 install to a fresh per-account
    folder, via robocopy -- deliberately NOT shutil.copytree. Both
    earlier shapes of this function were wrong in a way worth keeping
    written down:

    v1 (shutil.copytree(source, dest), no ignore): mirrored
    provision_account.ps1's `Copy-Item -Recurse` exactly. Failed on the
    first real VPS run with WinError 32 -- the source can be Tony's own,
    actively-running C:\\MT5-Tony, whose embedded-browser cache
    (temp\\EBWebView\\...\\Cookies) and per-symbol price-history cache
    (Bases\\<server>\\history\\<symbol>\\*.hcc) are held open by the live
    terminal. copytree aborts the ENTIRE copy on the first locked file.

    v2 (copytree with ignore= skipping every temp/, logs/, history/
    subtree): dodged WinError 32 but broke the very next step.
    _verify_login then failed, every single time, with
    (-10001, 'IPC send failed') -- a fast ~4s failure, nothing ever
    listening. A terminal launched /portable from a folder that has no
    temp/ directory does not bring up the local IPC channel the
    MetaTrader5 Python package attaches to (it doesn't recreate temp/ if
    it's absent at launch). The single provisioning run that ever
    succeeded predated v2 and did a full copy; every run after v2
    shipped died identically at _verify_login. The volatile DIRECTORIES
    have to exist at copy time; their stale CONTENTS do not -- MT5
    re-downloads history on first connect and repopulates temp/ itself
    once the directory is there.

    Turned out not to be the whole story either -- see the "single
    launch, still fails" note in _verify_login's own docstring, found
    AFTER this rewrite. Keeping this version regardless: a full,
    complete copy is still the correct contract for "a fresh portable
    install," independent of whatever else is causing -10001.

    v3 (this): robocopy /E copies every directory, and logs-and-skips
    the handful of individually locked cache files instead of aborting.
    Complete tree + WinError 32 tolerated. robocopy's exit code is a
    bitmask: < 8 is a clean copy, 8..15 means "some files were skipped"
    (here: the locked caches -- expected, logged, non-fatal), >= 16 is a
    real failure. The terminal64.exe check afterward is the actual
    guarantee the copy is usable, independent of the exit code."""
    mt5_dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "robocopy", str(source_path), str(mt5_dest),
            "/E",            # every subdirectory, including empty ones
            "/R:0", "/W:0",  # never retry a locked file (robocopy's default is 1e6 retries x 30s)
            "/NFL", "/NDL", "/NP",  # quiet: no per-file / per-dir list, no progress bar (keep the summary)
        ],
        capture_output=True, text=True,
    )
    if result.returncode >= 16:
        raise ProvisioningError(
            f"robocopy could not copy {source_path} -> {mt5_dest} "
            f"(exit {result.returncode}): {result.stdout.strip()} {result.stderr.strip()}"
        )
    if result.returncode >= 8:
        # Individual files failed to copy. Expected, and safe, when the
        # source is a running terminal: the locked *.hcc history cache
        # and EBWebView cookie DB, both rebuilt by the fresh terminal.
        # Logged in full so a genuinely missing config file can't hide
        # behind "oh, that's just the usual locked-cache noise".
        errors = [ln.strip() for ln in result.stdout.splitlines() if "ERROR" in ln.upper()]
        log.warning(
            "robocopy skipped %d locked file(s) copying %s (expected when the source terminal is running): %s",
            len(errors), source_path, " | ".join(errors) or result.stdout.strip()[-500:],
        )
    if not (mt5_dest / "terminal64.exe").exists():
        raise ProvisioningError(
            f"MT5 install copy did not produce terminal64.exe at {mt5_dest} "
            f"(robocopy exit {result.returncode}) -- the copy is incomplete, "
            f"not just missing cache files"
        )


def _mt5_terminal_log_tail(mt5_dest: Path, since: float, max_lines: int = 40) -> str:
    """Best-effort read of the newest lines the just-launched portable
    terminal wrote to its own journal (mt5_dest\\logs\\<date>.log). MT5
    writes these UTF-16, tab-separated.

    Added after a live incident where _verify_login kept failing with a
    bare (-10001, 'IPC send failed') from mt5.initialize() and NOTHING
    else -- that code alone can't tell "the account won't authorize"
    apart from "the terminal never opened its IPC pipe" apart from "two
    terminals are fighting over the same portable dir". The terminal's
    own journal ("authorization failed", "no connection to <server>",
    "account disabled", "terminal started"...) is the only artifact that
    says which.

    `since` (a time.time() captured right before launching this
    attempt) is load-bearing, not decoration: robocopy preserves the
    SOURCE file's original mtime, so a copied-over history.log from
    Tony's own terminal weeks ago still carries that old timestamp even
    though it was "just copied" moments ago. Without filtering by
    `since`, glob+sort-by-name or sort-by-mtime can both silently return
    one of Tony's old journals instead of anything from THIS run --
    exactly what happened the first time this was added (every failure
    reported the identical stale 2026-08-01 compiler log). Only a log
    file actually written or appended to AFTER `since` can possibly be
    about this attempt."""
    try:
        logs_dir = mt5_dest / "logs"
        candidates = [p for p in logs_dir.glob("*.log") if p.stat().st_mtime >= since]
        if not candidates:
            return "(no terminal journal was created or updated during this attempt -- only pre-existing, stale logs are present, or the terminal never started)"
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        raw = newest.read_bytes()
        for enc in ("utf-16", "utf-8", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            return "(could not decode terminal journal log)"
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[-max_lines:]) or "(terminal journal log is empty)"
    except OSError as e:
        return f"(could not read terminal journal log: {e})"


def _verify_login(terminal_path: Path, job: dict, config: PollerConfig) -> None:
    """Calls the UNMODIFIED bridge/scripts/verify_mt5_login.py as a
    subprocess -- exactly how provision_account.ps1 step 3 already
    does it. Never imported in-process; see this module's own docstring
    for why.

    Deliberately does NOT pre-launch terminal64.exe itself first (an
    earlier version did, mirroring provision_account.ps1's separate
    step 2 -- launch by hand, wait, then verify). Found live on real
    VPS testing: mt5.initialize(path=..., login=..., password=...,
    server=...) already launches, logs in, AND connects the terminal at
    `path` itself when it isn't already running -- see
    verify_mt5_login.py. Pre-launching a second terminal64.exe process
    against the SAME portable folder raced this subprocess's own launch
    attempt for that folder's IPC channel: whichever one lost got a
    FAST 'IPC send failed' (a lock conflict, not a slow response) --
    reproduced identically on a completely fresh, never-before-used
    demo account, ruling out account-side throttling as the cause.
    config.mt5_verify_timeout_ms was bumped up to compensate for this
    single call now covering the full cold launch, not just the
    verification of an already-warm one.

    UPDATE, found live right after deploying the above: removing the
    pre-launch did NOT fix -10001 either -- same account, same error,
    same step, same speed, on a clean redeploy. The double-launch race
    was real but was not the (or not the only) root cause. Neither
    Python's bare error code nor this poller's own logs say why MT5
    itself refuses the IPC handshake, so _mt5_terminal_log_tail's
    capture of the terminal's OWN journal (below) is now the only
    remaining source of a real answer -- read that message before
    forming another hypothesis blind."""
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
    since = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=config.mt5_verify_timeout_ms / 1000 + 15,
    )
    log.info("verify_mt5_login output: %s", (result.stdout or "").strip())
    if result.returncode != 0:
        journal = _mt5_terminal_log_tail(terminal_path.parent, since)
        raise ProvisioningError(
            f"MT5 login verification failed: {result.stdout}{result.stderr}".strip()
            + f"\n--- terminal journal ({terminal_path.parent}\\logs, newest, since this attempt) ---\n{journal}"
        )


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


def _open_firewall_port(label: str, port: int, config: PollerConfig) -> None:
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
