# Pending items

A consolidated punch list of everything still open across the project,
as of 2026-08-29. `HANDOFF.md`'s "Open items" section has the full
narrative/context behind each of these; this file is the condensed,
actionable view. Update this alongside `HANDOFF.md` when an item's
status changes -- don't let the two drift.

---

## Real bugs found 2026-09-02 (live-money-affecting, discovered a week late)

Found by cross-referencing the real MT5 mobile app's trade history
against this app's own `events`/`trades` tables, prompted by the user
asking why a trade ran overnight instead of closing same-day. Full
story in `PHASE3_VALIDATION.md`'s "Correction (2026-09-02)" section --
these two are the same incident, two separate root causes.

- [x] **Sibling-order race: a second real fill can get silently
      dropped instead of tracked.** DONE 2026-09-02. When two
      candidates' pending orders both fill before the loser's cancel
      completes, `order_manager.py`'s `_on_fill()` used to unconditionally
      overwrite its tracking to keep only the winning ticket -- even
      when the cancel failed because the "loser" had *also* already
      filled for real. That second real position never got a
      take-profit attached (only the tracked winner does) and was
      invisible to everything downstream from that point on. Confirmed
      live: ticket `#3147397683`, 27 Aug 2026, real Stop Loss but blank
      Take Profit, rode alone to a real loss. **Fix**: new
      `_handle_sibling_cancel_failure()` actively checks whether the
      cancel failed because the sibling is now a real open position --
      if so, closes it immediately (a genuine second fill from one
      candidate-set is an execution accident, not a second trade the
      strategy wanted) rather than giving it its own parallel tracking
      (a bigger, riskier change than this warranted). New
      `duplicate_fill_closed` event journals it; a distinct
      `duplicate_fill_close_failed` check name if even the close fails,
      so a human knows to act. 5 new tests. 356 passed / 1 skipped / 10
      pre-existing unrelated failures, 0 regressions. **Not yet deployed
      to the live VPS.**
- [x] **Cross-day restart recovery gap, confirmed to have happened for
      real.** DONE 2026-09-02. `PHASE3_RESTART_RECOVERY.md`/
      `PHASE3_VALIDATION.md` both already documented that the runner
      has no recovery for anything before "today" -- a restart spanning
      midnight means that whole prior day is never journaled.
      Previously theoretical ("not yet exercised for real"); confirmed
      to have happened for real 27-28 Aug 2026 (see bug 1's entry above
      for the shared incident). Planned via plan mode first (see this
      session's plan history) after a mid-design discovery changed
      scope: the bridge only supports looking up a KNOWN ticket's
      history, not a date-range listing, so full reconciliation of
      already-closed historical trades would need a new endpoint on
      Tony's live bridge -- deliberately scoped out in favor of three
      lower-risk pieces, all Hetzner-only: **(1)** a Telegram alert the
      moment a cross-day gap is detected on startup; **(2)**
      `shadow_runner/orphan_recovery.py` -- checks the broker directly
      for a real open position with no matching `trades` row and
      self-heals it (attaches the take-profit target it would have
      gotten live), scoped to still-open positions only (the more
      urgent case); **(3)** `_replay_historical_day()` -- reconstructs
      the raid/MSS/FVG/candidate journal for each missed day,
      structurally incapable of ever placing a real order for a
      historical day (never constructs an `OrderManager` for one at
      all, reusing the existing `combined_sink` guard rather than
      adding a new conditional). 18 new tests, including a proof that a
      real candidate firing during historical replay cannot reach a
      real order. 370 passed / 1 skipped / 10 pre-existing unrelated
      failures, 0 regressions. **Not yet deployed to the live VPS.**
      Full historical reconciliation of already-closed trades (the
      bridge-endpoint version) remains a known, documented, deliberate
      gap -- revisit separately if it still matters once this is live.

## Quick cleanup (low effort, low risk)

- [ ] **Delete the pre-cutover backup file on the VPS**
      (`config.json.v1....bak`) -- still has a **plaintext MT5
      password** sitting in the scratchpad. Higher urgency than the
      other cleanup items below.
- [ ] Delete the orphan `C:\bridge\accounts\05315ccf\config.json` --
      skews `_next_free_port` to 8003+, otherwise harmless.
- [ ] Delete the now-unused old `C:\bridge\app\`/`C:\bridge\scripts\`/
      `C:\bridge\venv` once the new `C:\bridge\bridge` checkout has run
      stable for a few days.

## Blocked externally

- [ ] **Second broker account (friend's).** Waiting on their
      credentials -- nothing technical blocking this; the bridge
      architecture already supports it (new `config.json`, new port,
      second `uvicorn` process).

## Deliberately deferred -- now more urgent than before

- [ ] **Windows auto-logon / reboot survival for the VPS.** The
      provisioning poller runs as a Scheduled Task tied to an
      interactive login, not a real boot-time service -- a VPS reboot
      with nobody logged in means it doesn't come back automatically.
      Was a "test accounts get blocked" inconvenience before; now that
      `fvg` is live-trading real orders, it's a live-trading
      availability risk. Needs Sysinternals `Autologon.exe` (not the
      raw registry method) plus a real reboot to verify -- schedule
      deliberately, this VPS also hosts Tony's live bridge.

## Real work, unblocked and ready

- [x] **Dynamic model registry.** DONE 2026-08-31, **deployed to the
      live VPS 2026-09-02.** User corrected a wrong assumption
      (fvg/ob/fvg_ob are NOT the only models -- the roster keeps
      growing, names unknown yet), which exposed a real gap:
      `events.model`/`trades.model` had hardcoded DB CHECK constraints
      limiting them to exactly those 3 names, and 3 separate frontend
      files hardcoded the same list. New `models` table (migration
      0018) + FK constraints instead; adding a model is now one
      admin-UI form (`/admin/models`), not a migration -- backfills
      every existing user's `model_configs` row immediately, no script
      run needed. 346 passed (7 new), 10 pre-existing unrelated
      failures, 0 regressions. Migration applied live via a one-off
      `docker compose run --rm api alembic upgrade head` before the
      code swap, same night the frontend itself went live for the
      first time ever.
- [x] **Trader-facing trade story ("why was this trade placed").**
      DONE 2026-08-31, **deployed to the live VPS 2026-09-02.** Real
      traders are about to get access; outcome numbers alone weren't
      enough, and the only page that explained a trade's reasoning
      (`AdminTradeDetail.tsx`) was admin-only. New `GET
      /trades/{trade_id}/event-chain` (`app/core/trade_story.py`,
      `app/core/event_narration.py`) walks the raid -> MSS -> FVG ->
      candidate -> fill -> close chain and narrates it in plain
      English; new `/trades/:tradeId` page. 100% read-only, no schema
      migration, no shadow_runner write-path change. 339 passed (23
      new), 10 pre-existing unrelated failures, 0 regressions. Already
      put to real use the same night it went live -- it's what led to
      discovering the two real bugs above.
- [x] **Write `PHASE3_VALIDATION.md`** (Phase 3 step 9). DONE
      2026-08-31 -- reports the real data: ~5 weeks of clean unattended
      live running, two real autonomous demo trades (one TP, one SL),
      plus the two cold-start bugs found/fixed on first deployment.
      Phase 3 is now complete, all 9 steps.
- [ ] **Multi-user trade fan-out -- designed 2026-09-02, not built.**
      The intended design was always "one shared detection engine per
      model, execution fans out to every subscribed user's own
      account" (`ModelConfig.status` already models per-user
      opt-out) -- but `shadow_runner` is actually hardcoded to exactly
      ONE user via env vars (`SHADOW_RUNNER_USER_ID`/`BRIDGE_URL`).
      Every other user who connects a broker account gets a real,
      independent bridge worker with nothing watching or trading it at
      all. Full design in `MULTI_USER_FANOUT_PLAN.md` (not committed):
      a new subscriber query (`ModelConfig` + `User` + `BrokerCredential`
      join, doesn't exist yet) and widening the handful of places that
      assume a single `OrderManager` per day into a dict keyed by
      `user_id` -- `OrderManager` itself already doesn't need to
      change, it's already correctly one-model-one-user-one-bridge
      scoped. Explicitly scoped to design-only for now; building it,
      and separately cutting the real live account over to it once
      built, are both deliberately deferred to their own later
      go-aheads.
- [ ] **New models beyond `fvg` (`ob`, `fvg_ob`, and any others).**
      Clarified 2026-08-29: the user will bring the actual model
      definitions/specs when ready. The job is then the same shape of
      engineering `fvg` already went through -- reimplement each as a
      bar-by-bar streaming state machine, validate it reproduces the
      reference model's trades exactly (golden-master style), then wire
      it into the same detection -> decision -> real-order pipeline --
      not open-ended strategy research from scratch. No OB-related code
      exists anywhere in this repo yet (confirmed by search), so this
      genuinely starts from zero once the specs arrive.
- [x] **Logging/audit review, part 1: instrument the security gap.**
      DONE 2026-08-30. Audited what was actually in place (the `events`
      table has excellent, disciplined trading-pipeline coverage --
      everything auth/credential/provisioning-adjacent had none). Added
      a new `audit_log` table + `app/core/audit.py`'s `write_audit_log()`
      (mirrors `write_event()`'s discipline), instrumented across
      `auth.py`, `broker_credentials.py`, `internal_bridge.py` (the
      plaintext-credential-fetch endpoint -- success and denial),
      `internal_provisioning.py`, `internal_decommission.py`. 288
      passed, 10 pre-existing unrelated failures, 0 regressions.
      Two follow-ups still open from the same review, tracked below.
- [x] **Logging/audit review, part 2a: Docker Compose + provisioning
      poller log rotation.** DONE -- `docker-compose.yml` now caps
      every service (`db`/`api`/`shadow_runner`/`caddy`) at 10MB x 5
      files via a shared `x-logging` anchor; the provisioning poller
      now writes its own 10MB x 5 rotating file at
      `<bridge_root>/logs/poller.log` instead of relying on whatever
      (if anything) captured its stderr. 299 passed, 10 pre-existing
      unrelated failures, 0 regressions. **Hetzner half deployed
      2026-09-02** (all 4 services recreated with the new logging
      driver, including `db`, as part of that night's full deploy
      pass). **Windows VPS half (the provisioning poller itself)
      still NOT deployed** -- this got left behind that same night; it
      needs its own separate `git pull` + Scheduled Task restart on
      the Windows box, unrelated to anything Hetzner-side.
- [ ] **Logging/audit review, part 2b: NSSM log rotation on Hetzner.**
      Deliberately deferred as its own separately-scheduled step, not
      bundled with 2a -- an earlier NSSM misconfiguration on this exact
      box (`MT5Bridge-Tony`) caused a real live-trading outage this
      project, so this needs a fresh, deliberate go-ahead rather than
      being folded into a routine change. NSSM's `AppRotateFiles`/
      `AppRotateOnline`/`AppRotateBytes` params, applied to whichever
      services run under NSSM there, require an actual service restart
      to take effect. **Discussed again 2026-08-31**: requires (a) an
      explicit go-ahead, not just referencing this list, and (b) the
      user being reachable during the restart in case of a rollback --
      user chose to skip for now rather than commit to that. Revisit
      whenever ready to schedule it deliberately.
- [x] **Logging/audit review, part 3a: `trades`<->`events` FK.** DONE
      2026-08-31. `events.trade_id` (migration 0017), set directly by
      `shadow_runner/runner.py`'s `_write_trade()` -- no more
      re-deriving the fill/close match heuristically every time
      something needs it. `app/routers/admin.py`'s event-chain endpoint
      prefers the FK, falls back to the old heuristic only for
      historical (pre-migration) events; `app/scripts/backfill_event_trade_ids.py`
      backfills those on request (run-by-hand, not yet run anywhere).
- [x] **Logging/audit review, part 3b: shadow_runner structured JSON
      logging.** DONE 2026-08-31. `shadow_runner/main.py` now calls
      `app.core.logging.configure_logging()` instead of its own ad hoc
      `basicConfig()` -- gets `LOG_FORMAT=json` support, previously
      api-only. The provisioning poller could get the same treatment
      cheaply (separate Windows box, but not Tony's live bridge) if
      wanted -- not done yet, just not asked for. `bridge/app/main.py`
      (Tony's live bridge) stays deliberately deferred, same discipline
      as the NSSM item (2b) below.
- [ ] **Logging/audit review, part 3c: role/actor identity.** Still
      deferred as underspecified -- audit records can say WHAT happened
      but not who authorized it beyond a raw user/machine id.
      **Discussed again 2026-08-31**: asked the user what concrete gap
      this would need to close (the audit log already has
      `actor_type`/`actor_id`/`actor_label` -- what's missing beyond
      that isn't obvious without a real scenario). User chose to skip
      -- no concrete need identified. Revisit only if a real scenario
      surfaces (e.g. an admin acting on behalf of a user, or multiple
      credentials per user needing to be told apart in the audit trail).
- [~] **Monitoring/alerting.** Foundation built 2026-08-31
      (`app/core/telegram.py`, `app/core/healthchecks.py`), dormant
      until real credentials exist -- **user needs to**: create a
      Telegram bot via @BotFather, add it to an existing group, get the
      bot token + group chat id; create two healthchecks.io checks (one
      per service: api, shadow_runner), get their ping URLs. Once
      those exist, set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` in `.env`
      and each service's `HEALTHCHECKS_PING_URL` in `docker-compose.yml`
      (commented placeholders already there). Covered so far, built
      incrementally:
        - [x] `safety_check_failed` events -> Telegram alert
        - [x] process/service down -> healthchecks.io dead-man's-switch
              (api: 60s heartbeat via lifespan background task;
              shadow_runner: pinged once per run_forever() loop
              iteration)
        - [x] `order_placement_failed` events -> Telegram alert
              (2026-08-31)
      Not yet built, deliberately skipped 2026-08-31: **missed-trading-
      day alert**. Design tradeoff discussed: this project only tracks
      FOMC dates as known non-trading days, not general market
      holidays, so a naive "zero events by end of day" check would
      false-page on every real market holiday (rare for EURUSD --
      basically just Dec 25/Jan 1 -- but still a false alarm each
      time). Two options on the table when picked back up: (a)
      hardcode the handful of real FX closures so the check is
      accurate, or (b) ship the naive version and accept the occasional
      false page. User chose to skip both for now rather than pick.
- [x] **Secret rotation -- all three done 2026-09-02.** **Postgres
      password**: rotated live (`ALTER ROLE`, no `db` restart needed)
      after it surfaced hardcoded in plaintext in the VPS's
      `docker-compose.yml` during this session's deploy (now uses
      `${POSTGRES_PASSWORD}` substitution like everything else);
      `api`/`shadow_runner` restarted clean with zero downtime.
      **`JWT_SECRET_KEY`**: simple swap + `api`-only restart (confirmed
      `shadow_runner` never touches JWT, no HTTP auth layer); every
      logged-in session was signed out once, expected, no data risk.
      **`CREDENTIALS_ENCRYPTION_KEY`**: the one that needed real care --
      it's the Fernet key encrypting every stored broker credential
      (`broker_credentials`, including the real live account) at rest,
      and a naive swap would have made every existing encrypted row
      permanently undecryptable. Built
      `app/scripts/rotate_credentials_encryption_key.py` first (decrypts
      every row under the old key, re-encrypts under the new one, all
      inside one transaction -- aborts the whole batch, commits nothing,
      if any row fails to decrypt; `--dry-run` proves the old key is
      correct before anything real happens; 5 tests). Then ran it for
      real against the live table (5 rows, dry-run confirmed first),
      updated `.env`, restarted `api`. **Verified live in the browser**
      -- the Live page showed real balance/equity ($48,936.80) and real
      pending orders with real ticket numbers immediately after, proving
      the bridge successfully authenticated using credentials decrypted
      under the new key end-to-end, not just that the key format was
      accepted. 351 passed / 1 skipped / 10 pre-existing unrelated
      failures, 0 regressions.

## Real-money gate (explicitly not started)

- [ ] No slippage/spread/commission modeling anywhere -- material,
      given the strategy's average realized R:R is only ~1.1.
- [ ] Out-of-sample validation never done.
- Phase 4 ends at demo orders; going to real money is a separate,
  explicit go/no-go gated on both items above.

## Lower-priority hardening backlog

- [ ] Rate limiting on `/auth/login`
- [ ] CORS hardening
- [ ] A real secrets manager (vs. `.env` files)
- [ ] Dev/staging/prod environment separation
- [ ] `passlib` deprecation pin (`bcrypt<4.0`)
- [ ] `pydantic` class-based `Config` migration in `app/schemas/auth.py`
- [ ] Cosmetic: a harmless `WinError 64` asyncio traceback on Windows
      (Proactor event loop quirk) -- doesn't crash anything, fixable by
      switching to the Selector event loop
- [ ] The bridge itself has no auth layer -- reachability is gated
      purely by a Windows Firewall IP restriction to the Hetzner box.
      Fine for the current small, fixed set of trusted callers; revisit
      before this expands.
- [ ] Randomized-slice differential testing
- [ ] Per-bar performance profiling
