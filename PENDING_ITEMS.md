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

- [ ] **Write `PHASE3_VALIDATION.md`** (Phase 3 step 9) -- now has real
      data to report on: ~5 weeks of clean unattended live running, two
      real autonomous demo trades this week (one take-profit, one
      stop-loss).
- [ ] **Clarify scope for `ob`/`fvg_ob` models.** Does an Order Block
      batch/reference model already exist somewhere outside this repo
      (the same starting point `fvg` had via the external, locked
      `FVG_model.py`), or does this need strategy research from
      scratch? No OB-related code or research exists anywhere in this
      repo today -- confirmed by search. This answer changes the scope
      from "port an existing proven strategy" to "research and backtest
      a new one" entirely.
- [ ] **Logging/audit review.** What's actually in place today (the
      `events` table, `admin_dashboard/`, where Hetzner/VPS logs land)
      hasn't been audited end-to-end -- do that pass rather than
      assuming current coverage is sufficient now that real orders are
      flowing.
- [ ] **Monitoring/alerting.** Nothing currently pages a human if the
      bot stops running, misses a trading day, or an order fails to
      fill -- everything today is checked by someone looking manually.
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
