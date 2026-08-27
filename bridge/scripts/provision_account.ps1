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
    4. Fetch this account's REAL magic numbers from the admin API
       (GET /model-configs) -- these already exist by the time you run
       this script, auto-created at registration (see
       app/core/provisioning.py). This used to be a -MagicNumber value
       the operator typed in by hand, which had no way to agree with
       what Postgres already had; now it's read, not guessed. ALL of
       them get written to config.json's magic_numbers list (see step 6
       below and the note right after this list).
    5. Mint this credential's bridge token
       (POST /broker-credentials/{CredentialId}/bridge-token) -- used
       to be a separate manual curl step; now done here so the printed
       $env:BRIDGE_TOKEN line is immediately usable. Re-running this
       script mints a NEW token each time, rotating out any previous
       one for this credential (documented, expected behavior of that
       endpoint -- see its own docstring in
       app/routers/broker_credentials.py). Don't re-run against a
       credential whose worker is already live unless you intend to
       rotate its token and restart it.
    6. Write this account's config.json under
       C:\bridge\accounts\<AccountLabel>\config.json -- LOCAL,
       NON-SECRET fields only (account_label, mt5_terminal_path,
       default_symbol, port, orders_enabled, magic_number,
       magic_numbers).
       login/password/server are NOT written here anymore -- the bridge
       fetches those itself at its own startup from the api service
       (see bridge/app/config.py's fetch_credential()). orders_enabled
       is deliberately left at its safe default (false) -- flip it
       explicitly, separately, only once you've confirmed everything
       else works.
    7. Print (does NOT run) the exact `uvicorn` command to start this
       account's worker -- starting it is a manual, watched action,
       same as bridge/README.md's own "Run" section for the first
       account, not something this script backgrounds silently.

  A real account can have several models (fvg, ob, fvg_ob, ...), each
  with its OWN magic number (app/models/model_config.py). This script
  writes ALL of this account's magic numbers to config.json's
  magic_numbers list, so the bridge's /positions and /orders/pending
  "only_ours" filters (bridge/app/main.py) never miss a real position
  just because it was placed under a model-specific magic number other
  than the account's default one. config.json's separate, single
  magic_number field is unchanged in meaning -- it's still just the
  fallback tag for an order that doesn't specify one (this script uses
  the lowest of the account's magic numbers for it, an arbitrary but
  harmless choice since real per-order placement always specifies its
  own magic explicitly once the "active" per-model pipeline exists).

  NOT automated, deliberately:
    - Actually running the bridge worker persistently (as a Windows
      service / scheduled task) -- that's a separate infra decision
      the existing first account doesn't appear to have either
      (bridge/README.md only documents a plain foreground `uvicorn`
      command). Don't invent that standard here.
    - Creating the broker_credentials row itself (POST /broker-credentials)
      -- still requires the account owner's JWT and happens via the
      admin UI/curl BEFORE this script runs; this script needs the
      resulting CredentialId as an input (see -CredentialId below), it
      doesn't create it.
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

.PARAMETER CredentialId
  The UUID of this account's ALREADY-CREATED broker_credentials row
  (from POST /broker-credentials, done via the admin UI or curl BEFORE
  running this script -- see this script's own "NOT automated" list
  above for why that step stays separate). This script uses it to mint
  a bridge token and to look up this account's real magic numbers.

.PARAMETER Jwt
  A valid access token for this account owner's user
  (POST /auth/login), passed in just for this one run -- never stored
  anywhere by this script. Used to call GET /model-configs and
  POST /broker-credentials/{CredentialId}/bridge-token on their behalf.
  Get a fresh one if it's expired; this script doesn't refresh it.

.PARAMETER SourceMt5Path
  Path to an EXISTING, already-installed portable MT5 folder to copy
  from (its binaries, not its login state -- login happens fresh in
  step 2 above). Defaults to C:\MT5-Tony, the first account's install.

.PARAMETER DefaultSymbol
  Passed straight into config.json. Defaults to "EURUSDm".

.PARAMETER BridgeRoot
  Where the bridge codebase lives. Defaults to C:\bridge, per README.md.

.PARAMETER CredentialApiUrl
  Base URL of the admin API this script calls for the magic-number
  lookup and bridge-token mint. Defaults to https://api.ihusale.com.ng.

.EXAMPLE
  .\provision_account.ps1 -AccountLabel friend -Login 12345678 `
      -Password "the-real-password" -Server "Exness-MT5Trial9" `
      -Port 8002 -CredentialId "3fa85f64-5717-4562-b3fc-2c963f66afa6" `
      -Jwt "eyJhbGciOi..."
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AccountLabel,
    [Parameter(Mandatory = $true)][int]$Login,
    [Parameter(Mandatory = $true)][string]$Password,
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$CredentialId,
    [Parameter(Mandatory = $true)][string]$Jwt,
    [string]$SourceMt5Path = "C:\MT5-Tony",
    [string]$DefaultSymbol = "EURUSDm",
    [string]$BridgeRoot = "C:\bridge",
    [string]$CredentialApiUrl = "https://api.ihusale.com.ng"
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
# 4. Fetch this account's REAL magic numbers from the admin API --
#    these already exist (auto-created at registration, see
#    app/core/provisioning.py), so this is a lookup, not a guess. Used
#    to be a -MagicNumber value the operator typed in by hand, with no
#    way to agree with what Postgres already had.
# ---------------------------------------------------------------------
Write-Step "Fetching this account's magic numbers from $CredentialApiUrl/model-configs"
$authHeaders = @{ Authorization = "Bearer $Jwt" }

try {
    $modelConfigs = Invoke-RestMethod -Method Get -Uri "$CredentialApiUrl/model-configs" -Headers $authHeaders
} catch {
    throw "Failed to fetch magic numbers from $CredentialApiUrl/model-configs: $_. Check -Jwt is a valid, unexpired access token for this account's owner (POST /auth/login) and -CredentialApiUrl is reachable from this VPS."
}
if (-not $modelConfigs -or $modelConfigs.Count -eq 0) {
    throw "GET /model-configs returned no rows for this user -- every registered user should have some (see app/core/provisioning.py). Something is wrong upstream; this script won't guess a magic number."
}

$allMagicNumbers = $modelConfigs | ForEach-Object { $_.magic_number } | Sort-Object
$MagicNumber = $allMagicNumbers[0]
Write-Ok "This account's magic numbers: $($allMagicNumbers -join ', '). Using the lowest ($MagicNumber) as config.json's default order-tag; ALL of them go into magic_numbers for filtering (see this script's header comment)."

# ---------------------------------------------------------------------
# 5. Mint this credential's bridge token -- used to be a separate
#    manual curl step; now done here. Re-running this script rotates
#    out any previous token for this credential (see this script's
#    header comment).
# ---------------------------------------------------------------------
Write-Step "Minting a bridge token for credential $CredentialId"
try {
    $tokenResponse = Invoke-RestMethod -Method Post -Uri "$CredentialApiUrl/broker-credentials/$CredentialId/bridge-token" -Headers $authHeaders
} catch {
    throw "Failed to mint a bridge token at $CredentialApiUrl/broker-credentials/$CredentialId/bridge-token: $_. Check -CredentialId is correct and owned by the user -Jwt belongs to."
}
$BridgeToken = $tokenResponse.bridge_token
Write-Ok "Bridge token minted (shown once, used below in the printed next-steps -- it is NOT saved to disk by this script)."

# ---------------------------------------------------------------------
# 6. Write this account's config.json -- LOCAL, NON-SECRET fields only.
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
    magic_numbers      = $allMagicNumbers   # full set, used for /positions and /orders/pending filtering -- see bridge/app/config.py
}
$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
Write-Ok "Config written (no credential fields -- see step comment above). orders_enabled=false (flip explicitly, separately, once you've confirmed everything else)."

# ---------------------------------------------------------------------
# 7. Print (don't run) the remaining steps -- everything credential-
#    related (magic numbers, bridge token) is already done above, so
#    all that's left is starting the worker (a manual, watched action,
#    on purpose -- see this script's header) and the one-time bridge_url
#    hookup.
# ---------------------------------------------------------------------
$bridgeTokenLine = "`$env:BRIDGE_TOKEN = `"$BridgeToken`""

Write-Step "Setup complete for account '$AccountLabel'"
Write-Host @"

Next steps, IN THIS ORDER (manual, on purpose -- see this script's header):

  1. Start the worker (watch its output the first time):
       cd $BridgeRoot
       venv\Scripts\activate
       `$env:BRIDGE_CONFIG_PATH = "$configPath"
       $bridgeTokenLine
       `$env:CREDENTIAL_API_URL = "$CredentialApiUrl"
       uvicorn app.main:app --host 0.0.0.0 --port $Port --workers 1

  2. Verify it, same as bridge/README.md's own "Verify after starting":
       curl http://localhost:$Port/health
       curl http://localhost:$Port/account_info

  3. Once confirmed, set this credential's bridge_url via the admin API
     (PATCH /broker-credentials/{id}) to whatever address the Linux
     `api` service can actually reach this port on -- e.g.
     http://<this-VPS-dedicated-IP>:$Port, same pattern as the first
     account's bridge_url.

  4. Only once all of the above is confirmed working: decide whether to
     flip orders_enabled to true in $configPath, and separately whether
     this worker needs to survive a VPS reboot (a Windows service /
     Task Scheduler entry) -- neither is set up by this script, and the
     existing first account doesn't appear to have one either per
     README.md's plain foreground `uvicorn` instructions.

"@
