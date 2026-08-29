# HANDOFF — SMC/ICT Trading Bot: Live Bot Build

**Purpose of this file:** continuity anchor for resuming work in a new
session. Everything here is current as of 2026-08-29 (Phase 3 step 8
done -- ~5 weeks of clean live running; Phase 4 now proven live too --
`fvg` is `active` in production and has autonomously placed two real
demo orders, one TP and one SL; self-service MT5 provisioning +
removal + the bridge sync-gap fix all shipped the same night).
Detailed records live in the docs referenced below -- this is the map,
not the territory.

---

## Where things stand

| Phase | Status |
|---|---|
| Phase 0 -- Postgres schema + auth foundation | **Complete.** Running locally, 13+ tests, CI on GitHub Actions, both originally-unverified items closed (credential encryption round-trip, shadow-models CHECK constraint). See `PRODUCTION_READINESS.md`. |
| Phase 1 -- streaming state machine | **Complete.** Reproduces the locked batch FVG model **exactly: 603/603 trades**, every field identical at 1e-9 tolerance, across 10.5 years, bar-by-bar, no lookahead. See `PHASE1_VALIDATION.md` (both parts) and `phase1/streaming/README.md`. |
| Phase 2 -- read-only MT5 broker adapter | **Complete.** Bridge service live on the Windows VPS, verified against the real demo account on all four endpoints. See `PHASE2_VALIDATION.md`. |
| Phase 3 -- shadow mode (live data, journal-only) | **Step 8 done (2026-08-29 report): ~5 weeks of clean, unattended live running since the two cold-start bugs were fixed** (`PHASE3_RESTART_RECOVERY.md` addendum 2) -- no further bugs, no manual intervention needed. `CurrentDay` construction, the 10am decision, and backfill all confirmed working correctly against genuinely live bars, not just engineered test data. Only step 9 (write `PHASE3_VALIDATION.md`) remains -- see "Phase 3 progress" below. (The two real signal fires this week went straight to real orders, not journal entries -- see Phase 4.) |
| Phase 4 -- real demo orders (designated live model only) | **Live and proven, not just built.** `fvg`'s `ModelConfig.status` is `active` in production. This week it found two trade candidates entirely on its own and placed two real demo orders through the bridge with zero manual intervention -- one filled and hit take-profit, the other filled and hit stop-loss. Full pipeline validated end-to-end for the first time: autonomous signal detection -> real order placement (`bridge/app/main.py`'s order endpoints, `BridgeConfig.orders_enabled`) -> real fill -> real close via TP or SL (`shadow_runner/order_manager.py`, "Phase 4 step 2c"). n=2 says nothing about edge (explicitly deferred to the real-money gate, item 7 below) -- what it proves is that the mechanics work correctly under real autonomous operation, both outcome paths (TP and SL) included, not just in the 37 unit tests that covered this before. |

Full test suite: **62 passed** from Phase 0/1 (Phase 0 app tests + 7
streaming component test files), **plus 25 more from Phase 3**
(`day_selection_gate`, the `DayOrchestrator` event_sink addition,
runner orchestration/timing, trade entry-exit lookup, restart recovery,
cold-start bootstrap, and the two live-bug regression tests) -- all
pure-logic/fake-DB, no real Postgres available in the sandbox these were
authored in; verified against the real Hetzner DB by running live
instead (see `PHASE3_RESTART_RECOVERY.md`). (Phase 2 has no automated
test suite -- verified live via curl against the real demo account; see
`PHASE2_VALIDATION.md`.)

## Repo layout (the parts that matter)

- `app/` -- FastAPI app: auth, models (users, broker_credentials with
  Fernet-encrypted secrets, user_settings, trades, events,
  notifications), logging, error handling.
- `phase1/` -- golden-master tooling (`extract_golden_master.py`,
  `convert_mt_to_ascii.py`, committed `golden_master_trades.jsonl`;
  the 12MB `golden_master_events.jsonl` is gitignored, regenerable per
  `phase1/README.md`).
- `phase1/streaming/` -- the seven state-machine components + README:
  `daily_swing_detector`, `intraday_swing_detector`, `raid_detector`,
  `mss_watch`, `fvg_detector`, `trade_attempt`, `day_orchestrator`.
- `bridge/` -- Phase 2 MT5 bridge service. Lives on the Windows VPS at
  `C:\bridge`, not yet folded into the main Linux-side repo tree --
  worth deciding whether it becomes a subfolder here or stays a
  separate deployable unit, since it runs on different hardware
  (Windows-only, one process per broker account).
- `tests/` -- everything, flat.
- **Deployed on Hetzner** at `/app4/the-bot` (matches the numbering
  convention of this box's other projects: `/app`, `/app2/content-agent`,
  `/app3/trywaka-api`). `docker-compose.yml` there defines `db` (Postgres
  16-alpine) and `api` (this repo's FastAPI app, built via a new
  `Dockerfile` at repo root) -- both running persistently
  (`restart: unless-stopped`), same pattern as the other projects on that
  box. `.env` on the Hetzner box holds the real `DATABASE_URL`,
  `JWT_SECRET_KEY`, `CREDENTIALS_ENCRYPTION_KEY` -- generated fresh for
  this deployment, not copied from local dev. `api` is on host port 8003
  (8000/8001/8002 already taken by other projects on that box). Neither
  `Dockerfile` nor the Hetzner-specific `docker-compose.yml` are committed
  to the repo yet -- open item, see below.

## Key decisions made (and why)

- **Python end-to-end** (FastAPI/SQLAlchemy/Alembic/Postgres), chosen
  specifically because Phase 2 needs the official `MetaTrader5` Python
  package (no Node equivalent).
- **Streaming = clean reimplementation, validated against a golden
  master** (not importing batch functions). The golden master logs
  every intermediate event (93,791 events), enabling stage-by-stage
  validation.
- **One component per stage, validated in isolation before the next.**
  This caught two real wiring bugs the components themselves didn't
  have: (1) the "one raid search at a time" scheduling assumption
  (cost 285 of 603 trades) -- fixed by `DayOrchestrator`'s parallel
  candidates + lexicographic (raid_bar, mss_bar) priority among FILLED
  attempts; (2) target-window seed off by one bar (29 field
  mismatches). Full story in `PHASE1_VALIDATION.md` part 2.
- **Per-user live/shadow model split** (one live model, others
  journal-only) with DB-level constraint preventing overlap.
- **golden_master_events.jsonl not committed** (12MB, regenerable).
- **Phase 2 architecture decision (resolved):** the official
  `MetaTrader5` package is Windows-only. Went with a small Windows VPS
  (Database Mart Express, 2 cores/6GB/60GB SSD, Windows Server 2022)
  running a standalone FastAPI bridge -- not Wine, not the FOREX.com
  REST API (multi-day approval, deferred). Broker: switched from the
  originally-planned FOREX.com to **Exness**, demo account 476123801
  on Exness-MT5Trial9, symbol EURUSDm.
- **One process per broker account (hard constraint, not a choice):**
  `mt5.initialize()` holds exactly one connection per OS process --
  no multiplexing two accounts through one process. Multi-user is N
  worker processes side by side (own config file, own port each), not
  one router. Second account (a friend's) still deferred --
  credentials not yet available -- but the codebase is ready: adding
  it later is just a new config file + port, zero code changes.
- **Bridge credentials: local JSON config file for v1**
  (`C:\bridge\config.json`), not the Postgres-driven encrypted flow
  from Phase 0. That flow is deferred until the Linux app is actually
  ready to drive the bridge; right now it's a standalone service.
- **Timezone normalization (rulebook §33.3), now implemented and
  verified live:** MT5 server confirmed at GMT+0. Every timestamp the
  bridge returns is converted via `zoneinfo("America/New_York")` at
  request time -- never a hardcoded offset -- so it tracks DST
  automatically. Verified against a live tick: 20:58:59 UTC ->
  16:58:59 NY (-04:00), the correct EDT offset for late July.

## Phase 3 progress

Full plan is 9 steps. Status:

1. ✅ **Persistent `api` service on Hetzner** -- turned the one-off test
   container into a real `docker-compose` service, matching the existing
   project pattern on that box.
2. ✅ **Network path Hetzner -> VPS bridge.** The VPS originally sat
   behind Database Mart's shared IP with NAT port-forwarding -- only RDP
   was forwarded, so external requests to port 8001 hung indefinitely
   (never reached the OS, nothing to do with Windows Firewall). Fixed by
   purchasing a dedicated IP (~$2/mo). Full story and current
   IP/port in `PHASE2_VALIDATION.md`'s addendum. Verified:
   `curl http://<VPS-dedicated-IP>:8001/health` from the Hetzner box
   returns a live, correct response.
3. ✅ **Day-selection-gate as real code** --
   `phase1/streaming/day_selection_gate.py` (`DaySelectionGate`). FOMC
   exclusion (calendar extended through Dec 2026, with a 45-day
   staleness self-check so it can't silently go stale again), trend
   determination wrapping `DailySwingDetector`, and 5am-5pm NY session
   windowing. 7 unit tests.
4. ✅ **Closed the event-type gap.** `VALID_EVENT_TYPES` in
   `app/models/event.py` extended with 12 missing types (swing
   confirmations, `fvg_rejected_min_stop`, `trade_closed`,
   `day_skipped_*`/`day_trend_determined`, `fomc_calendar_stale_warning`,
   later `trend_history_bootstrapped`). Also added `events.is_shadow`
   (mirrors `Trade.is_shadow` -- migration `0002`), since `order_filled`/
   `trade_closed` would otherwise be ambiguous once Phase 4 adds real
   broker events.
5. ✅ **Wrote the shadow runner** -- new `shadow_runner/` package
   (`config.py`, `bridge_client.py`, `day_state.py`, `runner.py`,
   `persistence.py`, `main.py`). Real design problem solved along the
   way: `DayOrchestrator` needs `session_end_idx` (10am's bar index)
   fixed at construction time, which isn't knowable until 10am's data
   actually exists -- so the runner waits until then, then backfills the
   whole morning in one pass, rather than constructing too early with a
   wrong value. Also: `DayOrchestrator` itself gained an additive
   `event_sink` callback (zero behavior change, confirmed by the
   pre-existing test suite still passing unmodified) so intermediate
   events are observable for journaling, not just the final trade.
6. ✅ **Mid-day-restart recovery** -- `PHASE3_RESTART_RECOVERY.md` is the
   full writeup (explicitly requested: document what's covered and what
   to do if it's insufficient). Two independent recoveries: trend history
   (always safe, pure read) and today's in-progress session (replayed
   only if nothing's been journaled yet today, to avoid duplicate rows --
   otherwise skipped with a logged gap). Plus a cold-start bootstrap
   added after first deployment (see step 8) that seeds ~17 trading days
   of real trend history instead of starting from zero.
7. ✅ **Deployed as its own container** -- `shadow_runner` service in
   Hetzner's `docker-compose.yml`, same image/`Dockerfile` as `api`,
   different `command:` (`python -m shadow_runner.main`), same pattern
   as this box's `app-worker-1`. `BRIDGE_URL` and
   `SHADOW_RUNNER_USER_ID` set directly on the service.
8. ✅ **Run live, validate -- done (reported 2026-08-29).** First live
   deployment happened to cold-start on a weekend and immediately
   surfaced two real bugs neither design review nor unit tests caught
   -- both found, fixed, and confirmed via real logs on Hetzner within
   the same session. Full writeup in `PHASE3_RESTART_RECOVERY.md`'s
   addendum 2: (1) the bootstrap's own marker event was tricking the
   "did today already start" check into a false positive on every cold
   start; (2) cold-starting when the bridge's most-recent bars belong
   to an already-finished day (Friday's tail end, seen on a Sunday)
   produced a misleading `insufficient_bars` verdict instead of just
   waiting for real data. Both fixed. Since then: **~5 weeks of clean,
   unattended live running, no further bugs, no manual intervention.**
   Two real signal fires in the most recent week -- `fvg`'s
   `ModelConfig.status` is `active` in production, so both went
   straight to real (demo) orders rather than journal entries, one
   hitting take-profit and one hitting stop-loss (see Phase 4's own
   entry above for what that actually validates). n=2 says nothing
   about the strategy's edge (explicitly deferred to the real-money
   gate -- item 7 below) -- what it confirms here is the mechanical
   question step 8 was actually asking: `CurrentDay` construction, the
   10am decision, and backfill all work correctly against genuinely
   live bars, unattended, not just engineered test data or short-lived
   manual observation.
9. ⬜ **Write `PHASE3_VALIDATION.md`** -- step 8 now has real live
   output to report on (5 weeks clean uptime, 2 real signal fires,
   1W/1L). Actual next step for Phase 3.

Deliberately deferred, not blocking: bridge authentication (currently
gated only by the Windows Firewall IP restriction to the Hetzner box --
acceptable for now, revisit before this expands beyond a small, fixed
set of trusted callers).

## Open items, in priority order

1. ✅ **Phase 3 step 8: run live, validate.** DONE (2026-08-29 report)
   -- see "Phase 3 progress" above and Phase 4's own entry: ~5 weeks
   clean unattended running, two real autonomous demo trades this week
   (one TP, one SL). Only step 9 (write `PHASE3_VALIDATION.md`)
   remains for Phase 3.
2. ✅ **Commit the Hetzner `docker-compose.yml`.** DONE 2026-08-29,
   commit `0a9c571` (same risk class `Dockerfile` already had once --
   `6dd1892` -- the original, never-committed copy on the Hetzner box
   went missing with no trace, discovered while fixing a stale-image
   CORS bug). The real file's `db.environment` had `POSTGRES_PASSWORD`
   hardcoded in plaintext -- caught before committing anything,
   replaced with `${POSTGRES_PASSWORD}` (Compose reads this from a
   `.env` file in the same directory, same convention every other
   secret in this project already follows). **Required one-time
   follow-up on Hetzner, not yet confirmed done**: add
   `POSTGRES_PASSWORD=<the real value>` to `/app4/the-bot/.env`
   (gitignored, never committed) before the next `git pull` +
   `docker compose up` there -- without it, the substitution resolves
   to empty and the `db` container won't start with the right
   password.
3. ✅ **`C:\bridge`/checkout sync gap -- fully fixed and verified live,
   both halves (2026-08-29).** Was: `bridge/`
   lived only in the git checkout (`C:\the_bot_temp`); the VPS's
   actually-running `C:\bridge` was a separate, hand-copied directory,
   so every new/changed file under `bridge/` needed a manual
   `Copy-Item` or it was silently missing -- hit twice in one night
   before this was fixed. Real fix: `C:\bridge` is now itself a real
   git working directory, sparse-checked-out to just the `bridge/`
   subtree (cone mode: `git sparse-checkout set bridge`), landing at
   `C:\bridge\bridge\...`. A fresh, separate venv was created there
   (`C:\bridge\bridge\venv`) rather than moving the existing
   `C:\bridge\venv` -- that one's still the configured interpreter for
   `MT5Bridge-Tony` and the live `bridge-476787945` worker, and moving
   it would have broken either on its next restart. The provisioning
   poller (`MT5ProvisioningPollerTask`) was cut over to the new
   checkout -- its env vars now include `BRIDGE_ROOT=C:\bridge\bridge`
   (already-existing, environment-overridable config, no code change
   needed), and its Scheduled Task action points at the new script
   path. **Verified live**: a real, brand-new commit
   (`cc89ff9`..`8f746dd`) flowed onto the VPS via a plain `git pull`
   with zero `Copy-Item`, confirmed via a marker comment actually
   appearing in the file afterward.

   One real hiccup along the way, resolved cleanly: the first
   `git checkout -b main origin/main` of a brand-new branch under
   sparse-checkout populated one file (`app/main.py`) outside the
   intended `bridge/` scope, colliding by coincidence with Tony's own
   pre-existing `C:\bridge\app\main.py` (same relative path, totally
   unrelated files -- the top-level repo's FastAPI backend vs. the
   bridge worker). Confirmed directly (content + an unchanged
   `LastWriteTime` predating the whole session) that Tony's real file
   was never touched -- git's own "already present, not updated"
   safety behavior held. Fixed with `git rm --cached --sparse
   app/main.py` (index-only, never touches the working tree) --
   left staged-uncommitted intentionally; never push that removal, the
   real backend file must stay in shared history.

   **Stage B (`MT5Bridge-Tony`'s own cutover) is now also done,
   verified live, on the second attempt.** The first attempt caused a
   real, brief outage: before changing anything, only `AppDirectory`
   and `AppEnvironmentExtra` were checked/recorded -- not `Application`
   -- and `Application` was wrongly assumed to follow the poller's
   `python.exe -m <module>` pattern. Tony's service actually runs the
   venv's `uvicorn.exe` console script directly (`Application =
   ...\venv\Scripts\uvicorn.exe`, `AppParameters = app.main:app --host
   0.0.0.0 --port 8001 --workers 1`, no `-m uvicorn` needed since
   `uvicorn.exe` already is uvicorn). Setting `Application` to
   `python.exe` produced `python.exe: can't open file 'app.main:app'`
   (no `-m` flag, so Python tried to open it as a script) -- NSSM
   auto-paused the crash-looping service. Rolled back cleanly (the
   copied, not moved, `config.json` meant the original was never at
   risk) and confirmed `/health` -> `connected: true, trade_allowed:
   true` again before retrying. **Lesson, worth generalizing beyond
   this one incident**: capture all four relevant NSSM fields
   (`Application`, `AppDirectory`, `AppParameters`,
   `AppEnvironmentExtra`) before changing any live service's config,
   not just the ones expected to matter, and never assume one
   service's configuration pattern applies to another just because
   both are "a Python worker service set up earlier."

   The corrected retry succeeded cleanly: `Application ->
   C:\bridge\bridge\venv\Scripts\uvicorn.exe` (not `python.exe`),
   `AppDirectory -> C:\bridge\bridge`, `AppEnvironmentExtra` set to all
   three of `BRIDGE_TOKEN`/`CREDENTIAL_API_URL`/`BRIDGE_CONFIG_PATH=
   C:\bridge\bridge\config.json` together in one call, all three values
   verified with `nssm get` *before* restarting this time -- restart
   succeeded first try, `/health` confirmed `connected: true,
   trade_allowed: true`. Both `MT5Bridge-Tony` and the provisioning
   poller now run from the same `C:\bridge\bridge` git checkout; a
   future change to either `bridge/app/` or
   `bridge/scripts/provisioning_poller/` only needs `git pull` there,
   no more `Copy-Item` anywhere in this pipeline.

   **Still open, low urgency**: the old top-level `C:\bridge\app\`/
   `C:\bridge\scripts\` (now-unused pre-migration duplicates) and the
   old `C:\bridge\venv` (no longer referenced by anything, now that
   both services are on the new one) can be deleted once this has run
   stable for a while -- not deleted immediately, deliberately, to keep
   an easy manual fallback for a few days after two real service
   restarts in one night.
4. **Second broker account (friend's).** Still blocked on credentials,
   not on anything technical -- the bridge architecture already
   supports it. Pick up whenever available: new `config.json`, new
   port, second `uvicorn` process.
5. ~~Wire the bridge worker into a real process supervisor~~ **DONE
   2026-08-28.** Tony's real account (476123801, Exness-MT5Trial9) is
   now supervised by NSSM as a real Windows service (`MT5Bridge-Tony`,
   `C:\nssm\nssm.exe`), `StartType: Automatic` -- survives a reboot,
   auto-restarts on crash. Confirmed via `/health` -> `connected: true`
   after the cutover. Hit one real snag worth remembering: the service
   initially crash-looped (`KeyError: 'BRIDGE_TOKEN'`) because
   `AppEnvironmentExtra` was never actually set on it -- fix was
   `nssm set MT5Bridge-Tony AppEnvironmentExtra BRIDGE_TOKEN=... CREDENTIAL_API_URL=...`
   (both as separate space-separated arguments to one `nssm set` call,
   not joined). Also: the *old* foreground `uvicorn` process was still
   silently holding port 8001 the whole time (had to `Stop-Process` it
   before NSSM's copy could bind) -- so the account was never actually
   down during any of this, just running on the old process until the
   cutover completed. Still outstanding: delete the pre-cutover backup
   files left in the VPS scratchpad (`config.json.v1....bak` still has
   the plaintext MT5 password).
6. **Self-service MT5 provisioning -- all 3 phases code-complete and
   now verified live end-to-end (2026-08-29), including a real
   architecture fix.** Goal: submitting the "Broker Connection" form
   alone provisions an account's MT5 terminal + bridge worker
   automatically, no manual VPS steps (this is what item 4 above
   previously required by hand). Phase 0 (DB schema + internal
   claim/complete/fail API, `54fbcae`), Phase 1 (the VPS-side poller,
   `bridge/scripts/provisioning_poller/`, `0f78fbc`/`fc92b15`/
   `a6c9688`/`e01c67c`), and Phase 2 (`6f6c96b` -- flips
   `POST /broker-credentials` to auto-set `provisioning_status=
   'pending'`, adds live step-by-step progress reporting and a
   self-service retry endpoint, plus the frontend UI for all of it)
   were all built the same night. Real live testing then hit a
   genuinely hard bug: a freshly-provisioned account failed every time
   at `verifying_login` with `mt5.initialize() failed:
   (-10001, 'IPC send failed')` -- reproduced on multiple disposable
   demo accounts, ruling out account-side throttling. Root cause, found
   by direct comparison (`53f3907`, `96592e9` for the misc code
   improvements found along the way; the actual fix is infrastructure,
   not code): **the poller ran as an NSSM Windows service, which lives
   in Session 0 with no desktop -- a GUI app like `terminal64.exe`
   launched from there has nothing to attach to and fails silently.**
   Proven directly: the identical `mt5.initialize()` call succeeded
   when run manually from an interactive PowerShell session and failed
   every time through the service. Marking the service
   `SERVICE_INTERACTIVE_PROCESS` did NOT fix it -- Windows Server 2022
   no longer meaningfully supports interactive services despite `sc
   config` accepting the flag.

   **Fix deployed and verified live**: the poller now runs as a
   Scheduled Task (`MT5ProvisioningPollerTask`, registered via
   `Register-ScheduledTask` rather than `schtasks.exe` to avoid its
   default 3-day execution time limit) under `vps-cgea\administrator`'s
   own interactive logon session instead of the old `MT5Provisioner`
   NSSM service (now stopped + `start= disabled`, not deleted, in case
   of rollback). Its env vars (`MACHINE_TOKEN` etc, same values the
   NSSM service used) live in
   `C:\bridge\scripts\provisioning_poller\run_poller_task.ps1` --
   **VPS-only, deliberately never committed to git**, same treatment as
   `frontend/.env`; if this VPS is ever rebuilt, that file has to be
   recreated by hand from the current `MACHINE_TOKEN`/
   `CREDENTIAL_API_URL`/`PROVISIONING_PUBLIC_HOST`/`FIREWALL_REMOTE_IP`
   values. A real fresh demo account (476786959) went from a brand-new
   `POST /broker-credentials` call all the way to `provisioning_status:
   active, bridge_configured: true` under this new setup.

   **Still outstanding, deliberately deferred**: this only works while
   that interactive session stays logged in -- it does NOT yet survive
   a VPS reboot with nobody at the keyboard. The real fix for that is
   Windows auto-logon (via Sysinternals `Autologon.exe`, not the raw
   registry method) so the interactive session comes back automatically
   at boot, which needs a real reboot to verify -- deliberately not
   done yet tonight because this VPS also hosts Tony's live, actively-
   connected bridge (`MT5Bridge-Tony`), and a reboot means a real
   (brief) outage for his real account. Do this at a deliberately
   chosen time, not casually.

   The full signup-to-connected flow through the actual frontend UI is
   now also confirmed: submitted a fresh disposable demo account
   (`476787945`) directly through the "Broker Connection" form with
   zero manual/curl intervention, and it went to `ACTIVE` on its own. A
   real, unbounded `provisioning_error` (the stale MT5 journal dump)
   also exposed a frontend layout bug along the way -- rendered as a
   raw `<p>`, one long error blew out the whole table -- fixed by
   bounding it in a scrollable `<pre>`
   (`frontend/src/pages/BrokerCredentials.tsx`). Still open, cosmetic
   only: the orphan `C:\bridge\accounts\05315ccf\config.json` skewing
   `_next_free_port` to 8003+ (safe to `rm -rf`). See the
   `self_service_mt5_provisioning` memory for the full design and every
   real bug found/fixed along the way.

   **Account removal (decommission), same night, commit `0243786`:**
   users had no way back -- `PATCH .../is_active` only soft-disables
   trading, nothing ever tore down a provisioned account's real MT5
   terminal/NSSM service/firewall rule. Added the teardown half of the
   same state machine (`decommissioning -> removing ->
   removed/decommission_failed`, mirroring `pending -> in_progress ->
   active/failed`) instead of inventing a hard-delete convention this
   codebase has never used anywhere. `POST
   /broker-credentials/{id}/remove` is immediate when nothing was ever
   provisioned (no VPS round trip needed); otherwise it queues a real
   job claimed via new parallel `/internal/decommission-jobs/*`
   endpoints, which drive the already-battle-tested
   `_cleanup_prior_attempt()` from a new trigger -- no new Windows-side
   logic needed at all. **Verified live end-to-end same night**: fired
   a real removal against account `476786959`
   (`44f4e73d-8b62-48e5-b8e8-da0995571a31`) and confirmed on the VPS
   directly afterward -- `C:\MT5-44f4e73d` gone, its NSSM service gone
   ("service does not exist"), its firewall rule gone, its
   `C:\bridge\accounts\44f4e73d` config dir gone. Also clicked through
   the real "Remove" button in the browser UI successfully -- full
   parity of proof with provisioning's own (API/curl AND real
   click-through both verified).
7. **Strategy-level research gaps (block real money, NOT the build):**
   no slippage/spread/commission modeling anywhere (material at ~1.1
   avg realized R:R); out-of-sample validation never done. Phase 4
   ends at demo orders; real money is a separate go/no-go gated on
   these.
8. **Optional hardening backlog:** randomized-slice differential
   testing; per-bar performance profiling; Phase 0 prod items (rate
   limiting on /auth/login, CORS, secrets manager, dev/staging/prod
   separation); passlib deprecation (bcrypt<4.0 pin); pydantic
   class-based Config in `app/schemas/auth.py`; cosmetic -- uvicorn on
   Windows occasionally logs a harmless `WinError 64` asyncio traceback
   on a client connection reset (Proactor event loop quirk), doesn't
   crash the server, fix available later by switching to the Selector
   loop; bridge authentication (see "Phase 3 progress" above).

## Security notes

- A GitHub PAT was exposed in chat and in `.env` during earlier work --
  **revoked (confirmed by owner).** Keep `.env` free of anything the
  app doesn't declare; `Settings` rejects unknown vars (strict mode)
  unless `extra="ignore"` was added.
- Broker credentials (Postgres path): encrypted at rest (Fernet),
  decryptable by design (MT5 login needs plaintext). Never log them;
  `__repr__` is scrubbed.
- Bridge credentials (VPS path, Phase 2): plaintext in
  `C:\bridge\config.json` for v1 -- this file must never be committed;
  keep `config.example.json` tracked instead. Superseded by the
  Postgres flow once the Linux app drives the bridge directly.
- Bridge network exposure (Phase 3 addition): the VPS now has a
  dedicated public IP (`38.247.137.208`, RDP moved to port `1097`),
  with Windows Firewall restricting inbound 8001 to the Hetzner box's
  IP specifically -- not open to the internet at large. The bridge
  itself still has no authentication layer (no token/header check);
  reachability is gated purely by that firewall IP restriction. Fine
  for the current small, fixed set of trusted callers; revisit before
  this expands. Full detail in `PHASE2_VALIDATION.md`'s addendum.
- Hetzner box's `.env` (holding `DATABASE_URL` with the real DB
  password, `JWT_SECRET_KEY`, `CREDENTIALS_ENCRYPTION_KEY`) lives only
  on that server, generated fresh during this deployment -- not the
  same values as local dev, and not committed anywhere.

## Working style (established, keep it)

- Every change isolated and tested individually; never bundled.
- Suspicious of "too good" results -- multiple real bugs caught this
  way; all documented, never silently fixed.
- Zero-miss standard for validation: every discrepancy individually
  traced to a root cause before proceeding.
- Complete standalone docs after each milestone (this file, the
  READMEs, PHASE1_VALIDATION.md, PHASE2_VALIDATION.md,
  PHASE3_RESTART_RECOVERY.md, PRODUCTION_READINESS.md).
- One clear recommendation over menus of options; plain-language
  explanations on request; finish current work before starting new.
- When something breaks in production, fix it AND write down what broke,
  why, and how it was confirmed fixed -- not just the fix itself (see
  PHASE3_RESTART_RECOVERY.md's addendum 2 for the pattern: symptom, root
  cause, fix, confirmed-via-real-logs, in that order).

## How to resume in a new chat

Paste something like: "Continuing the SMC/ICT live bot build. Phases 0,
1, and 2 complete. Phase 3 (shadow mode) is done through step 8 -- the
shadow runner has been running live and clean on Hetzner for ~5 weeks,
unattended, since two early cold-start bugs were found and fixed (see
PHASE3_RESTART_RECOVERY.md's addendum 2). Phase 4 is live too: the
`fvg` model's `ModelConfig.status` is `active` in production, and it
has autonomously placed two real demo orders (one take-profit, one
stop-loss) with zero manual intervention. Only step 9 (write
PHASE3_VALIDATION.md) remains open on the validation side. See
HANDOFF.md's 'Open items' for everything else still pending." Then
share the current repo state (zip or file tree) if code work is
needed.
