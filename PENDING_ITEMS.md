# Pending items

A consolidated punch list of everything still open across the project,
as of 2026-08-29. `HANDOFF.md`'s "Open items" section has the full
narrative/context behind each of these; this file is the condensed,
actionable view. Update this alongside `HANDOFF.md` when an item's
status changes -- don't let the two drift.

---

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

- [x] **Write `PHASE3_VALIDATION.md`** (Phase 3 step 9). DONE
      2026-08-31 -- reports the real data: ~5 weeks of clean unattended
      live running, two real autonomous demo trades (one TP, one SL),
      plus the two cold-start bugs found/fixed on first deployment.
      Phase 3 is now complete, all 9 steps.
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
      unrelated failures, 0 regressions. Requires `docker compose up -d`
      per VPS service to actually take effect (not hot-reloadable) --
      not yet applied on the live VPS, just committed/pushed here.
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
- [ ] **Secret rotation** -- `JWT_SECRET_KEY`, `CREDENTIALS_ENCRYPTION_KEY`,
      the Postgres password. Long-standing, never done.

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
