# MT5 Bridge

A FastAPI worker that sits between a single MT5 terminal (on a Windows
VPS) and the main app. **This is a live, order-placing service** —
real orders have gone to real money accounts since 2026-08-28. The
original "read-only, Phase 2" framing is out of date; keep that history
in mind if you find it referenced elsewhere in this repo's older notes.

## Architecture

One Python process = one MT5 terminal connection = one account = one
port. `mt5.initialize()` can't hold two connections in one process, so
multi-account support is N of these worker processes side by side, one
per connected account, not a single router handling all of them:

```
C:\bridge\                                     <- full the_bot monorepo clone
C:\bridge\bridge\                              <- THIS codebase, shared (BridgeRoot)
C:\bridge\bridge\accounts\<label>\config.json  <- one account's config
C:\MT5-<label>\                                <- that account's own portable MT5 install
```

The nesting is deliberate, not an accident: the Windows VPS clones the
whole monorepo (one canonical checkout, set up by
`bridge/scripts/setup_bridge_host.ps1`), and every bridge-specific path
(venv, this service's own working directory, the poller's `BRIDGE_ROOT`)
points at the `bridge\` subfolder within it. Two *separate* checkouts
drifting out of sync with each other was a real, previously-hit bug
(see `HANDOFF.md`'s "Bridge sync-gap fix") -- one checkout avoids that
by construction.

Same `app/` code runs every worker — nothing in `app/` hardcodes a
specific account. What makes one worker different from another is
purely its own `config.json` + port.

### Credentials never live on disk here

`config.json` holds only local, non-secret fields (`account_label`,
`mt5_terminal_path`, `port`, `default_symbol`, `orders_enabled`,
`magic_number`/`magic_numbers`) — **no login, password, or server**.
Those are fetched once at worker startup from the main app's
`GET /internal/bridge-credentials`, authenticated by a `BRIDGE_TOKEN`
env var (a per-account, rotatable capability token — not the MT5
password itself). The fetched password is held in memory only, for
that process's lifetime, and never written to disk. See
`bridge/app/config.py`'s `fetch_credential()`.

```powershell
$env:BRIDGE_TOKEN = "<minted via POST /broker-credentials/{id}/bridge-token>"
$env:CREDENTIAL_API_URL = "https://api.ihusale.com.ng"
```

`CREDENTIAL_API_URL` must be `https://` — this call sends the token and
receives the plaintext password over the wire.

### The `orders_enabled` kill switch

Real order placement (`/orders`, `/orders/pending`, `/positions/{ticket}/close`,
etc.) is gated behind `orders_enabled` in that worker's own
`config.json` — defaults to `false`, so a config file copied verbatim
to a new account never accidentally enables trading by omission. This
is a filesystem-only switch: there is no API or UI path that can flip
it. Setting it `true` and restarting the worker is a deliberate, manual,
watched step every time — see `app/config.py`'s own field description
for the full reasoning.

## Setting up a fresh VPS as a bridge host

`scripts/setup_bridge_host.ps1` is the whole host bootstrap in one
idempotent script -- repo checkout, Python venv, and registering the
provisioning poller (below) as a real Scheduled Task. It deliberately
does NOT provision any MT5 account itself; that's the self-service flow
in the next section. See the script's own header comment for exactly
what it does and does not automate (auto-logon and the base MT5
template both stay manual, on purpose). Written for the 2026-10 full
VPS rebuild -- running it is now the documented way to set this up,
replacing hand-typed commands from `OPS_COMMANDS.md`/`HANDOFF.md`.

## Getting an account connected: self-service provisioning (the real flow)

`scripts/provisioning_poller/` is a persistent process on the Windows
box (runs as a Scheduled Task tied to an interactive logon — not an
NSSM service, due to Session 0 isolation preventing a service-context
process from launching a visible MT5 terminal) that polls the main
app's `/internal/provisioning-jobs/*` endpoints for pending signups and
does everything end to end: copies a fresh portable MT5 install,
launches and logs it in, verifies the login, writes `config.json`,
installs that account's own NSSM service (`bridge-<label>`), and opens
its firewall port. A user submitting broker credentials in the app UI
is enough to trigger this — no manual admin step. See
`scripts/provisioning_poller/main.py`'s own docstring for how it runs,
and `scripts/provisioning_poller/provisioner.py` for the actual
provisioning steps.

Decommissioning (removing an account) works the same way, in reverse —
triggered by the "Remove" action in the app's Broker Credentials UI.

### Manual fallback

`scripts/provision_account.ps1` does the same thing by hand, for
debugging the poller itself or a one-off account that needs manual
attention — see its own header comment for exactly what it does and
does NOT automate (starting the worker persistently and flipping
`orders_enabled` both stay manual, watched steps either way):

```powershell
cd C:\bridge\scripts
.\provision_account.ps1 -AccountLabel friend -Login 12345678 `
    -Password "the-real-password" -Server "Exness-MT5Trial9" `
    -Port 8002 -CredentialId "3fa85f64-5717-4562-b3fc-2c963f66afa6" `
    -Jwt "eyJhbGciOi..."
```

## Endpoints

Read-only (data):

| Endpoint | Purpose |
|---|---|
| `GET /health` | connection + trade_allowed status |
| `GET /account_info` | balance, equity, margin, leverage |
| `GET /tick?symbol=EURUSDm` | latest bid/ask/last |
| `GET /candles?symbol=...&timeframe=...&count=...` | OHLCV bars (`start_pos` param pages further back than one 5000-bar call reaches) |
| `GET /symbol_info?symbol=...` | contract size, tick size/value, digits, volume min/max/step |
| `GET /history/position/{ticket}` | a specific position's close details |
| `GET /history/deals?date_from=...&date_to=...` | raw broker deal history for a date range |

Order-placing (all gated behind `orders_enabled`, 403 otherwise):

| Endpoint | Purpose |
|---|---|
| `POST /orders` | market order |
| `GET /positions` | open positions (optionally filtered to this worker's own magic numbers) |
| `POST /positions/{ticket}/close` | close a position |
| `POST /positions/{ticket}/close_partial` | partial close |
| `POST /orders/pending` | place a pending limit order (this bridge only ever places limit orders, never stop orders — see `mt5_client.py`'s module note) |
| `GET /orders/pending` | list pending orders |
| `DELETE /orders/pending/{ticket}` | cancel a pending order |
| `POST /positions/{ticket}/modify` | change stop-loss/take-profit on an open position |

Every timestamp is returned twice: `time_utc` and `time_ny` (both ISO
8601, tz-aware). NY offset is computed at request time via
`zoneinfo("America/New_York")` — never a hardcoded constant — so it
tracks DST automatically. MT5 server time is GMT+0, so the epoch value
from MT5 is treated as a direct UTC instant before converting to NY.

## Run

```powershell
cd C:\bridge\bridge
venv\Scripts\activate
$env:BRIDGE_TOKEN = "..."
$env:CREDENTIAL_API_URL = "https://api.ihusale.com.ng"
uvicorn app.main:app --host 0.0.0.0 --port <this-account's-port> --workers 1
```

`--workers 1` is not optional — see the architecture note above. In
practice this always runs as its own NSSM service (`bridge-<label>`),
not a foreground `uvicorn` process, so it survives independently of any
one login session.

## Verify after starting

```powershell
curl http://localhost:<port>/health
curl http://localhost:<port>/account_info
curl "http://localhost:<port>/candles?symbol=EURUSDm&timeframe=M5&count=5"
```

`/health` should report `connected: true`. If it doesn't, check that
the account's own MT5 terminal (`C:\MT5-<label>`, launched `/portable`)
is actually running and logged in — this bridge does not launch the
terminal for you, only connects to an already-running one.
