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
      pre-existing unrelated failures, 0 regressions. **Confirmed
      deployed and live as of 2026-09-04** (verified via
      `git merge-base --is-ancestor 6cb5591 87836bf`, the last commit
      independently confirmed deployed).
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
      failures, 0 regressions. **Confirmed deployed and live as of
      2026-09-04** (verified via `git merge-base --is-ancestor 86c76f5
      87836bf`, the last commit independently confirmed deployed).
      Full historical reconciliation of already-closed trades (the
      bridge-endpoint version) remains a known, documented, deliberate
      gap -- revisit separately if it still matters now that this is
      live. **Update 2026-09-04**: the user asked to actually recover
      the wider Aug 10 -> Sept 4 window (not just this incident's two
      days), which turned into a full new plan (see below) -- Piece A
      of it is now done.
- [x] **Historical reconciliation, Piece A: deep narrative-only replay
      back to Aug 10, 2026.** DONE and deployed 2026-09-04. Plan:
      `misty-seeking-crescent.md`'s "Historical reconciliation -- Aug
      10 through Sept 4, 2026" section. `_replay_historical_day()`'s
      existing bar-fetch was capped at one 5000-bar `/candles` call
      (~17 trading days back from whenever it runs) -- not enough to
      reach a 25-day-old gap. Added an optional `start_pos` param to
      `/candles` (`bridge/app/main.py` + `mt5_client.py` -- MT5's real
      `copy_rates_from_pos` already supports this, the bridge just
      hardcoded 0) plus client-side pagination
      (`BridgeClient.get_candles_paginated()`), then a new one-off
      script (`shadow_runner/scripts/backfill_narrative_aug10_sept4_2026.py`,
      same precedent as `heal_orphans_2026_09_04.py`) reusing the
      already-tested, unmodified `_replay_historical_day()`/
      `_decide_day(historical=True)` guard -- structurally incapable of
      placing a real order or writing a `trades` row, narrative only.
      12 new tests. Full suite: 10 pre-existing failures (unchanged),
      434 passed.

      **Deploy hit real, separate problems along the way, all
      resolved**: (1) the bridge box's git checkout (`C:\bridge`, sparse
      to `bridge/`) had a stuck, uncommitted, years-stale tracked file
      (`app/main.py`, the backend's real entrypoint, never meant to be
      tracked on this box at all) blocking `git pull` outright --
      resolved via `git rm --sparse` + a local-only merge-resolution
      commit (never pushed, this box only pulls). (2) The Hetzner
      `.env`'s `BRIDGE_URL` (port 8002) turned out to be a legitimate,
      deliberately-provisioned dedicated reference account
      (`476781537`) the user had already set up separately -- NOT the
      real trading account (`476123801`, port 8001, `MT5Bridge-Tony`)
      -- initially misdiagnosed as a misconfiguration, corrected once
      the user clarified. (3) Two separate NSSM services
      (`MT5Bridge-Tony` port 8001, `bridge-6cf5919a` port 8002) both run
      from the same `C:\bridge\bridge` checkout -- both needed their
      own explicit restart to pick up the code change; only restarting
      one left the other silently still running the old code (caught
      via a live smoke-check showing identical `start_pos=0` and
      `start_pos=5000` results, not assumed).

      **Backfill run result, verified against the live DB directly, not
      just the script's own claim**: 18 days replayed (3 correctly
      identified as weekend gaps -- Aug 15/22/29, all Saturdays), 7
      already covered by ordinary live polling. `SELECT COUNT(*) FROM
      trades WHERE ... AND is_shadow = false` for the window returned
      0 (confirms no real trade was ever created, structurally
      impossible either way). 566 real narrative events landed,
      spanning exactly 2026-08-10 05:15 UTC -> 2026-08-28 14:00 UTC;
      sampled content directly (real swing-high/low prices, correct
      chronological order) rather than trusting row counts alone.

      **Piece B (real trade reconciliation -- actual fills/exits/
      profit against the broker for this same window) is NOT done** --
      needs a new bridge date-range deals endpoint, its own dedicated
      live-VPS validation session, deliberately not started this pass.
      See the plan doc for the full design.

## Quick cleanup (low effort, low risk)

- [ ] **Delete the pre-cutover backup file on the VPS**
      (`config.json.v1....bak`) -- still has a **plaintext MT5
      password** sitting in the scratchpad. Higher urgency than the
      other cleanup items below.
- [ ] Delete the orphan `C:\bridge\accounts\05315ccf\config.json` --
      skews `_next_free_port` to 8003+, otherwise harmless.
- [ ] Delete the now-unused old `C:\bridge\app\`/`C:\bridge\scripts\`/
      `C:\bridge\venv` once the new `C:\bridge\bridge` checkout has run
      stable for a few days. **Partially forced 2026-09-04**: this old
      `app/` directory turned out to still be a genuinely (if stalely)
      git-tracked path at the repo root, blocking `git pull` outright
      on the bridge box -- `app/main.py` specifically was removed (via
      `git rm --sparse` + a local merge-resolution commit) to unblock
      a deploy. The rest (`config.py`/`models.py`/`mt5_client.py`/
      `config.json`/`scripts/`/`accounts/`/`logs/`) is untouched,
      still untracked, still pending this same cleanup.

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
- [x] **Multi-user trade fan-out -- BUILT and tested 2026-09-03,
      NOT yet deployed to the live VPS.** The intended design was
      always "one shared detection engine per model, execution fans out
      to every subscribed user's own account" (`ModelConfig.status`
      already models per-user opt-out) -- `shadow_runner` was hardcoded
      to exactly ONE user via env vars. Built across three commits:
      `get_active_subscribers()` (`5710f87`); `Event.user_id`/
      `Trade.user_id` both made nullable, migrations 0020/0021
      (`9ec2a14`) so the shared narrative and the model's own always-on
      shadow trade record have a genuinely ownerless home; `OrderManager`/
      `PositionTracker` both widened to one-per-subscriber (`12c151d`)
      -- the `PositionTracker` widening was a real gap found mid-build,
      not in the original design (without it, real orders would have
      fanned out correctly while overnight risk management silently
      kept working for only one account). 396 passed / 1 skipped, 0
      regressions. Full design + build notes in
      `MULTI_USER_FANOUT_PLAN.md`, plain-language explanation in
      `MULTI_USER_FANOUT_BUILD_EXPLAINED.md` (neither committed).
      **Important**: no feature flag gates this -- the moment it's
      deployed, any user with an active `ModelConfig` + working bridge
      starts receiving real trades automatically. Still ahead before
      the real account cuts over: the deployment-model shift (one
      container per model), the 2-week journal-only rollout acceptance
      criteria, and the admin UI's nested per-subscriber trade story
      (deliberate fast-follow).
- [x] **Dedicated price-only reference account -- DONE, live
      2026-09-04.** Previously one single real account did double duty:
      supplied detection's price feed (`BRIDGE_URL`) AND placed real
      trades. Built a genuinely separate account (Exness demo, its own
      dedicated user `reference-feed@ihusale.com.ng`, provisioned
      end-to-end via self-service) whose only job is supplying prices --
      doubly protected from ever placing a real order (`orders_enabled:
      false` on its bridge worker, AND every `ModelConfig` left at the
      default `disabled`). `BRIDGE_URL` cut over live from the real
      account's own bridge (port 8001) to this one (port 8002),
      confirmed via the running container's actual environment and a
      clean same-day bar replay against the new feed. Two real things
      found and fixed along the way: a raw, unfriendly 403 error on the
      Live page (the bridge gates GET /positions/pending-orders behind
      the same orders_enabled switch as real order placement -- now a
      proper 409 + friendly frontend message, fixes this for every
      account, not just this one) and `BRIDGE_URL`/
      `SHADOW_RUNNER_USER_ID` being hardcoded directly in
      `docker-compose.yml` (moved to `.env`, required a scoped `api`
      rebuild -- `shadow_runner`'s own still-undeployed fan-out image
      deliberately left untouched). Full account in
      `DEDICATED_REFERENCE_ACCOUNT.md`.
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
      (`app/core/telegram.py`, `app/core/healthchecks.py`).
      **Telegram half: DONE, live 2026-09-04.** Bot created via
      @BotFather, added to a group, `TELEGRAM_BOT_TOKEN`/
      `TELEGRAM_CHAT_ID` set in `.env` on the live server, restarted,
      verified with a real end-to-end test message actually landing in
      the group (`app/scripts/test_telegram_alert.py`) -- not just
      "configured," genuinely confirmed working. Real, direct
      motivation for finally doing this: the 2026-09-04 incident (see
      `SEPT_4_DEPLOY_AND_INCIDENT.md`) where every relevant failure got
      journaled to the database but nothing paged anyone, since this
      was still dormant at the time. **healthchecks.io half: still not
      done** -- needs two checks created (one per service: api,
      shadow_runner), their ping URLs set in `.env`, and uncommented in
      `docker-compose.yml`. Covered so far, built incrementally:
        - [x] `safety_check_failed` events -> Telegram alert
        - [x] process/service down -> healthchecks.io dead-man's-switch
              (api: 60s heartbeat via lifespan background task;
              shadow_runner: pinged once per run_forever() loop
              iteration) -- built, not yet wired to real check URLs
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
