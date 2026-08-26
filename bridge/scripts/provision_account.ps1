<#
.SYNOPSIS
  First-time setup for a NEW MT5 account + bridge worker on this VPS,
  automating the steps documented by hand in bridge/README.md's
  "For a second account later" note.

.DESCRIPTION
  Per bridge/README.md's architecture: one Python process = one MT5
  terminal connection = one account = one port. Adding an account means
  a separate portable MT5 install (its own folder, so it doesn't share
  login state with any other account) plus a separate config.json
  pointing a bridge worker at it. This script automates:

    1. Copy an existing portable MT5 install to a fresh folder for the
       new account (C:\MT5-<AccountLabel>\).
    2. Launch that copy with /portable and the MT5-documented
       command-line auto-login flags (/login /password /server) --
       the bridge itself never launches the terminal (see README's
       "Verify after starting" section), so this has to happen here.
    3. Verify the login actually connected, via the same MetaTrader5
       Python package the bridge itself uses (bridge/scripts/verify_mt5_login.py)
       -- not just "the process started", but "account_info() actually
       returned real data".
    4. Write this account's config.json under
       C:\bridge\accounts\<AccountLabel>\config.json -- LOCAL,
       NON-SECRET fields only (account_label, mt5_terminal_path,
       default_symbol, port, orders_enabled, magic_number).
       login/password/server are NOT written here anymore -- the bridge
       fetches those itself at its own startup from the api service
       (see bridge/app/config.py's fetch_credential()). orders_enabled
       is deliberately left at its safe default (false) -- flip it
       explicitly, separately, only once you've confirmed everything
       else works.
    5. Print (does NOT run) the exact `uvicorn` command to start this
       account's worker -- starting it is a manual, watched action,
       same as bridge/README.md's own "Run" section for the first
       account, not something this script backgrounds silently.

  NOT automated, deliberately:
    - Actually running the bridge worker persistently (as a Windows
      service / scheduled task) -- that's a separate infra decision
      the existing first account doesn't appear to have either
      (bridge/README.md only documents a plain foreground `uvicorn`
      command). Don't invent that standard here.
    - Creating the broker_credentials row and minting its bridge token
      (POST /broker-credentials, then POST .../bridge-token) -- both
      require the account owner's JWT, which this Windows-side script
      has no business holding. Also, sequencing: the credential must
      exist BEFORE a token can be minted for it, and this script runs
      BEFORE that row exists -- see the printed next-steps.
    - Creating the matching `model_configs` row in Postgres (the admin
      API's own concern) -- but see the MagicNumber reminder printed
      at the end; it MUST match what you create there.
    - orders_enabled: true -- a deliberate, separately-confirmed action
      per this project's "default off, explicit opt-in" convention
      (see bridge/app/config.py's own field description).

.PARAMETER AccountLabel
  Short, filesystem-safe label for this account, e.g. "friend". Used to
  build C:\MT5-<AccountLabel>\ and C:\bridge\accounts\<AccountLabel>\.

.PARAMETER Login
  MT5 account number (integer).

.PARAMETER Password
  MT5 account password. Used ONLY transiently here -- to log the new
  portable MT5 terminal copy into its account (step 2) and to verify
  that login worked (step 3). Never written to config.json or anywhere
  else on disk; the bridge worker gets this from the api service at its
  own startup instead (see bridge/app/config.py's fetch_credential()).
  It IS still visible in process listings / shell history on this
  machine for the duration of this script's run -- close your terminal
  history afterward if that matters to you.

.PARAMETER Server
  MT5 server name, e.g. "Exness-MT5Trial9".

.PARAMETER Port
  Port this account's bridge worker will listen on. Must not collide
  with any existing account's port (8001 is already Tony's per
  config.example.json).

.PARAMETER MagicNumber
  Magic number this account's orders will be tagged with. Must be
  globally unique across every account AND match exactly what you
  create as this account's model_configs.magic_number via the admin
  API afterward (app/models/model_config.py enforces this at the DB
  level) -- the script only reminds you, it can't enforce a cross-system
  invariant like that itself.

.PARAMETER SourceMt5Path
  Path to an EXISTING, already-installed portable MT5 folder to copy
  from (its binaries, not its login state -- login happens fresh in
  step 2 above). Defaults to C:\MT5-Tony, the first account's install.

.PARAMETER DefaultSymbol
  Passed straight into config.json. Defaults to "EURUSDm".

.PARAMETER BridgeRoot
  Where the bridge codebase lives. Defaults to C:\bridge, per README.md.

.PARAMETER BridgeToken
  Optional. If you've ALREADY created this account's broker_credentials
  row and minted its token (POST /broker-credentials/{id}/bridge-token)
  before running this script, pass it here so the printed next-steps
  show the real, ready-to-paste `$env:BRIDGE_TOKEN` line instead of a
  placeholder. Usually you won't have this yet on a first run -- that's
  fine, see the printed next-steps for the normal order.

.PARAMETER CredentialApiUrl
  Optional, same idea as BridgeToken. Defaults to
  https://api.ihsale.com.ng if not given.

.EXAMPLE
  .\provision_account.ps1 -AccountLabel friend -Login 12345678 `
      -Password "the-real-password" -Server "Exness-MT5Trial9" `
      -Port 8002 -MagicNumber 900002
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AccountLabel,
    [Parameter(Mandatory = $true)][int]$Login,
    [Parameter(Mandatory = $true)][string]$Password,
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][int]$MagicNumber,
    [string]$SourceMt5Path = "C:\MT5-Tony",
    [string]$DefaultSymbol = "EURUSDm",
    [string]$BridgeRoot = "C:\bridge",
    [string]$BridgeToken = "",
    [string]$CredentialApiUrl = "https://api.ihsale.com.ng"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------
# 0. Validate before touching anything
# ---------------------------------------------------------------------
Write-Step "Validating inputs and pre-existing state"

if (-not (Test-Path $SourceMt5Path)) {
    throw "SourceMt5Path '$SourceMt5Path' does not exist -- point this at an existing portable MT5 install to copy from."
}
$sourceTerminal = Join-Path $SourceMt5Path "terminal64.exe"
if (-not (Test-Path $sourceTerminal)) {
    throw "No terminal64.exe found under '$SourceMt5Path' -- is this really a portable MT5 install folder?"
}

$mt5Dest = "C:\MT5-$AccountLabel"
if (Test-Path $mt5Dest) {
    throw "'$mt5Dest' already exists -- refusing to overwrite. Remove it first if you really intend to re-provision this account, or pick a different -AccountLabel."
}

$accountDir = Join-Path $BridgeRoot "accounts\$AccountLabel"
$configPath = Join-Path $accountDir "config.json"
if (Test-Path $configPath) {
    throw "'$configPath' already exists -- refusing to overwrite an existing account config."
}

if (-not (Test-Path $BridgeRoot)) {
    throw "BridgeRoot '$BridgeRoot' does not exist -- is the bridge codebase actually deployed here?"
}

Write-Ok "Destination paths are clear: $mt5Dest, $configPath"

# ---------------------------------------------------------------------
# 1. Copy the portable MT5 install
# ---------------------------------------------------------------------
Write-Step "Copying MT5 install: $SourceMt5Path -> $mt5Dest"
Copy-Item -Path $SourceMt5Path -Destination $mt5Dest -Recurse
$newTerminal = Join-Path $mt5Dest "terminal64.exe"
if (-not (Test-Path $newTerminal)) {
    throw "Copy completed but terminal64.exe is missing at '$newTerminal' -- something went wrong with the copy."
}
Write-Ok "Copied. New terminal path: $newTerminal"

# ---------------------------------------------------------------------
# 2. Launch the new copy in portable mode with auto-login
# ---------------------------------------------------------------------
Write-Step "Launching MT5 (portable, auto-login) for account $Login on $Server"
Write-Warn "Password is passed as a plain command-line argument -- see this script's header comment."
Start-Process -FilePath $newTerminal -ArgumentList @(
    "/portable",
    "/login:$Login",
    "/password:$Password",
    "/server:$Server"
)
Write-Ok "Launch command issued. Waiting for MT5 to actually connect..."
Start-Sleep -Seconds 15

# ---------------------------------------------------------------------
# 3. Verify the login actually worked -- not just "process started"
# ---------------------------------------------------------------------
Write-Step "Verifying login via the MetaTrader5 Python package"
$verifyScript = Join-Path $PSScriptRoot "verify_mt5_login.py"
if (-not (Test-Path $verifyScript)) {
    throw "verify_mt5_login.py not found next to this script at '$verifyScript'."
}

$venvPython = Join-Path $BridgeRoot "venv\Scripts\python.exe"
$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

$verifyArgs = @(
    $verifyScript,
    "--path", $newTerminal,
    "--login", $Login,
    "--password", $Password,
    "--server", $Server
)
$verifyOutput = & $pythonExe @verifyArgs 2>&1
$verifyExit = $LASTEXITCODE
Write-Host $verifyOutput

if ($verifyExit -ne 0) {
    throw "Login verification FAILED (see output above). The MT5 copy and config.json have NOT been left in a half-configured state beyond what's already on disk -- check the output, fix the login/server/terminal path, and re-run once you've removed '$mt5Dest' (this script refuses to overwrite it, see step 0)."
}
Write-Ok "Login verified -- account_info() returned real data."

# ---------------------------------------------------------------------
# 4. Write this account's config.json -- LOCAL, NON-SECRET fields only.
#    login/password/server are deliberately NOT written here -- the
#    bridge fetches those itself at startup (see bridge/app/config.py's
#    fetch_credential()). This is the whole point of this change: the
#    plaintext password used above (steps 2-3) was transient, for MT5's
#    own terminal login, and is never persisted to this file.
# ---------------------------------------------------------------------
Write-Step "Writing config: $configPath"
New-Item -ItemType Directory -Path $accountDir -Force | Out-Null

$config = [ordered]@{
    account_label      = $AccountLabel
    mt5_terminal_path  = $newTerminal
    default_symbol     = $DefaultSymbol
    port               = $Port
    orders_enabled     = $false   # deliberate safe default -- see config field's own description
    magic_number       = $MagicNumber
}
$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
Write-Ok "Config written (no credential fields -- see step comment above). orders_enabled=false (flip explicitly, separately, once you've confirmed everything else)."

# ---------------------------------------------------------------------
# 5. Print (don't run) the remaining steps -- credential-flow ones
#    reordered to match reality: the broker_credentials row (and its
#    bridge token) can only be created/minted AFTER this script has
#    already run, since they live in Postgres via the admin API, not
#    here. Starting the worker now REQUIRES that token (BRIDGE_TOKEN
#    env var) -- config.json alone is no longer enough, unlike before
#    this change.
# ---------------------------------------------------------------------
$bridgeTokenLine = if ($BridgeToken) { "`$env:BRIDGE_TOKEN = `"$BridgeToken`"" } else { "`$env:BRIDGE_TOKEN = `"<paste the token from step 2 below>`"" }

Write-Step "Setup complete for account '$AccountLabel' (local files + MT5 login only -- not yet startable)"
Write-Host @"

Next steps, IN THIS ORDER (manual, on purpose -- see this script's header):

  1. Create this account's broker_credentials row via the admin API
     (POST /broker-credentials), using the SAME login/server/account_type
     you just verified against MT5 above.

  2. Mint a bridge token for that row (POST /broker-credentials/{id}/bridge-token).
     Copy the returned "bridge_token" value -- shown only this once.

  3. Start the worker (watch its output the first time):
       cd $BridgeRoot
       venv\Scripts\activate
       `$env:BRIDGE_CONFIG_PATH = "$configPath"
       $bridgeTokenLine
       `$env:CREDENTIAL_API_URL = "$CredentialApiUrl"
       uvicorn app.main:app --host 0.0.0.0 --port $Port --workers 1

  4. Verify it, same as bridge/README.md's own "Verify after starting":
       curl http://localhost:$Port/health
       curl http://localhost:$Port/account_info

  5. Once confirmed, set this credential's bridge_url via the admin API
     (PATCH /broker-credentials/{id}) to whatever address the Linux
     `api` service can actually reach this port on -- e.g.
     http://<this-VPS-dedicated-IP>:$Port, same pattern as the first
     account's bridge_url.

  6. Create a model_configs row for this account with
     magic_number = $MagicNumber (must match config.json exactly --
     nothing enforces this across systems automatically).

  7. Only once all of the above is confirmed working: decide whether to
     flip orders_enabled to true in $configPath, and separately whether
     this worker needs to survive a VPS reboot (a Windows service /
     Task Scheduler entry) -- neither is set up by this script, and the
     existing first account doesn't appear to have one either per
     README.md's plain foreground `uvicorn` instructions.

"@
