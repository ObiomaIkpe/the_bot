# Admin frontend: React/TypeScript UI + real backend API

## Context

The current admin tool (`admin_dashboard/`) is a read-only, single-operator
Streamlit app that reads the whole database unscoped. The goal is to
replace this with a proper multi-user product surface: users log in, see
their own data, flip switches (pause a model, change its status), and take
direct live actions (close a position, cancel a pending order) — all from a
TypeScript/React frontend.

This requires building a real API first: today `app/main.py` only wires up
`/auth/*` (register/login/me, already working, JWT-based) and `/health`.
There are zero routers for reading trades/events/model configs, and zero for
writing anything. The frontend is pure greenfield (confirmed: no
`package.json`/`.tsx`/frontend directory anywhere in the repo).

**Non-negotiable architecture rule:** the browser never talks to the
Windows bridge directly. The bridge (`bridge/app/main.py`) has no auth at
all — it's reachable only because Windows Firewall whitelists the Hetzner
box's IP specifically. All bridge calls happen server-side, from the `api`
FastAPI service (which already lives on the same trusted Hetzner box as
`shadow_runner`, so it's the same trust boundary that already exists). Flow
is always: `browser → FastAPI (Hetzner) → bridge (Windows), when truly needed`.

Two kinds of "switches" (both wanted in v1):
1. **DB-flag switches** (`user_settings.is_paused`, `model_configs.status`)
   — already safe by construction: `shadow_runner/order_manager.py` polls
   these fresh from Postgres before every real action. Writing the DB row
   is enough; no bridge call needed.
2. **Direct live actions** (close an open position, cancel a pending order)
   — these DO need the API to call the bridge synchronously. Higher risk,
   needs an ownership check so a user can only touch their own positions.

## Ownership model for live actions

`bridge/app/models.py`'s `Position` and `PendingOrder` schemas both include
a `magic: int` field, and `model_configs.magic_number` is globally unique
across the whole system (by design — see `app/models/model_config.py`).
So: fetch a user's own magic numbers from their `ModelConfig` rows, fetch
positions/pending orders from the bridge (`only_ours=False` — the bridge's
own `only_ours` flag is server-side and resolves to its *own* single
hardcoded magic, not caller-supplied, per the bug fixed in
`prove_trade_lifecycle.py`'s commit), then filter client-side by matching
`magic` against the user's own set. This is the same technique
`prove_trade_lifecycle.py` already had to work around — reuse it, don't
reinvent it.

## Backend plan

### New config (`app/core/config.py`)
Add to `Settings`:
- `cors_allowed_origins: str` (comma-separated, parsed into a list) — no
  CORS middleware exists at all today (`app/main.py` has none).
- `bridge_url: str` — currently only `shadow_runner/config.py` reads
  `BRIDGE_URL` directly from `os.environ`; the `api` service needs its own
  path to it for the live-action routes.

### New schemas (follow `app/schemas/auth.py`'s convention: plain
`BaseModel`, `from_attributes = True` for DB-backed responses)
- `app/schemas/events.py` — `EventOut`
- `app/schemas/trades.py` — `TradeOut`
- `app/schemas/model_configs.py` — `ModelConfigOut`, `ModelConfigUpdate` (status only, validated against `VALID_MODEL_STATUSES`)
- `app/schemas/settings.py` — `UserSettingsOut`, `UserSettingsUpdate` (is_paused, max_daily_loss_pct, news_filters)
- `app/schemas/trading.py` — `PositionOut`, `PendingOrderOut`, `CloseResult`, `CancelResult`

### New routers, all requiring `Depends(get_current_user)` and filtering
every query by `current_user.user_id` (this is the key difference from
`admin_dashboard`, which reads everything unscoped for a single trusted
operator)
- `app/routers/events.py` — `GET /events` (model/since/limit filters), reuse the query shape from `admin_dashboard/queries.py::get_recent_events` but scoped to `current_user.user_id`.
- `app/routers/trades.py` — `GET /trades` (model/is_shadow/outcome/days_back filters), same pattern from `get_trades`.
- `app/routers/model_configs.py` — `GET /model-configs`, `PATCH /model-configs/{config_id}` (status only; 404 if the row isn't the current user's).
- `app/routers/settings.py` — `GET /settings`, `PATCH /settings` (is_paused toggle + other fields).
- `app/routers/trading.py` — the live-action router:
  - `GET /trading/positions`, `GET /trading/pending-orders` — call `shadow_runner.bridge_client.BridgeClient` (reuse as-is, construct with `settings.bridge_url`), filter by the ownership check above.
  - `POST /trading/positions/{ticket}/close` — ownership check, then `BridgeClient.close_position(ticket)` (bridge itself still enforces its own `orders_enabled` gate — propagate a 403 from the bridge as a 409 to the frontend with a clear message).
  - `DELETE /trading/pending-orders/{order_ticket}` — same pattern, `BridgeClient.cancel_pending_order(...)`.

Every write endpoint (`PATCH model-configs`, `PATCH settings`, both
`trading` actions) journals an `Event` via
`shadow_runner.persistence.write_event(db, event, user_id, model)` (already
a plain-`Session` function, works fine from a FastAPI request too) so every
manual action is auditable the same way real broker actions already are.
Add two new event types to `app/models/event.py`'s `VALID_EVENT_TYPES` (a
plain Python tuple, not a DB constraint — no migration needed) and to
`REAL_ACTION_EVENT_TYPES`:
- `"manual_close_requested"`
- `"manual_cancel_requested"`

When a manual close actually completes, also worth setting
`Trade.real_close_reason = 'manual'` — that value is already anticipated in
the schema's own comment (`app/models/trade.py` line 66) but nothing sets
it today.

### `app/main.py`
Add `CORSMiddleware` (origins from `settings.cors_allowed_origins`), and
`app.include_router(...)` for all five new routers.

### No Alembic migration needed
Every column/table this touches already exists
(`user_settings.is_paused`, `model_configs.status`, `trades.real_close_reason`).
`VALID_EVENT_TYPES` additions are app-level only. If that changes during
implementation, the next migration number is `0006_...`.

## Frontend plan (`frontend/`, new top-level folder — same-repo pattern as `admin_dashboard/`)

- **Stack:** Vite + React + TypeScript. Add `@tanstack/react-query` for
  data fetching/caching/polling (replaces Streamlit's meta-refresh-tag
  auto-refresh with real interval refetching for the event feed and the
  live positions panel).
- **Auth:** a small `AuthContext` — POST to `/auth/login`
  (`OAuth2PasswordRequestForm` shape: `username`=email, `password`), store
  the JWT (localStorage, since there's no refresh-token flow — access
  token expires in 60 min per `jwt_access_token_expire_minutes`; on
  expiry/401 from any API call, redirect to `/login`). A typed `apiClient`
  wrapper attaches `Authorization: Bearer <token>` to every request.
- **Pages:**
  - `Login` — email/password form, calls `/auth/login`.
  - `Dashboard` — event feed table + trades table, same filters as
    `admin_dashboard/app.py`'s tabs, scoped to the logged-in user
    automatically (no user/model picker needed beyond model, since the API
    already scopes by JWT).
  - `Settings` — per-model status switches (disabled/shadow/active
    dropdown) and the account-wide pause toggle, each firing the
    corresponding `PATCH`.
  - `Live` — positions + pending orders tables from `/trading/*`, with
    Close/Cancel buttons. **Require a confirmation modal before firing**
    (these are real, irreversible broker actions) — no bare-click destructive
    buttons.

## Verification

- **Backend:** run `uvicorn app.main:app --reload` locally against the dev
  Postgres; `curl`/httpie through login → token → each new GET endpoint,
  confirm scoping (create a second test user, confirm they can't see or
  patch the first user's rows → expect 404, not data leakage). Add pytest
  coverage under `tests/app/` following the existing style (real Postgres +
  `db_session` fixture, per `tests/conftest.py`) for: per-user scoping on
  every GET, 404 on cross-user PATCH/close/cancel, and the
  `VALID_EVENT_TYPES`/journaling side effect of each write endpoint.
- **Frontend:** `npm run dev`, log in against the local backend, confirm
  the dashboard renders real rows, confirm a settings toggle round-trips
  (PATCH → row changes in Postgres → UI reflects it on next refetch).
- **Live actions — explicit safety note:** do not test
  close/cancel against the real Hetzner+bridge+live-account stack without
  direct, in-the-moment confirmation first — these are real money actions
  on a real broker connection, unlike everything else in this plan which
  only touches the app's own Postgres data.

## Addendum: pause decentralized during M2 (2026-08-26)

While building `PATCH /settings`, hit the "settings audit event" snag
described above for real, and it surfaced a bigger question: `is_paused`
was originally *only* account-wide (`UserSettings.is_paused`). Decided to
decentralize it rather than route around the snag:

- **Kept** `UserSettings.is_paused` exactly as it was — the account-wide
  emergency stop, checked fresh on every trade candidate
  (`order_manager.py`'s `_is_user_paused()`). This stays because a single
  action that guarantees "stop everything for this user right now" is a
  real safety property worth keeping, especially under pressure.
- **Added** `ModelConfig.is_paused` (migration `0007`) — a second, finer
  layer for pausing just one model. Checked fresh via a new
  `_is_model_paused()`, same fail-toward-not-paused discipline as the
  account-level check. Either one being true blocks that model's real
  order placement; the account-level check runs first and short-circuits
  (see `test_account_pause_short_circuits_before_the_model_level_check`).
- Resolved the original audit-event snag as originally recommended:
  `PATCH /settings` fans out one `account_settings_updated` event per
  `model_config` the user has (accurate — an account-wide change really
  does affect every one of them); `PATCH /model-configs/{id}` (which now
  also handles `is_paused` per-model) journals a single
  `model_config_updated` event with its own natural `model_name`.

## Addendum: M3.5 -- broker credentials + account info (2026-08-26)

Raised mid-build: the whole app is of limited use without a way for a
user to actually connect their MT5 account and see real account data
(balance, equity). Two real gaps this closed:

1. `app/models/broker_credential.py` had zero API surface -- nothing
   ever wrote to or read from it. Added `app/routers/broker_credentials.py`:
   `POST /broker-credentials` (self-service -- a user submits their own
   MT5 login/password/broker/server/account_type), `GET
   /broker-credentials` (scoped, list), `PATCH /broker-credentials/{id}`
   (sets `bridge_url` and/or `is_active`). The response schema
   (`BrokerCredentialOut`) deliberately never returns
   `account_password`, encrypted or not -- only `account_login` (an
   account number, not secret at the same level) is exposed.
2. M3's trading router assumed a single global `BRIDGE_URL` for
   everyone -- only ever correct for exactly one account. Added
   `bridge_url` to `broker_credentials` (migration `0008`; each MT5
   account already maps to its own bridge worker/port by design -- see
   `bridge/app/config.py`) and rewrote `trading.py`'s
   `get_bridge_client()` to resolve per-user from their own active,
   bridge-connected credential row instead. Removed the now-dead
   `Settings.bridge_url` global entirely. Also added
   `GET /trading/account-info` (balance/equity/margin), proxying the
   bridge's existing `/account_info` endpoint.

**Important limit, not solved by code:** saving credentials and having
a bridge worker actually running for that account are two different
events. MT5 requires a real terminal install on the Windows VPS --
running two accounts simultaneously means two separate portable
installs (`terminal64.exe /portable`, each in its own folder, each with
its own bridge worker process/port/config.json -- the bridge config
schema already anticipated exactly this). No API can provision that
automatically. A `broker_credentials` row with `bridge_url` still null
means "connected in the sense of saved credentials, not yet wired to a
running worker" -- `/trading/*` returns a clear 503 for that case,
distinct from "no credentials at all."

## Suggested build order

Backend first (M1 read routers → M2 DB-flag writes → M3 live bridge
actions → M3.5 broker credentials + account info), verified end-to-end
via curl/pytest before any frontend code is written, since the frontend
can't do anything meaningful without a working, correctly-scoped API
underneath it. Then frontend scaffold → Dashboard → Settings → Live, in
that order.

## Addendum: M4 + M5 -- frontend built (2026-08-26)

Both done together (M5 followed immediately after M4's scaffold, per the
user's "keep going without stopping for permission" instruction while
away). `frontend/` (Vite + React + TypeScript + `@tanstack/react-query`,
`react-router-dom`) now has: `Login` → JWT stored via `AuthContext`,
`ProtectedRoute` gating everything else; `Dashboard` (event feed + trades
table, both auto-scoped by the backend's JWT auth, no picker needed);
`Settings` (per-model status/pause switches via `PATCH /model-configs`,
account-wide pause + max daily loss via `PATCH /settings`); `Live`
(positions/pending orders from `/trading/*`, Close/Cancel gated behind
`ConfirmModal` since these are real, irreversible broker actions).

Verified: `npm run build` (tsc + vite build) and `npm run lint` both
clean. `npm run dev` confirmed serving correctly via curl. **Not
verified**: actual interactive browser use of the login → dashboard →
settings → live flow — no browser tool available in the environment this
was built in. User should click through it themselves before considering
this fully done.

Follow-up, same session: added `src/pages/BrokerCredentials.tsx` --
self-service form to connect an MT5 account (`POST /broker-credentials`)
and mint/re-mint its bridge token, with a warning before re-minting
(invalidates whatever token a running bridge worker currently holds).
Closes the gap noted above -- the paused MT5-credential-cutover steps
(see the plan file) can now be done through this page instead of `curl`.

## Addendum: screens restructuring + design system (2026-08-27)

M1-M5 shipped functional but visually bare (inline styles only, no
shared design system) and organized around the order backend endpoints
happened to get built rather than what a customer actually needs first.
Since this is meant for paying customers, restructured the original 5
pages into 7 and added a design-system foundation. Full plan (context,
rationale, exact scope decisions) at
`~/.claude/plans/misty-seeking-crescent.md`.

- **Design system**: `frontend/src/styles/tokens.css` (dark-mode-first
  CSS custom properties, monospace stack for financial figures) +
  `components.css` (shared classes), consumed by new wrapper components
  (`Card`, `Button`, `Badge`, `StatTile`, `EmptyState`, `Table`,
  `ModelCard`). `Layout.tsx` moved from a flat top-nav to a left sidebar.
- **Overview** (new, replaces `Dashboard` as the default landing page):
  connection health, balance/equity, P&L today/week/all-time, a
  per-model card grid, trimmed recent-activity feed.
- **Models** (new list + `/models/:modelName` detail): per-model
  P&L/win-rate/trade history, computed client-side from the existing
  `GET /trades?model=X` (no backend changes — `Trade.model` and
  `ModelConfig.model_name` already share the same string values).
- **Live**: unchanged data/actions, restyled only.
- **Trade History** (new, split out of the old `Dashboard`): full trades
  table with real filters + client-side column sort.
- **Broker Connection** (`BrokerCredentials.tsx`, reframed): a customer
  with zero connected accounts now sees a prominent first-run connect
  form instead of a buried settings row; `RootRedirect.tsx` sends new
  users here by default instead of Overview.
- **Account Settings** (new, split out of the old `Settings`):
  account-wide pause + max daily loss % only; per-model controls moved
  to Models.

Explicitly out of scope this round: new backend endpoints (connection
health is inferred from the existing 503/502 responses on
`GET /trading/account-info`, same pattern `Live.tsx` already used),
charting library, light/dark toggle, profile/password screen.

Verified: backend baseline unchanged (207 passed / 10 pre-existing
unrelated failures — no backend files touched), frontend `npm run
build`/`npm run lint`/`npm run test` (13 tests) all clean. **Not
verified**: interactive browser click-through — no browser tool
available in this environment.

## Addendum: the four scoped-out gaps, built (2026-08-27)

The screens redesign explicitly called out four things as deliberately
out of scope. Built all four on request. Full plan at
`~/.claude/plans/misty-seeking-crescent.md`.

- **Backend `GET /trading/health`**: wraps the already-existing but
  previously-unexposed `BridgeClient.health()`, reusing
  `get_bridge_client`/the bridge's own `HealthResponse` model exactly
  like `account-info` does. Distinguishes three states account-info's
  503/502 alone couldn't: not configured (503), bridge unreachable
  (502), and bridge reachable but MT5 itself disconnected (200,
  `connected: false`). `Overview.tsx`/`Live.tsx` now use it directly
  instead of inferring health from account-info's error status.
- **`POST /auth/change-password`**: new endpoint on `app/routers/auth.py`,
  reusing the exact `verify_password`/`hash_password` functions
  register/login already use — no new crypto. New `Profile.tsx` page
  (fetches the already-existing `GET /auth/me`) shows account info + a
  password-change form. Known limitation, by design: no session/token
  invalidation on change, no re-verification email.
- **Light/dark theme toggle**: `ThemeContext.tsx` (same shape as
  `AuthContext.tsx`) toggles a `data-theme` attribute + localStorage.
  Because every component already reads semantic Tailwind utilities
  backed by real CSS custom properties, this needed **zero changes to
  any existing component** — just a light-palette override block in
  `index.css` keyed off `[data-theme="light"]`. First-pass color values,
  not visually verified.
- **P&L-over-time chart**: added `recharts` (the one new runtime
  dependency), a `PnlChart.tsx` wrapper themed via CSS vars (so it
  re-themes automatically with the toggle above), wired into Overview
  (all-models) and Model detail (single-model) using trade data already
  fetched on those pages.

Verified: backend 214 passed / same 10 pre-existing unrelated failures
(7 new tests: 3 for `/trading/health`, 4 for change-password). Frontend
`build`/`lint`/`test` (16 tests) all clean; build now warns about bundle
size (656KB vs. the previous 298KB) — expected, from adding a charting
library, not an error. **Not verified**: interactive browser
click-through, especially the light theme's contrast/feel — no browser
tool available in this environment.

## Addendum: auto-provision default models + settings (2026-08-27)

Found two real gaps while reviewing what was left: `ModelConfig` rows
were never created anywhere except by hand (Models page permanently
empty for a new signup), and the identical gap existed for
`UserSettings` (`GET`/`PATCH /settings` 404 forever). Decision: models
stay developer/system-controlled, never customer-created, but all
models are available to every user automatically, scoped per user.
Full plan/rationale at `~/.claude/plans/misty-seeking-crescent.md`.

- `app/core/provisioning.py`: `provision_new_user_defaults()` — creates
  a user's `fvg`/`ob`/`fvg_ob` `ModelConfig` rows (`status="disabled"`,
  never auto-activated) with collision-safe magic-number allocation,
  plus a default `UserSettings` row. Idempotent, so it's shared by both
  `POST /auth/register` (new signups) and
  `app/scripts/backfill_user_defaults.py` (existing users — run once
  locally, confirmed your test account picked up its 3 models +
  settings).
- Required updating 16 existing test call sites across 3 files that
  manually inserted a `ModelConfig`/`UserSettings` row right after
  registering (now collides with the auto-provisioned one), plus
  rewriting 2 tests whose premise — a registered user has zero
  models/settings — is no longer reachable via the public API.

Verified: 217 passed, same 10 pre-existing unrelated failures.

## Addendum: at most one active broker credential per user (2026-08-27)

Second bug from the earlier review, now fixed. `create_broker_credential()`
never set `is_active` explicitly, so every new credential defaulted to
`True` — connecting a second MT5 account left two active rows for the
same user, and `get_bridge_client()`'s `.filter(is_active=True).first()`
(no `ORDER BY`) would arbitrarily pick one. Full plan at
`~/.claude/plans/misty-seeking-crescent.md`.

- Radio-button semantics: `create_broker_credential()`/
  `update_broker_credential()` now deactivate any other active
  credential for that user before activating one — connecting or
  switching accounts "just works," no extra step for the common
  single-account case.
- DB-level backstop: migration `0010` adds a partial unique index
  (`unique=True, postgresql_where="is_active"`) on `(user_id)` — at
  most one active row per user, enforced even if some future code path
  forgets the application-side logic above. Verified drift-free via
  `alembic revision --autogenerate` (only the pre-existing, unrelated
  `user_settings` drift showed up).
- Caught and fixed one incidental regression:
  `test_broker_credential_encryption.py` had an unrelated test creating
  two same-user credentials that both defaulted active — fixed by
  marking one explicitly inactive (irrelevant to what that test
  actually checks).

Verified: 220 passed, same 10 pre-existing unrelated failures. Manual
curl check: connecting a second account correctly deactivates the
first.

## Milestone M6: replace admin_dashboard/ (Streamlit) with a real
## admin section here (2026-08-30)

`admin_dashboard/` was a separate, read-only Streamlit app querying
Postgres directly, unscoped across all users -- deliberately outside
this frontend's own per-user architecture. The user wanted one UI, not
two. Full plan/rationale at `~/.claude/plans/misty-seeking-crescent.md`.

- Added a real `User.is_admin` flag (migration `0016`), gated
  server-side via a new `get_current_admin` dependency
  (`app/core/deps.py`) and a new `app/routers/admin.py` -- the one
  place in the whole API that reads across ALL users on purpose. Five
  endpoints: `/admin/events`, `/admin/safety-checks`, `/admin/trades`
  (+ `/admin/trades/{id}/event-chain`, porting the Streamlit tool's
  fill/close-matching logic server-side), `/admin/audit-log`,
  `/admin/model-configs`. Every admin response adds a `user_email`
  field the Streamlit tool never had -- a genuine improvement, not just
  a port, since the original had no per-user shape to show at all.
- No self-service way to become an admin -- `app/scripts/promote_to_admin.py`,
  run by hand, same "no HTTP endpoint" precedent as minting a
  provisioning machine token.
- React side: a shared `useCurrentUser()` hook (`frontend/src/auth/`),
  a new `AdminRoute` guard (server enforcement is still the real
  boundary; this just keeps a non-admin from seeing the UI at all), a
  conditional "Admin" nav section, and 5 new pages under
  `frontend/src/pages/admin/` mirroring `TradeHistory.tsx`'s existing
  conventions exactly (filters via `URLSearchParams`, `Table`/
  `EmptyState`, a `recharts` bar chart for the safety-check-failure
  counts, same as `PnlChart.tsx`'s one existing chart).
- Deliberately read-only, matching the Streamlit original -- no write
  actions live here; per-user live actions already exist elsewhere in
  this frontend.

Verified: 297 passed (9 new), same 10 pre-existing unrelated failures.
Frontend: `tsc -b && vite build` clean, all 16 existing frontend tests
still pass. Manual end-to-end curl check against a real running server
and the real dev DB: a fresh user gets 403 from every `/admin/*` route,
`promote_to_admin.py` flips `is_admin`, the same user then gets 200
from all five with real data, and `/auth/me` reflects `is_admin` back
correctly. User manually confirmed the "Admin" nav section and pages in
the browser afterward.

`admin_dashboard/` itself (and its `docker-compose.yml` service block)
removed 2026-08-30, per the plan's "port first, delete after" decision.
**Open follow-up**: the actual Streamlit container may still be running
on the VPS -- see `HANDOFF.md` open item 10 for the remaining
`docker compose`/`Caddyfile` teardown there.

## Process

- **Each milestone (M1 → M2 → M3 → M4 → M5) is implemented and verified
  individually, then presented for manual approval before starting the
  next one** — matching this project's own established working style
  (`HANDOFF.md`: "every change isolated and tested individually; never
  bundled"). No milestone starts on the assumption that approving this
  plan document approved all of them at once.
- Every file edit while building this is approved manually, one at a time
  — no auto-accept.
