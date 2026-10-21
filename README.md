# The Bot

A multi-user trading platform built around one idea: a single shared
detection engine finds a setup once per model, and that same trade
fires across every user's own broker account that has that model
enabled, sized to their own balance. Users opt in/out per model, not
by running their own separate bot instance.

**Status**: the FVG model has been trading real money live since
2026-08-28. A second model, Cascade Raid, was built and validated
against real historical data but has since been deprecated and is
being replaced from scratch — don't build on `cascade_raid/`.

Stack: Python + FastAPI + SQLAlchemy + Alembic + Postgres on the
backend, React (Vite) on the frontend, `MetaTrader5`'s official Python
package for broker connectivity (the reason this is Python end-to-end
rather than Node — no supported Node equivalent exists).

## Architecture

```
app/              -- the main API: auth, broker credential onboarding,
                     model configs, trades, events, settings, admin
frontend/         -- React app: trader-facing pages + an /admin section
phase1/streaming/ -- the FVG model's detection engine (pure: bar in,
                     event out -- no DB or bridge knowledge at all)
shadow_runner/    -- the live process: polls bars, runs them through a
                     model, fans results out to every active
                     subscriber's own order-execution handler, journals
                     every event/trade to Postgres
bridge/           -- a SEPARATE FastAPI service that runs on a Windows
                     VPS next to a real MT5 terminal, wrapping the
                     MetaTrader5 package; one process = one MT5
                     connection = one account = one port
cascade_raid/     -- DEPRECATED, being replaced -- do not extend
alembic/          -- migrations
tests/            -- pytest suite
```

Each of `bridge/`, `phase1/`, and `frontend/` has its own README with
component-specific detail -- this file stays at the system level.

### How a trade actually happens

1. `shadow_runner` polls its reference bridge for newly-closed bars
   (plain HTTP GET on a fixed interval -- not push/streaming) and feeds
   them into the active model's detection engine (`phase1/streaming/`
   for FVG), bar by bar.
2. When the engine emits a `trade_candidate_ready` event, `shadow_runner`
   looks up everyone currently subscribed to that model (checked fresh
   every time, never cached) and fans the event out to each
   subscriber's own `OrderManager` -- each isolated in its own
   try/except, so one subscriber's broken bridge connection can't block
   execution for anyone else.
3. Each subscriber's `OrderManager` places a real pending order on
   their own MT5 bridge, sized from their own account balance and
   `risk_pct`. Fills/closes are confirmed by polling the broker's own
   position/order state -- never inferred from price.
4. Everything (detection milestones, order placement, fills, closes,
   safety-check failures) is journaled to the `events`/`trades` tables
   for the trader-facing and admin UIs to read back.

### Broker connectivity

MT5 only allows one connection per process, so the `bridge/` service
runs one instance per connected account on a Windows VPS, each its own
port. Connecting a new account is self-service (`bridge/scripts/provisioning_poller/`
drives the actual Windows-side setup) -- a user submits credentials in
the app, and a background poller on the Windows box provisions a
dedicated MT5 terminal + bridge worker for them, with no manual admin
step. Real order placement is additionally gated behind
`orders_enabled` in each bridge worker's own `config.json` -- a
filesystem-only, intentionally non-network-reachable kill switch, not
something togglable from the UI.

## Running it locally

1. Create a Postgres database and user, or just use the bundled
   `docker-compose.yml` (brings up `db`, `api`, `shadow_runner`,
   `frontend`, and a Caddy reverse proxy).
2. Copy `.env.example` to `.env` and fill in real values -- at minimum:
   - `DATABASE_URL`, `JWT_SECRET_KEY`, `CREDENTIALS_ENCRYPTION_KEY`
     (the last one encrypts broker credentials at rest -- generate with
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
   - `BRIDGE_URL` / `SHADOW_RUNNER_USER_ID` -- which bridge
     `shadow_runner` uses as its detection price feed, and which real
     user it journals events against. See `DEDICATED_REFERENCE_ACCOUNT.md`
     for why the price feed is a dedicated, order-less reference
     account rather than tied to any one trader's real account.
   - Telegram/healthchecks.io vars are optional -- everything no-ops
     safely if left unset.
3. Install dependencies: `pip install -r requirements.txt`
4. Run migrations: `alembic upgrade head`
5. Start the API: `uvicorn app.main:app --reload`
   (or `docker compose up -d` for the full stack)
6. Verify: `curl http://localhost:8000/health`

The `bridge/` service is a separate deployment target entirely -- it
runs on a Windows VPS next to real MT5 terminals, not as part of this
compose file. See `bridge/README.md`.

## Testing

`pytest` (config in `pytest.ini`, `testpaths = tests`). Scripts meant
to be run directly (backfills, one-off reconciliation jobs) follow a
"extract the core logic into a pure, testable function, keep `main()`
as a thin wrapper" convention throughout -- see
`shadow_runner/scripts/` for examples.

## Where the rest of the history lives

This repo accumulates a lot of dated, working-notes-style markdown at
the root as real incidents/builds happen (deploy diagnoses, secret-handling
notes, phase validation writeups, etc.) -- most are point-in-time and not
meant to stay authoritative forever. The two most load-bearing for
understanding current state:

- `PENDING_ITEMS.md` -- open/deferred work, organized by area.
- `HANDOFF.md` -- design history and decisions, for context on *why*
  things are shaped the way they are.

## Deliberately out of scope here

Anything broker-credential-bearing never belongs in this README (or
anywhere in chat/logs) -- see `SECRET_HANDLING_DISCIPLINE.md` for the
project's actual discipline on this.
