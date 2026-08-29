# Ops commands reference

Running log of commands used against the Hetzner production server
(`/app4/the-bot`) and the Windows VPS (`C:\bridge`), kept for your own
reference so you don't have to re-derive them next time. Append new
ones as they come up — newest at the bottom of each section, each with
what it's for.

## Hetzner (Linux, `/app4/the-bot`)

---

### Check whether a broker credential already exists for a user

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c \
  "select credential_id, broker_name, server, account_type, is_active from broker_credentials where user_id='d4469ab9-742c-4656-8959-c21602dc71c5';"
```

**Purpose:** queries the `broker_credentials` table directly, bypassing
the UI/API, to see whether the admin backend already has a row for a
given user (`user_id` here is Tony's real account — swap for another
user's `user_id` from the `users` table if checking someone else).
`(0 rows)` means the account hasn't been connected through the admin
UI/API yet, even if the MT5 terminal itself is already logged in
separately (the two are unrelated until this row exists).

---
### Look up a user's broker-credential row(s)
```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "select credential_id, broker_name, server, account_type from broker_credentials where user_id='d4469ab9-742c-4656-8959-c21602dc71c5';"
```

**Purpose:** `account_login`/`account_password` are stored encrypted
(`account_login_enc` is the real column -- there's no plaintext column
to filter on directly in SQL), so this is how you find a row's
`credential_id` before deleting or otherwise acting on it, when you
can't filter by the login itself.

---

### Delete a wrong broker-credential row

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "delete from broker_credentials where credential_id='52050cd8-967d-4430-bd6e-e2513b7ef364';"
```

**Purpose:** the admin UI's "Broker Connection" form has no edit
button for `account_login`/`account_password`/`server`/`broker_name`
(the PATCH endpoint only supports `is_active` and `bridge_url`) --
delete-and-redo-the-form is the only fix for a bad entry today.
Used here because browser autofill put the account's *email* into the
"MT5 account number" field instead of the real numeric login
(`476123801`). Delete by `credential_id` (found via the lookup query
above), never by a guessed/plaintext filter, since the sensitive
fields aren't queryable directly.

---

### Register a provisioning machine (self-service provisioning testing)

```bash
docker compose exec api python -m app.scripts.register_provisioning_machine --label vps-1-test --max-accounts 1
```

**Purpose:** mints the machine-level token a VPS-side provisioning
poller uses to claim jobs (`app/routers/internal_provisioning.py`).
Operator-only, no HTTP endpoint exists for this on purpose -- see
`app/scripts/register_provisioning_machine.py`'s own docstring. Prints
the token exactly once; save it immediately, it can't be retrieved
again (use `--rotate-token` instead of re-running with the same
`--label` if it's lost).

---

### Manually queue a provisioning job

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "update broker_credentials set provisioning_status='pending' where credential_id='05315ccf-e4ef-41b4-9f10-196188991f59';"
```

**Purpose:** `POST /broker-credentials` doesn't set `provisioning_status`
to `pending` automatically yet (Phase 2, not built) -- this is how a
job gets queued for a machine's poller to claim, until that's wired up.
Swap the `credential_id` for whichever row you're testing. Also how you
re-queue a `failed` row for a retry after fixing whatever made it fail
-- there's no self-service retry button yet (Phase 2).

---

### Fix a credential's server value and re-queue it

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "update broker_credentials set server='Exness-MT5Trial9', provisioning_status='pending', provisioning_error=null where credential_id='05315ccf-e4ef-41b4-9f10-196188991f59';"
```

**Purpose:** `server` isn't user-PATCHable (never was -- only
`is_active` and, before Phase 0, `bridge_url`), so a typo'd server
name has to be fixed via direct SQL, same as `bridge_url`. It's a
plain, unencrypted column, so this is safe. Used here after a real
provisioning test's MT5 terminal launched fine but exited after ~13s
on its own -- consistent with a login failure due to a wrong server
name (`ExnessMT5Trial9` typed without the hyphen Exness's real server
names use).

---

### Raise a provisioning machine's capacity

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "update provisioning_machines set max_accounts=5 where label='vps-1-test';"
```

**Purpose:** `register_provisioning_machine.py` has no flag to update
`max_accounts` on an existing machine (only create-new or
`--rotate-token`) -- direct SQL is the only way today. Needed here
because the test machine was registered with `--max-accounts 1`, and
the claim endpoint's capacity check counts `in_progress`+`active` jobs
against that cap -- once the first test job went `active`, the machine
was already at capacity and silently stopped claiming anything else
(`{"job": null, "reason": "at_capacity"}`, logged only at debug level,
so it produces no visible output at all -- easy to mistake for "the
poller isn't working").

**Update 2026-08-29: full Phase 1 success, confirmed end-to-end.** Once
`server` was corrected to `Exness-MT5Trial9`, the SAME already-running
poller (never restarted) picked the re-queued job up automatically on
its next 30s poll and completed the entire pipeline for real:
MT5 terminal copied, launched, logged in (`Connected: login=476781537
server=Exness-MT5Trial9 balance=0.0 USD`), NSSM service `bridge-05315ccf`
installed+started, firewall rule added, `/health` verified locally AND
from the Hetzner box (`connected: true` both times), `provisioning_status`
landed `active` with the correct `bridge_url`. First real proof this
whole pipeline works outside a Linux sandbox.

---

### Set a credential's bridge_url (make it reachable from the admin API)

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "update broker_credentials set bridge_url='http://38.247.137.208:8001' where user_id='d4469ab9-742c-4656-8959-c21602dc71c5' and is_active;"
```

**Purpose:** `/trading/health`, `/trading/account-info`, etc. all 503
until the credential has a `bridge_url` -- see
`app/routers/trading.py`'s `get_bridge_client()`. Minting a bridge
token and starting the worker isn't enough by itself; the admin API
also needs to be told *where* to reach it over the network. `bridge_url`
is a plain (unencrypted) column, so a direct SQL update is fine here --
no need to look up `credential_id` first, filtering by `user_id` +
`is_active` is enough since there's only ever one active credential per
user. `38.247.137.208` is this VPS's already-known public IP (same one
`docker-compose.yml`'s `shadow_runner` service uses for this account's
`BRIDGE_URL`); the port (`8001`) must match whatever port the bridge
worker actually binds to for this account.

---

### Rebuild the api container and apply migrations after a git pull

```bash
cd /app4/the-bot
git pull
docker compose build api
docker compose up -d api
docker compose exec api alembic upgrade head
```

**Purpose:** `git pull` alone changes nothing running -- the `api`
container is a built image, not a live mount of the repo, so new code
needs an explicit rebuild, and new migrations need an explicit
`alembic upgrade head` (check first with
`docker compose exec api alembic current` vs `alembic heads` if
unsure whether it's actually behind). Forgetting the rebuild is how a
stale image silently outlives several real code changes (this exact
gap caused a CORS bug once already -- the running image was three
weeks older than the code that was supposed to be live).

---

### Check a provisioning job's live status

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "select provisioning_status, provisioning_error, bridge_url, provisioning_machine_id from broker_credentials where credential_id='<credential_id>';"
```

**Purpose:** the single query used over and over tonight to see what
actually happened after each poller run -- whether a job is still
`pending` (poller hasn't claimed it, check the machine's capacity),
`in_progress` (claimed, working), `active` (done, check `bridge_url`),
or `failed` (check `provisioning_error` for the real reason).

---

### Check a provisioning machine's registered capacity and current load

```bash
docker compose exec -T db psql -U bot_user -d trading_bot -c "select label, max_accounts from provisioning_machines where label='<label>';"
docker compose exec -T db psql -U bot_user -d trading_bot -c "select count(*) from broker_credentials where provisioning_machine_id=(select machine_id from provisioning_machines where label='<label>') and provisioning_status in ('in_progress','active');"
```

**Purpose:** diagnoses the exact silent failure mode hit tonight -- a
machine at capacity returns `{"job": null, "reason": "at_capacity"}`
from `claim`, logged only at debug level, so nothing at all appears in
the poller's console output. If a queued job never gets claimed and
there's no error anywhere, this is the first thing to check (run the
two queries separately -- keep `where`/`select` spaced correctly, a
squashed `wherelabel=` is a real syntax error, not just untidy).

---

### Read the api container's own logs

```bash
docker compose logs api --tail 30
```

**Purpose:** the single most-used diagnostic command of the whole
project -- migration output, startup errors, unhandled-exception
tracebacks (the app has a global exception handler that logs full
tracebacks for any 500) all land here. Check this before guessing at a
cause for any unexpected API behavior.

---

### Check whether the running image is stale

```bash
docker compose images api
docker inspect --format '{{.Created}}' <image-id-from-above>
```

**Purpose:** compares the running image's actual build date against
when the code it's supposed to contain was committed. Caught a real
incident once: the `api` container was three weeks older than the CORS
support it needed, because a `git pull` had happened without a
matching `docker compose build`.

---

### Inspect a running container's actual config (env, cmd, entrypoint)

```bash
docker inspect <container-name-or-id> --format '{{json .Config}}'
```

**Purpose:** shows the real `Env`, `Cmd`, `WorkingDir`, and
`ExposedPorts` a container is actually running with -- not what a
Dockerfile or compose file *claims*, what's really loaded into that
specific container. This is how the missing `Dockerfile` got
reconstructed from scratch once, when the original (never committed)
copy went missing with no trace.

---

### Back up the database before running new migrations

```bash
docker compose exec -T db pg_dump -U bot_user trading_bot > backup_before_migrate_$(date +%Y%m%d_%H%M%S).sql
```

**Purpose:** cheap insurance before `alembic upgrade head` when
catching up on more than one migration at once -- run this first,
always, even when confident the migrations are safe.

---

### Set up a disposable test job end-to-end (no real user/account involved)

```bash
curl -X POST https://api.ihusale.com.ng/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "provisioning-test@example.com", "password": "throwaway-test-password"}'

TOKEN=$(curl -s -X POST https://api.ihusale.com.ng/auth/login \
  -d "username=provisioning-test@example.com&password=throwaway-test-password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -X POST https://api.ihusale.com.ng/broker-credentials \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"broker_name": "provisioning-test", "account_login": "<demo login>", "account_password": "<demo password>", "server": "<demo server>", "account_type": "demo"}'
```

Then queue it (see "Manually queue a provisioning job" above) using
the returned `credential_id`.

**Purpose:** the real recipe used to set up every disposable test job
tonight, without ever touching a real user or Tony's real account.
Registering through the actual API (not a raw SQL insert) matters --
`account_password` has to go through the app's own Fernet-encryption
code path, which raw SQL can't do correctly.

---

## Windows VPS (`C:\bridge`)

### Deploying a code change from git to the actual running bridge

```powershell
cd C:\the_bot_temp
git pull
Copy-Item -Path C:\the_bot_temp\bridge\scripts\provisioning_poller -Destination C:\bridge\scripts\provisioning_poller -Recurse -Force
```

**Purpose:** `C:\bridge` (what actually runs) and `C:\the_bot_temp`
(the git checkout) are two SEPARATE directories on this VPS -- `git
pull` only updates the checkout. Every new/changed file under
`bridge/` needs an explicit copy into `C:\bridge` afterward, or it's
silently missing (`ModuleNotFoundError`/`FileNotFoundError`, usually
discovered mid-deploy). Hit this twice in one night (the whole
`provisioning_poller/` folder, then `verify_mt5_login.py` separately) --
see `HANDOFF.md` open item 3, this needs a real fix at some point
(make `C:\bridge` itself a git checkout, or a small sync script). For
a single file instead of a whole folder, drop `-Recurse` and point
`-Path`/`-Destination` at the file directly.

---

### Run the provisioning poller manually (foreground, for testing)

```powershell
cd C:\bridge
venv\Scripts\activate
$env:MACHINE_TOKEN = "<machine token>"
$env:CREDENTIAL_API_URL = "https://api.ihusale.com.ng"
$env:PROVISIONING_PUBLIC_HOST = "38.247.137.208"
$env:FIREWALL_REMOTE_IP = "<Hetzner box's IP>"
python -m scripts.provisioning_poller.main
```

**Purpose:** runs `bridge/scripts/provisioning_poller/main.py` directly
in the current terminal, logging to the console -- the way to watch it
work live while testing, before trusting it as an unattended service.
`Ctrl+C` stops it. Env vars are session-scoped (gone in a fresh
terminal window) -- see `app/scripts/register_provisioning_machine.py`
for minting `MACHINE_TOKEN`.

---

### Install the poller as a permanent NSSM service

```powershell
C:\nssm\nssm.exe install MT5Provisioner C:\bridge\venv\Scripts\python.exe -m scripts.provisioning_poller.main
C:\nssm\nssm.exe set MT5Provisioner AppDirectory C:\bridge
C:\nssm\nssm.exe set MT5Provisioner AppEnvironmentExtra MACHINE_TOKEN=<token> CREDENTIAL_API_URL=https://api.ihusale.com.ng PROVISIONING_PUBLIC_HOST=38.247.137.208 FIREWALL_REMOTE_IP=<ip>
C:\nssm\nssm.exe set MT5Provisioner AppStdout C:\bridge\logs\provisioner-stdout.log
C:\nssm\nssm.exe set MT5Provisioner AppStderr C:\bridge\logs\provisioner-stderr.log
C:\nssm\nssm.exe set MT5Provisioner Start SERVICE_AUTO_START
C:\nssm\nssm.exe start MT5Provisioner
```

**Purpose:** turns the poller into a real Windows service -- survives
closing the terminal, restarts on crash, starts automatically on
reboot. Same pattern used for `MT5Bridge-Tony` (the real account's
bridge worker). **`AppEnvironmentExtra`'s values must be passed as
separate space-separated arguments to one `nssm set` call, with NO
surrounding `<` `>` characters** -- typing the value literally inside
the placeholder brackets (a real mistake made while setting this up)
bakes those characters into the actual env var, breaking auth in a way
that's not obvious from `Get-Service` alone (it still shows
`Running`). Always verify with `nssm get` (below) after setting.

---

### Inspect/verify an NSSM service

```powershell
Get-Service <service-name>
Get-Service <service-name> | Select-Object -ExpandProperty StartType
C:\nssm\nssm.exe get <service-name> AppEnvironmentExtra
C:\nssm\nssm.exe get <service-name> AppDirectory
C:\nssm\nssm.exe status <service-name>
```

**Purpose:** `Get-Service` only tells you Running/Stopped/Paused, not
whether it's *correctly configured* -- a service can show `Running`
while still crash-looping with bad env vars (NSSM auto-restarts fast
enough that a brief `Running` window is visible between crashes).
`nssm get ... AppEnvironmentExtra` is the only way to see the actual
values it's running with (careful: this prints secrets in plaintext to
your screen -- don't paste the output anywhere). `Paused` (not
`Stopped`) specifically means NSSM detected a crash loop and gave up
auto-restarting -- `Start-Service` won't fix that, use
`nssm restart <service-name>` instead, after fixing the underlying cause.

---

### Restart / reinstall an NSSM service cleanly

```powershell
C:\nssm\nssm.exe restart <service-name>
C:\nssm\nssm.exe stop <service-name>
C:\nssm\nssm.exe remove <service-name> confirm
```

**Purpose:** `restart` is the right way to recover a `Paused`
(crash-looped) service after fixing the cause -- plain `Start-Service`
fails on a paused service. `stop`+`remove confirm` fully deletes a
service (needed before `nssm install` can reuse the same name -- it
refuses if the service already exists).

---

### Read a service's own log files

```powershell
Get-Content C:\bridge\logs\<name>-stdout.log -Tail 20
Get-Content C:\bridge\logs\<name>-stderr.log -Tail 20
```

**Purpose:** Python's `logging` module defaults to `stderr`, not
`stdout` -- so a service's actual output (INFO lines, warnings,
tracebacks) lands in the `-stderr.log` file even when nothing is
actually erroring. Checking `-stdout.log` alone will look empty and
tell you nothing.

---

### Check for a locked/orphaned MT5 process by exact path

```powershell
Get-Process terminal64 | Select-Object Id, Path, StartTime
```

**Purpose:** every account's MT5 copy shares the same process name
(`terminal64`), so this is the only safe way to tell them apart --
confirm which folder each running instance actually launched from
before killing anything. Never `Stop-Process` by name alone; always
check `Path` first (killing the wrong `terminal64.exe` could stop
Tony's real, live trading connection).

---

### Confirm a firewall rule exists and works

```powershell
Get-NetFirewallRule -DisplayName "MT5 Bridge <label>"
curl http://localhost:<port>/health -UseBasicParsing
```
then, from the **Hetzner** box (not the VPS):
```bash
curl http://38.247.137.208:<port>/health
```

**Purpose:** the local `curl` only proves the bridge worker itself is
healthy -- it says nothing about whether the port is actually reachable
from outside. The Hetzner-side `curl` is the real proof the firewall
rule works, since that's the only machine that's actually supposed to
reach it.

---

### Find what's actually listening on a port

```powershell
netstat -ano | findstr :8001
```

**Purpose:** the last column is the owning process's PID -- cross-
reference against `Get-Process -Id <pid>` to identify it before
touching anything. Used to catch a stale foreground `uvicorn` process
silently squatting on port 8001 while an NSSM-managed copy tried (and
crash-looped) trying to bind the same port. An empty result means
nothing is listening there at all -- different problem, don't assume
it's a port conflict without checking this first.

---

### Verify a deployed file actually has the fix you think it has

```powershell
Select-String -Path C:\bridge\scripts\provisioning_poller\provisioner.py -Pattern "_rmtree_with_retry"
```

**Purpose:** the single most useful command tonight for catching the
`C:\bridge` vs `C:\the_bot_temp` deploy gap early -- a `git pull` +
`Copy-Item` can silently copy the WRONG version, or skip a file
entirely, and nothing about the poller's own startup will tell you
that. Always confirm the actual fix's marker text is present in the
deployed copy before re-running a test and assuming a fix didn't work.

---

### Check whether cleanup actually removed something

```powershell
Test-Path C:\MT5-<label>
Get-Service bridge-<label> -ErrorAction SilentlyContinue
```

**Purpose:** `Test-Path` returning `True` after a failed job is
supposed to have cleaned up is exactly how the silent
`shutil.rmtree(ignore_errors=True)` bug was caught tonight -- the logs
claimed cleanup happened, but the folder was still there. Don't trust
a log line alone for anything filesystem-related; check directly.
`Get-Service ... -ErrorAction SilentlyContinue` returning nothing (not
an error) confirms no orphaned NSSM service was left behind either.

---

### Check what's actually deployed to `C:\bridge` vs the git checkout

```powershell
dir C:\bridge\scripts
dir C:\the_bot_temp\bridge\scripts
```

**Purpose:** side-by-side comparison to spot anything present in the
checkout but missing from the deployed copy -- run this any time a
`ModuleNotFoundError`/`FileNotFoundError` shows up on the VPS after a
`git pull`, before assuming the code itself is wrong.

---

### Confirm session env vars are actually set in the current window

```powershell
echo $env:MACHINE_TOKEN
echo $env:CREDENTIAL_API_URL
echo $env:PROVISIONING_PUBLIC_HOST
echo $env:FIREWALL_REMOTE_IP
```

**Purpose:** these are session-scoped (`$env:`, not
`[Environment]::SetEnvironmentVariable(...,"User")` -- deliberately,
see the multi-account token-isolation reasoning elsewhere in this
project) -- gone the moment you open a fresh terminal window. A blank
line back from any of these means it's unset in *this* window, even if
you set it correctly in a different one earlier. Check this before
assuming a fresh 401/`KeyError` means the code or the token itself is
wrong.

---

### Check (and clear) a permanent, User-scope environment variable

```powershell
[Environment]::GetEnvironmentVariable("BRIDGE_TOKEN", "User")
[Environment]::SetEnvironmentVariable("BRIDGE_TOKEN", $null, "User")
```

**Purpose:** unlike `$env:` (session-only), these persist in the
Windows registry across terminal windows and reboots -- which is
exactly why they're the WRONG place for a per-account secret like
`BRIDGE_TOKEN`: only one value can exist per variable name at this
scope, so a second account's token would silently overwrite the
first's the moment it's set this way. `GetEnvironmentVariable`
returning blank confirms it's unset; use `$null` (not an empty string)
to actually delete a previously-set one, not just blank it out.

---

### Find where NSSM actually is, if it's not on PATH

```powershell
Get-ChildItem C:\nssm -Recurse -Filter nssm.exe
```

**Purpose:** `nssm` typing as a bare command fails
(`CommandNotFoundException`) if it was downloaded manually rather than
installed via choco/winget (which add it to PATH automatically) -- this
finds the real full path (e.g. `C:\nssm\nssm.exe`) so every other
`nssm.exe` command in this file actually resolves.

---

### Safely stop a specific process by PID

```powershell
Stop-Process -Id <pid> -Force
```

**Purpose:** always get the PID from a command that shows the exact
path first (`Get-Process terminal64 | Select Id, Path` or
`netstat -ano | findstr :<port>`'s last column) -- never stop a
process by name alone when multiple instances of the same executable
can be running (every MT5 account's terminal shares the name
`terminal64.exe`).

---

### Watch for a short-lived process before it disappears

```powershell
1..25 | ForEach-Object { Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object Id, Path; Start-Sleep -Seconds 1 }
```

**Purpose:** catches a process that starts and exits on its own within
seconds -- a single `Get-Process` check after the fact can easily miss
it entirely and wrongly conclude "it never launched." This is exactly
how a wrong MT5 server name was diagnosed: the terminal DID launch and
ran for ~13 seconds before exiting on its own, which only this kind of
repeated polling would catch.

---

### Check Windows' own record of an NSSM service failure

```powershell
Get-EventLog -LogName Application -Source nssm -Newest 10 | Format-List
```

**Purpose:** fallback diagnostic for when a service's `AppStdout`/
`AppStderr` haven't been configured yet (check with
`nssm get <service> AppStdout` first) -- NSSM logs service-level
start/stop/crash events to the Windows Application event log
independently of whatever the wrapped program itself logs.

---

### Manually launch MT5 directly, bypassing the poller entirely

```powershell
C:\MT5-<label>\terminal64.exe /portable /login:<login> /password:<password> /server:<server>
```

**Purpose:** the fastest way to see MT5's own real-time behavior with
your own eyes (a login-error dialog, a EULA prompt, or nothing at all)
when a poller run's `IPC timeout`/`Authorization failed` message alone
isn't enough to diagnose. Needs the folder to actually exist first --
it gets deleted by cleanup after every failed attempt, so either catch
it mid-run or let a fresh attempt create it before running this.