# Phase 2 Validation — MT5 Bridge (Read-Only Data Adapter)

## Scope

Phase 2 builds the read-only bridge between the live/demo MT5 terminal and
the rest of the system: a FastAPI worker running on the Windows VPS,
one process per account, exposing `/health`, `/account_info`, `/tick`, and
`/candles`. No order functions exist anywhere in this codebase — that's
scoped to Phase 4, gated behind Phase 3 shadow mode.

## Infrastructure

- **VPS**: Database Mart Express, 2 cores / 6GB RAM / 60GB SSD, Windows
  Server 2022, RDP via **dedicated IP** (upgraded from shared IP during
  Phase 3 networking work): `38.247.137.208:1097` (not 3389). Shared IP
  (`38.247.142.117:10010`) is retired as of this upgrade -- the bridge's
  externally-reachable address changed accordingly, see Phase 3 addendum
  below.
- **MT5 terminal**: Exness build 6090, installed portably at `C:\MT5-Tony`,
  launched via a `/portable` shortcut
- **Account**: Exness demo 476123801 on server `Exness-MT5Trial9`. Original
  wizard-issued credentials were invalid; fixed via a password reset in the
  Exness Personal Area
- **Runtime**: Python 3.14.6 + `MetaTrader5` package 5.0.6090 (version
  matched to the terminal build)
- **Symbol**: EURUSDm
- **Bridge location**: `C:\bridge` on the VPS, run via `uvicorn
  app.main:app --host 0.0.0.0 --port 8001 --workers 1`

## Architecture decisions

- **One process per account.** `mt5.initialize()` holds exactly one
  connection per OS process — there is no way to multiplex two accounts
  through one Python process. Multi-user support is N of these worker
  processes side by side (each its own config file, its own port), not one
  router. Confirmed and designed around from the start of this phase.
- **Credentials**: local JSON config file per worker (`C:\bridge\config.json`),
  path overridable via `BRIDGE_CONFIG_PATH`. The Postgres-encrypted-credential
  flow built in Phase 0 (Fernet, `broker_credentials` table) is deferred until
  the Linux app is ready to drive this bridge — v1 is a standalone local
  service.
- **Second account (friend's) deferred** — credentials unavailable this
  session. Adding it later is just a second config file + a second port
  against the same codebase, no code changes required.
- **Timezone normalization (rulebook §33.3)**: MT5 server confirmed twice
  (chart + `copy_rates` timestamp) to run at GMT+0. Every timestamp returned
  by the bridge is converted at request time via
  `zoneinfo("America/New_York")` — never a hardcoded offset — so it tracks
  DST automatically. Every candle/tick response carries both `time_utc` and
  `time_ny`.

## Real bugs found and fixed getting this running

Three genuine issues surfaced during setup, none of them in the original
design — all environment/runtime problems specific to this VPS:

1. **`pydantic-core` failed to build from source.** The VPS runs Python
   3.14, which is new enough that `pydantic-core==2.9.2`'s prebuilt wheel
   doesn't exist yet for `cp314-win_amd64`. Pip fell back to compiling via
   Rust/maturin, which then failed because there's no MSVC linker
   (`link.exe`) installed. Fixed by loosening the pin to `pydantic>=2.10` in
   `requirements.txt`, which resolved to `pydantic-core 2.46.4` — a version
   that does ship a `cp314` wheel, no compilation needed.

2. **`zoneinfo.ZoneInfoNotFoundError: No time zone found with key
   America/New_York`.** Unlike Linux, Windows doesn't ship the IANA tzdata
   database, so Python's stdlib `zoneinfo` had nothing to load from. Fixed
   by adding `tzdata>=2025.1` to `requirements.txt` (pure-Python package
   that bundles the database).

3. **The real one: MT5 calls hung forever on a different thread than
   `initialize()`.** First working config had `mt5.initialize()` called on
   FastAPI's startup thread, but request handlers (`/health` etc.) run on
   threads from FastAPI's default sync threadpool. `curl -v` against
   `/health` showed the TCP handshake completing and the request being
   fully sent — then nothing. No response, no exception, no server-side log
   line, indefinitely. This is thread-affinity in the underlying MT5 IPC
   layer, not a bug in the request path.

   **Fix**: every `mt5.*` call in `mt5_client.py`, including `initialize()`
   itself, now runs through one dedicated single-worker
   `ThreadPoolExecutor`. Public functions (`health()`, `account_info()`,
   `tick()`, `candles()`) submit work to that executor and block on
   `future.result(timeout=15)` — so no matter which thread FastAPI
   dispatches a request to, the actual MT5 call always happens on the one
   thread that owns the connection. The 15s timeout also means a
   regression here now surfaces as a clear `TimeoutError` instead of
   another silent infinite hang.

## Known benign noise

Uvicorn on Windows occasionally logs an `asyncio` traceback —
`OSError: [WinError 64] The specified network name is no longer
available` — from the Proactor event loop's socket-accept handler when a
client connection resets or there's a brief network blip (observed once
during an RDP session, no apparent cause). This does not crash the server;
uvicorn keeps accepting new connections after logging it. Cosmetic fix
available later (switch to the Selector event loop on Windows) but not a
blocker — noted here so it isn't mistaken for a real failure in a future
session.

## Verification — all four endpoints, live against the real account

Run Sunday Aug 2, 2026, market closed (last tick/candles reflect Friday
July 31 close, ~16:55–16:58 NY time — expected weekend behavior, not a
bug):

- **`GET /health`** → `{"status":"ok","account_label":"tony","login":476123801,"connected":true,"trade_allowed":true,"detail":null}`
- **`GET /account_info`** → `{"login":476123801,"server":"Exness-MT5Trial9","balance":50000.0,"equity":50000.0,"margin":0.0,"margin_free":50000.0,"margin_level":null,"leverage":1000,"currency":"USD"}` — `margin_level` correctly `null` when `margin` is 0 (divide-by-zero guard working as designed)
- **`GET /tick?symbol=EURUSDm`** → bid/ask returned with `time_utc: 2026-07-31T20:58:59+00:00` / `time_ny: 2026-07-31T16:58:59-04:00`. **4-hour offset confirmed correct for late July (EDT/daylight saving active)** — validates the zoneinfo conversion is live, not a hardcoded guess.
- **`GET /candles?symbol=EURUSDm&timeframe=M5&count=5`** → 5 bars returned, OHLC + tick_volume + spread + real_volume all populated, same correct dual-timestamp pattern on every bar.

## Phase 2 status: complete

Bridge is live, connected, and verified against the real demo account on
all four in-scope endpoints. No order functions present anywhere in the
codebase. Ready for Phase 3 (shadow mode) to build on top of this as a
data source.

## Carried-forward gotchas for the next worker (friend's account)

When standing up the second account's worker, expect to hit — and can
skip straight past — the same three environment issues above, since
`requirements.txt` now already pins the fixed versions
(`pydantic>=2.10`, `tzdata>=2025.1`). The thread-affinity fix is in
`mt5_client.py` itself, so it's automatically inherited — just point a new
`config.json` at the friend's credentials/terminal path/port and run a
second `uvicorn` process.

## Addendum — external connectivity, added during Phase 3 networking work

Phase 2 validation above was entirely `localhost`/RDP-session-based; the
bridge had never been tested from outside the VPS itself. Phase 3 needed
this, since the shadow runner (Hetzner) has to reach the bridge (VPS) over
the public internet. Two real infrastructure issues surfaced getting there:

- **The VPS originally sat behind Database Mart's shared IP with NAT
  port-forwarding** (`38.247.142.117:10010`, RDP only). Only the RDP port
  was forwarded — port 8001 had no route in from outside at all, so
  external requests hung indefinitely (TCP connects at the OS level never
  even happen; nothing to do with Windows Firewall). Fixed by purchasing a
  **dedicated IP** from Database Mart (~$2/mo add-on, ~1hr provisioning
  review). New address: `38.247.137.208`, new RDP port `1097` (port
  changed along with the IP -- confirm current value in the Database Mart
  panel if this drifts further). With a dedicated IP, any port opened in
  Windows Firewall is directly reachable -- no more per-port forwarding
  requests needed for future ports.
- **Windows Firewall needed an explicit inbound rule** for 8001, scoped to
  the Hetzner box's IP specifically (not open to the world, since the
  bridge has no authentication layer yet -- see open item below):
  ```
  netsh advfirewall firewall add rule name="MT5 Bridge from Hetzner" ^
    dir=in action=allow protocol=TCP localport=8001 remoteip=<HETZNER_IP>
  ```

**Verified**: `curl http://38.247.137.208:8001/health` from the Hetzner
box returns a live, correct response (`connected: true`, real account
data) -- external connectivity confirmed end-to-end.

**Open item, deliberately deferred**: the bridge still has zero
authentication -- reachability is currently gated only by the Windows
Firewall IP restriction (Hetzner's IP specifically), not by anything the
bridge itself checks. Acceptable for the current single-consumer,
known-IP setup; revisit (e.g. a shared-secret header) before this
expands beyond a fully trusted, fixed set of callers.
