# MT5 Bridge — Phase 2

Read-only FastAPI worker that sits between the (future) Linux app and a
single MT5 terminal on this Windows VPS. **No order functions exist in this
codebase** — that's Phase 4, gated behind shadow mode (Phase 3).

## Architecture recap

One Python process = one MT5 terminal connection = one account = one port.
`mt5.initialize()` can't hold two connections in one process, so multi-user
support is N of these worker processes side by side, not one router:

```
C:\bridge\                  <- this codebase, shared
C:\bridge\config.json       <- Tony's account, port 8001   (this session)
C:\bridge\accounts\friend\config.json   <- friend's account, port 8002 (later)
```

Each config file points at its own MT5 terminal path (own `C:\MT5-<name>\`
portable install) and its own port. Same `app/` code runs both — nothing in
`app/` hardcodes an account.

## Setup on the VPS

```powershell
cd C:\bridge
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy config.example.json config.json
notepad config.json   REM fill in real login/password/paths
```

`config.json` holds plaintext credentials for v1 — this file must never be
committed. Add `config.json` (but not `config.example.json`) to `.gitignore`
if this directory ever goes in the repo. The Postgres-encrypted-credential
flow from Phase 0 is deferred until the Linux app is ready to drive this
bridge; right now the bridge is a standalone local service.

## Run

```powershell
cd C:\bridge
venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
```

`--workers 1` is not optional — see architecture note above.

For a second account later: copy this whole `C:\bridge` layout (or just
point `BRIDGE_CONFIG_PATH` at a second config file with a different `port`),
and run a second `uvicorn` command bound to that port.

## Endpoints (all read-only)

| Endpoint | Purpose |
|---|---|
| `GET /health` | connection + trade_allowed status |
| `GET /account_info` | balance, equity, margin, leverage |
| `GET /tick?symbol=EURUSDm` | latest bid/ask/last |
| `GET /candles?symbol=EURUSDm&timeframe=M5&count=100` | recent OHLCV bars |

Every timestamp is returned twice: `time_utc` and `time_ny` (both ISO 8601,
tz-aware). NY offset is computed at request time via
`zoneinfo("America/New_York")` — never a hardcoded constant — so it tracks
DST automatically (rulebook §33.3). MT5 server time is GMT+0, confirmed
twice against the live chart this session, so the epoch value from MT5 is
treated as a direct UTC instant before converting to NY.

## Verify after starting

```powershell
curl http://localhost:8001/health
curl http://localhost:8001/account_info
curl http://localhost:8001/tick?symbol=EURUSDm
curl "http://localhost:8001/candles?symbol=EURUSDm&timeframe=M5&count=5"
```

`/health` should report `connected: true` against demo account 476123801 on
Exness-MT5Trial9. If it doesn't, check that MT5 (`C:\MT5-Tony`, launched
`/portable`) is actually running and logged in — this bridge does not launch
the terminal for you, only connects to an already-running one.

## Scope boundary (Phase 2)

In scope: candles, tick, account_info, health — data only.
Explicitly out of scope until Phase 3/4: `order_send`, `order_check`,
position management, any function that could place, modify, or close a
trade. Nothing in this repo does that; keep it that way until shadow mode
(Phase 3) is built and reviewed.