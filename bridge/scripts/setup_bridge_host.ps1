<#
.SYNOPSIS
  One-shot, idempotent setup for THIS Windows VPS acting as the bridge
  host: repo checkout, Python venv, and the self-service provisioning
  poller registered as a real Scheduled Task. This script does NOT
  provision any MT5 account itself -- once it's done, use the app's own
  "Broker Connection" signup form for that, same as every other user.
  The poller (bridge/scripts/provisioning_poller/provisioner.py) does
  the rest automatically: MT5 terminal copy, login, config.json,
  per-account NSSM service, firewall rule.

.DESCRIPTION
  Written for the 2026-10 full VPS rebuild, after a deliberate full
  teardown (services, scheduled task, auto-logon, MT5 installs, bridge
  checkout -- everything). Replaces re-typing ad-hoc commands from
  OPS_COMMANDS.md/HANDOFF.md by hand each time; running this script IS
  the documented procedure from now on. Safe to re-run -- every step is
  idempotent (pulls instead of re-cloning, skips an existing venv,
  replaces rather than duplicates the Scheduled Task).

  Steps, in order:
    1. Preflight: elevated session, git/python/nssm.exe all present.
    2. Clone the_bot to -RepoRoot (default C:\bridge) if missing, else
       `git pull` it. ONE canonical checkout -- the two-separate-
       checkouts drift documented in HANDOFF.md's "Bridge sync-gap fix"
       is exactly what this avoids. Everything bridge-specific below
       uses -RepoRoot\bridge as BridgeRoot, matching OPS_COMMANDS.md's
       own `$env:BRIDGE_ROOT = "C:\bridge\bridge"` convention (NOT the
       flatter layout bridge/README.md's "Run" section shows -- that
       assumes bridge/ itself is the clone root; see that file if this
       ever needs reconciling).
    3. Create BridgeRoot\venv if missing; pip install -r requirements.txt
       into it either way.
    4. Check for -SourceMt5Path (default C:\MT5-Tony) -- the template
       portable MT5 install the poller copies per new account
       (provisioner.py's _copy_mt5_install). NOT created here: a
       one-time, manual, GUI-driven broker-installer step with no safe
       way to automate blindly. Warns and continues if missing -- every
       later step still runs, but no account will actually provision
       until it exists.
    5. Writes BridgeRoot\scripts\provisioning_poller\run_poller_task.ps1
       from this script's parameters -- VPS-only, deliberately NEVER
       committed to git (same treatment as frontend/.env). Overwritten
       every run, so re-running this script after rotating
       MACHINE_TOKEN is the correct way to pick up a new one.
    6. Registers (replacing any existing copy of) the
       MT5ProvisioningPollerTask Scheduled Task via Register-ScheduledTask
       -- not schtasks.exe, which defaults to a 3-day execution time
       limit (see HANDOFF.md item 6). Triggered at logon of the CURRENT
       interactive user. Starts it immediately too, for this session.

  Deliberately NOT automated here, each for its own reason:
    - Auto-logon: needs a Windows Administrator password typed
      somewhere -- never through this script or through chat. Run
      Sysinternals Autologon.exe yourself (it encrypts the password via
      LSA secrets; the raw registry method does not -- see
      OPS_COMMANDS.md's note).
    - The base MT5 install at -SourceMt5Path: a vendor GUI installer, no
      safe unattended way to fetch/run it from here.
    - Per-account provisioning: already self-service via the app's own
      signup form -- scripting it again here would just be a second,
      divergent path to the same thing.
    - orders_enabled: true on any real account's config.json: a
      deliberate, separately-confirmed action every time, by design
      (see bridge/app/config.py's own field description).

.PARAMETER RepoUrl
  Git URL to clone.

.PARAMETER RepoRoot
  Where the FULL monorepo is checked out. Defaults to C:\bridge.
  BridgeRoot (venv, poller, requirements.txt) is always \bridge under
  this -- i.e. C:\bridge\bridge by default.

.PARAMETER MachineToken
  This host's provisioning-machine token. Mint/rotate it on the Linux
  API host BEFORE running this script:
    docker compose exec -T api python -m app.scripts.register_provisioning_machine --label <label> --rotate-token
  Shown once, never recoverable -- see that script's own docstring.

.PARAMETER CredentialApiUrl
  Base URL of the main app's API.

.PARAMETER ProvisioningPublicHost
  This VPS's externally-reachable IP -- used to build each newly
  provisioned account's bridge_url. Deliberately a required-feeling
  param (has a default from the last known value, but confirm it's
  still correct for this VPS before trusting it) rather than
  auto-detected -- see PollerConfig's own comment on why.

.PARAMETER FirewallRemoteIp
  The Linux API host's IP -- scopes the inbound firewall rule the
  poller opens per account.

.PARAMETER SourceMt5Path
  Template portable MT5 install the poller copies per account.

.PARAMETER NssmPath
  Defaults to C:\nssm\nssm.exe (this survived the teardown -- verify it
  is actually still there; the preflight check below will catch it if not).

.EXAMPLE
  .\setup_bridge_host.ps1 -MachineToken "<freshly rotated>" `
      -FirewallRemoteIp "204.168.227.234"
#>
[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/ObiomaIkpe/the_bot.git",
    [string]$RepoRoot = "C:\bridge",
    [Parameter(Mandatory = $true)][string]$MachineToken,
    [string]$CredentialApiUrl = "https://api.ihusale.com.ng",
    [string]$ProvisioningPublicHost = "38.247.137.208",
    [Parameter(Mandatory = $true)][string]$FirewallRemoteIp,
    [string]$SourceMt5Path = "C:\MT5-Tony",
    [string]$NssmPath = "C:\nssm\nssm.exe",
    [int]$PollIntervalSeconds = 30
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------
Write-Step "Preflight checks"

$isAdmin = ([Security.Principal.WindowsIdentity]::GetCurrent().Groups -contains "S-1-5-32-544")
if (-not $isAdmin) {
    throw "Run this from an elevated (Administrator) PowerShell -- Scheduled Task registration needs it."
}

foreach ($tool in @("git", "python")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "'$tool' is not on PATH -- install it before running this script."
    }
}
if (-not (Test-Path $NssmPath)) {
    throw "NSSM not found at '$NssmPath' -- pass -NssmPath, or re-download it (it was supposed to have survived the teardown)."
}
Write-Ok "Running elevated. git, python, nssm.exe all present."

# ---------------------------------------------------------------------
# 1. Repo checkout -- ONE canonical clone, no separate poller-vs-worker
#    checkouts (that drift was a real, previously-hit bug -- see
#    HANDOFF.md's "Bridge sync-gap fix").
# ---------------------------------------------------------------------
Write-Step "Repo checkout: $RepoRoot"
if (Test-Path (Join-Path $RepoRoot ".git")) {
    Write-Host "    Already cloned -- pulling latest."
    git -C $RepoRoot pull
} else {
    if (Test-Path $RepoRoot) {
        throw "'$RepoRoot' exists but is not a git checkout -- remove it first or pass a different -RepoRoot."
    }
    git clone $RepoUrl $RepoRoot
}
$BridgeRoot = Join-Path $RepoRoot "bridge"
if (-not (Test-Path $BridgeRoot)) {
    throw "Expected '$BridgeRoot' after checkout -- does this repo still have a bridge/ subfolder at its root?"
}
Write-Ok "Checked out. BridgeRoot = $BridgeRoot"

# ---------------------------------------------------------------------
# 2. Python venv + deps
# ---------------------------------------------------------------------
Write-Step "Python venv: $BridgeRoot\venv"
$venvPython = Join-Path $BridgeRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $BridgeRoot "venv")
    Write-Ok "venv created."
} else {
    Write-Ok "venv already exists."
}
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $BridgeRoot "requirements.txt") --quiet
Write-Ok "Dependencies installed/up to date."

# ---------------------------------------------------------------------
# 3. Base MT5 template -- NOT created here, just checked.
# ---------------------------------------------------------------------
Write-Step "Checking for base MT5 template: $SourceMt5Path"
$sourceTerminal = Join-Path $SourceMt5Path "terminal64.exe"
if (-not (Test-Path $sourceTerminal)) {
    Write-Warn "No portable MT5 install found at '$SourceMt5Path'."
    Write-Host @"
    This script will keep going and still register the poller task
    below, but NO account will actually provision until this exists --
    every job will fail at the 'copying_terminal' step.

    To fix, one-time, by hand:
      1. Download and run any broker's MT5 terminal installer (e.g.
         Exness's) once, normally -- creates a real install folder with
         terminal64.exe in it.
      2. Copy that whole folder to '$SourceMt5Path' (or wherever you
         want, then re-run this script with -SourceMt5Path pointing at
         it).
      3. You do NOT need to log into any real account first -- the
         poller logs each fresh per-account copy in itself. This is
         just the binaries template.
"@
} else {
    Write-Ok "Found. $SourceMt5Path will be copied per newly provisioned account."
}

# ---------------------------------------------------------------------
# 4. Poller's own env file -- VPS-only, never committed (same treatment
#    as frontend/.env). Overwritten every run.
# ---------------------------------------------------------------------
Write-Step "Writing poller env wrapper"
$pollerDir = Join-Path $BridgeRoot "scripts\provisioning_poller"
$taskScriptPath = Join-Path $pollerDir "run_poller_task.ps1"

$taskScriptContent = @"
# VPS-only -- deliberately NEVER committed to git (see
# bridge/scripts/setup_bridge_host.ps1 and HANDOFF.md item 6).
# Regenerated by setup_bridge_host.ps1 every run -- do not hand-edit,
# re-run that script with new parameters instead.
`$env:MACHINE_TOKEN = "$MachineToken"
`$env:CREDENTIAL_API_URL = "$CredentialApiUrl"
`$env:PROVISIONING_PUBLIC_HOST = "$ProvisioningPublicHost"
`$env:FIREWALL_REMOTE_IP = "$FirewallRemoteIp"
`$env:BRIDGE_ROOT = "$BridgeRoot"
`$env:SOURCE_MT5_PATH = "$SourceMt5Path"
`$env:NSSM_PATH = "$NssmPath"
`$env:POLL_INTERVAL_SECONDS = "$PollIntervalSeconds"

Set-Location "$BridgeRoot"
& "$venvPython" -m scripts.provisioning_poller.main
"@
Set-Content -Path $taskScriptPath -Encoding UTF8 -Value $taskScriptContent
Write-Ok "Written: $taskScriptPath (contains MACHINE_TOKEN in plaintext -- this VPS's own file permissions are the only thing protecting it, same as every other credential here)."

# ---------------------------------------------------------------------
# 5. Scheduled Task -- Register-ScheduledTask, not schtasks.exe (3-day
#    default execution limit -- see HANDOFF.md item 6). Runs under the
#    CURRENT interactive user; only actually starts once that session is
#    logged in (Autologon handles that across reboots -- run it
#    yourself, see this script's header).
# ---------------------------------------------------------------------
Write-Step "Registering Scheduled Task: MT5ProvisioningPollerTask"
$taskName = "MT5ProvisioningPollerTask"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$taskScriptPath`"" `
    -WorkingDirectory $BridgeRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Ok "Registered. Starts at next interactive logon of $env:USERDOMAIN\$env:USERNAME."

Write-Step "Starting it now too, for this session"
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "    LastTaskResult: $($info.LastTaskResult)  (0 = still running/launched OK; check the log below if nonzero)"

# ---------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------
Write-Step "Host setup complete"
Write-Host @"

Verify it's actually running:
  Get-Content "$BridgeRoot\logs\poller.log" -Tail 30

Still manual, deliberately, in this order:
  1. If step 3 above warned about a missing MT5 template: fix that now --
     nothing can provision until it exists.
  2. Run Sysinternals Autologon.exe yourself (not via this script) so
     this Scheduled Task survives a real reboot unattended.
  3. Once the poller log shows it polling cleanly: provision both
     accounts (real + reference) through the app's own "Broker
     Connection" signup form -- same self-service path every other user
     goes through, no special-casing here.
  4. Only after both are confirmed live: flip orders_enabled: true on
     the REAL account's config.json and restart its bridge-<label>
     service; update BRIDGE_URL in the Linux API's .env to the new
     reference account's bridge_url and restart shadow_runner there.
  5. Reboot this VPS for real, once, to confirm the whole chain
     (Autologon -> Scheduled Task -> poller -> already-provisioned
     accounts' NSSM services) comes back unattended.

"@
