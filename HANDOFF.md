# HANDOFF — SMC/ICT Trading Bot: Live Bot Build

**Purpose of this file:** continuity anchor for resuming work in a new
session. Everything here is current as of mid-Phase-3 (infrastructure
stage complete, shadow-runner code not yet started). Detailed records
live in the docs referenced below -- this is the map, not the territory.

---

## Where things stand

| Phase | Status |
|---|---|
| Phase 0 -- Postgres schema + auth foundation | **Complete.** Running locally, 13+ tests, CI on GitHub Actions, both originally-unverified items closed (credential encryption round-trip, shadow-models CHECK constraint). See `PRODUCTION_READINESS.md`. |
| Phase 1 -- streaming state machine | **Complete.** Reproduces the locked batch FVG model **exactly: 603/603 trades**, every field identical at 1e-9 tolerance, across 10.5 years, bar-by-bar, no lookahead. See `PHASE1_VALIDATION.md` (both parts) and `phase1/streaming/README.md`. |
| Phase 2 -- read-only MT5 broker adapter | **Complete.** Bridge service live on the Windows VPS, verified against the real demo account on all four endpoints. See `PHASE2_VALIDATION.md`. |
| Phase 3 -- shadow mode (live data, journal-only) | **In progress (steps 1-7 of 9 complete).** Shadow runner is deployed and running live on Hetzner (`shadow_runner` container, polling the VPS bridge every 60s). Two real bugs found and fixed during the first live cold-start (see `PHASE3_RESTART_RECOVERY.md` addendum 2). Step 8 (run live, validate) is the current focus. See "Phase 3 progress" below. |
| Phase 4 -- real demo orders (designated live model only) | Not started. |

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
8. 🔶 **Run live, validate -- in progress.** First live deployment
   happened to cold-start on a weekend and immediately surfaced two real
   bugs neither design review nor unit tests caught -- both found,
   fixed, and confirmed via real logs on Hetzner within the same
   session. Full writeup in `PHASE3_RESTART_RECOVERY.md`'s addendum 2:
   (1) the bootstrap's own marker event was tricking the "did today
   already start" check into a false positive on every cold start; (2)
   cold-starting when the bridge's most-recent bars belong to an
   already-finished day (Friday's tail end, seen on a Sunday) produced a
   misleading `insufficient_bars` verdict instead of just waiting for
   real data. Both fixed; system now correctly idles through market-
   closed periods with clear log output. **Next real checkpoint:**
   confirm a full normal trading day once the market reopens --
   `CurrentDay` construction, the 10am decision, backfill, and (if any
   signal fires) a real journaled trade, still against Friday's
   engineered test data replaced by genuinely live EURUSDm bars.
9. ⬜ **Write `PHASE3_VALIDATION.md`** -- once step 8 has a real trading
   day's worth of live output to report on.

Deliberately deferred, not blocking: bridge authentication (currently
gated only by the Windows Firewall IP restriction to the Hetzner box --
acceptable for now, revisit before this expands beyond a small, fixed
set of trusted callers).

## Open items, in priority order

1. **Phase 3 step 3: day-selection-gate as real code.** See "Phase 3
   progress" above -- this is the actual next coding task.
2. **Commit the Hetzner deployment files.** `Dockerfile` and the
   Hetzner-specific `docker-compose.yml` currently only exist on the
   Hetzner box itself, not in the repo. Same class of gap as `bridge/`
   below -- worth fixing before either drifts from what's actually
   running.
3. **Decide where `bridge/` lives in version control.** Currently only
   exists on the VPS filesystem, not committed anywhere. Given it's a
   genuinely separate deployable (Windows-only, different hardware),
   worth deciding: subfolder in the main repo, or its own repo.
4. **Second broker account (friend's).** Still blocked on credentials,
   not on anything technical -- the bridge architecture already
   supports it. Pick up whenever available: new `config.json`, new
   port, second `uvicorn` process.
5. **Strategy-level research gaps (block real money, NOT the build):**
   no slippage/spread/commission modeling anywhere (material at ~1.1
   avg realized R:R); out-of-sample validation never done. Phase 4
   ends at demo orders; real money is a separate go/no-go gated on
   these.
6. **Optional hardening backlog:** randomized-slice differential
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
1, and 2 complete. Phase 3 (shadow mode) steps 1-7 of 9 complete -- the
shadow runner is deployed and running live on Hetzner
(`shadow_runner` container, polling the VPS bridge every 60s). Step 8
(run live, validate) is in progress: two real bugs were found and fixed
during the first live cold-start (bootstrap marker false-positive,
stale-bar-from-a-finished-day mishandling) -- see
PHASE3_RESTART_RECOVERY.md's addendum 2 for the full story, both
confirmed fixed via real Hetzner logs. Next checkpoint: confirm a full
normal trading day once the market reopens, then write
PHASE3_VALIDATION.md (step 9)." Then share the current repo state (zip
or file tree) if code work is needed.
