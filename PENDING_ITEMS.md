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

- [x] **Historical reconciliation, Piece B: real trade reconciliation
      against the broker.** DONE, deployed, and verified live
      2026-09-05. New `GET /history/deals` bridge endpoint (MT5's real
      `history_deals_get(date_from, date_to)`, not exposed before now
      -- only per-ticket lookup existed). New
      `shadow_runner/historical_reconciliation.py` correlates each
      closed deal against Piece A's replayed narrative -- a match gets
      a full `trades` row (candidate's own simulated entry/stop, real
      numbers in `real_*` columns -- never fabricated), a non-match is
      journaled honestly with no invented row (`stop_price`/
      `target_price` are NOT NULL with no honest value for an unmatched
      deal). Driver script (`shadow_runner/scripts/reconcile_deals_aug10_sept4_2026.py`)
      is dry-run by default.

      **Result, run live 2026-09-05**: 15 raw deals fetched (Aug 10 ->
      Sept 4), 4 real closed positions reconciled -- including BOTH
      known Aug 27/28 sibling-race incident trades (ticket 3147397442,
      +498.30; 3147397683, -490.75) plus two Sept 4 trades (+2039.28,
      +2027.56). Verified directly against the DB after commit, not
      just the script's own claim: `entry_price` correctly holds the
      simulated candidate number (not the real fill price -- this is
      what makes `build_trade_chain()` able to resolve these trades'
      stories at all), real profit/close values match exactly what the
      user confirmed against their own MT5 account.

      **Two real bugs found and fixed during live validation** (a dry
      run reported "0 matched" for trades known to be real -- not
      accepted, investigated with a diagnostic script querying the
      real DB directly): (1) closing deals (a stop-loss/take-profit
      hit OR a manual close -- confirmed both, live) very often report
      `magic: 0` even though the position itself was opened by the EA
      with the real magic number -- only the opening deal reliably
      carries it; the original design filtered the whole deal list by
      magic before pairing, silently dropping every real closing deal
      like this. (2) `trade_candidate_ready` events from BEFORE the
      multi-user fan-out convention change (e.g. the actual Aug 27
      sibling-race candidates) carry a real `user_id`, not NULL --
      filtering on `Event.user_id.is_(None)` excluded exactly the
      historical rows this tool exists to reconcile against. Both
      fixed with regression tests. Full suite (pytest -n auto): 10
      pre-existing failures (unchanged), 444 passed, 1 skipped.

      A third real issue was caught and fixed BEFORE ever running live:
      the driver script's first draft used `config.bridge_url` (the
      shared REFERENCE bridge, detection's price feed only) instead of
      the real trading account's own bridge -- would have silently
      queried the wrong account's (empty) deal history and looked like
      a clean "nothing to reconcile" result instead of an obviously
      wrong query target.

## Real bug found 2026-09-06 (duplicate real order placement -- root cause deliberately NOT fixed, by explicit user decision)

Found while explaining a chart to the user: two real trades on 27 Aug
2026 (tickets `3147397442` +498.30, `3147397683` -490.75) landed in
the *same* account with identical entry/stop/direction and near-
identical timestamps -- not the multi-user fan-out (only one real
account existed then or now), and not a coincidence. Traced through
the actual production `events` table (user shared prod DB credentials
directly for this -- **see rotation note below**), then confirmed
against the current `shadow_runner/order_manager.py` source, not
assumed from memory.

**Root cause, confirmed still present in current code**:
`on_trade_candidate_ready()`'s only duplicate guard is
`candidate_key = (event["raid_bar"], event["mss_bar"])`. The Aug 27
incident's two `trade_candidate_ready` events had *different* raid
bars (42 and 43) that both resolved to the identical entry/stop/
direction -- the guard doesn't recognize "this would place an
economically identical order," only "this exact raid+MSS pair already
happened." Both got submitted to the broker as two separate real
pending orders.

**Downstream, a second real gap**: when order `3147397442` filled, the
already-fixed sibling-cancel logic (`_handle_sibling_cancel_failure()`,
2026-09-02) tried to cancel `3147397683` -- the broker itself rejected
the cancel (`retcode=10013, comment='Invalid request'`), and since the
sibling hadn't filled *yet* at that exact instant, the code correctly
took the "just a cancel failure" branch (logs `cancel_sibling_order`
and stops) rather than the "sibling already filled" branch it's built
to catch. Nothing retries the cancel or re-checks that order later.
`3147397683` filled and closed for real with **zero events between
placement and reconciliation a day later** -- the live app never saw
it happen. (This is one of the two original "invisible trades" that
started the whole historical-reconciliation effort above -- now fully
explained, not just recovered after the fact.)

**Decision, made explicitly by the user**: don't fix the root cause --
accept that a duplicate order can still occur, and make sure that when
it does, it's always tracked and never silently forgotten, rather than
preventing it outright.

- [x] Confirmed how much of "always tracked" is **already true today**:
      the continuous orphan-position safety net (live since 2026-09-04,
      polls every 5 minutes) doesn't care *why* an unmatched real
      position exists -- it would catch a repeat of this within one
      polling cycle, alert on it, attempt to heal it, and write a
      permanent trade record the moment it's found. Real trades here
      run in hours, not minutes, so this covers the realistic case.
- [ ] **One residual gap, narrower but real**: a duplicate order that
      both fills *and* fully closes within a single 5-minute polling
      window wouldn't be caught by anything standing today -- the only
      thing that recovers a trade that fast is the historical-
      reconciliation script above, and that's a **manual, one-off run**,
      not a recurring job. Proposed fix (documented here per the user's
      request, not built): turn
      `shadow_runner/scripts/reconcile_deals_aug10_sept4_2026.py`'s
      approach into a small **scheduled recurring job** (e.g. nightly)
      against a rolling recent window, rather than a fixed historical
      date range run by hand -- would make "always tracked" a durable,
      standing guarantee instead of depending on someone remembering to
      re-run a script.

**Credential note**: the user shared production Postgres credentials
directly in chat to run the diagnostic query for this investigation
(`DATABASE_URL` with the `db_user`/password pointed at
`db:5432/trading_bot` -- only reachable from inside the VPS's own
Docker network, not from outside it). Per the user's own instruction,
these need to be **rotated** once this investigation is done, same
discipline as the 2026-09-02 secret rotation above.

## Dashboard data-consistency audit and fixes 2026-09-06

Prompted by a tester's bugsheet ("win rate doesn't make sense," "no
metric showing time closed/risk %/lot size," "missing exit prices,"
"incorrect outcome data," "real status of very first trade isn't
visible"). Full commit range: `72a60e0..4fed27e`.

**Root pattern behind almost every item**: two trade-writing code
paths -- `write_orphan_trade()` and `write_reconciled_historical_trade()`
(see the historical-reconciliation entries above) -- deliberately never
populate the SIMULATED side of a `Trade` row (`exit_price`, `outcome`,
`realized_r`), since neither ever went through same-day simulated
grading. Every page that only ever read the simulated field showed a
blank/wrong value even though the REAL side (`real_close_price`,
`real_status`, `real_profit`, etc.) was fully populated right next to
it. Fixed with a set of `resolve*()` helpers in `frontend/src/lib/pnl.ts`
(`resolveOutcome`, `resolveExitPrice`, `resolveCloseTime`,
`resolveRealizedR`, `resolveRealStatusLabel`) that prefer the real
value whenever the simulated one is missing -- applied consistently
across `TradeHistory.tsx`, `ModelDetail.tsx`, `AdminTrades.tsx`,
`TradeDetail.tsx`, and `AdminTradeDetail.tsx`.

**Why this is durable going forward, not a one-time patch**: a trade
caught live by a normally-running bot always gets both sides written
together by `write_trade()` in one shot -- the gap only ever exists for
a trade recovered after some abnormality (bot downtime, a missed fill).
The `resolve*()` helpers are general-purpose, not tied to specific
historical trades, so if orphan-recovery/historical-reconciliation ever
writes another such trade in the future, every page already displays
it correctly with no further intervention needed.

- [x] **Win rate / outcome nonsensical.** Already fixed earlier the
      same day (`e80a7a8`, `3a009f1`), before this bugsheet arrived --
      `summarizeTrades()`/`resolveOutcome()` now count a real, closed
      orphan/reconciled trade correctly instead of excluding it.
- [x] **Missing exit prices.** `resolveExitPrice()` -- falls back to
      `real_close_price`. Also caught and fixed a second time
      (user found it live via screenshot) on `TradeDetail.tsx`
      specifically -- that page had been deliberately left on the raw
      field, reasoning the simulated/real split made a fallback
      unnecessary; wrong in practice; fixed to match every other page.
- [x] **No close-time metric.** `Trade.real_close_time_ny` existed on
      the DB model but was never exposed through `TradeOut`/
      `AdminTradeOut` at all -- added end to end (schema, frontend
      type, new `resolveCloseTime()`, new "Closed" column).
- [x] **No risk % metric.** Was already returned by the API
      (`risk_pct_used`) but never rendered -- added a column.
- [x] **Real status of the very first trade (06/08/2026, predates the
      real account) invisible.** First attempt used `is_shadow` to
      decide when to show "no real order" -- **wrong**, verified live:
      `is_shadow` only reflects whether the model's config was
      'active' at decision time, not whether a broker existed to place
      a real order (this trade has `is_shadow=false` despite never
      having a real order, since the model was configured active
      before the account existed). Fixed to key off actual real-order
      data (`real_fill_price`/`real_close_price` both null) instead.
- [x] **Outcome contradiction on the trade-story detail pages**
      ("Outcome: open" shown directly beside "Status: closed,
      +$2039.28"). Root cause: these two pages read the raw `outcome`
      field instead of `resolveOutcome()`. Fixed on both trader-facing
      and admin versions.
- [x] **Floating-point noise in displayed prices** (`Stop:
      1.1577600000000001`). New `formatPrice()` helper
      (`frontend/src/lib/format.ts`, rounds to 6 decimals) applied to
      every price field on both detail pages and both list tables.
- [x] **Realized R blank for a reconciled trade.** No `real_realized_r`
      column exists to fall back to -- derived instead as
      `real_profit / (equity_before * risk_pct_used)`, the same
      relationship `write_trade()` uses in reverse. Needed exposing
      `equity_before` through the API (existed on the model, `NOT
      NULL`, never returned -- same schema-parity gap as
      `real_close_time_ny` above).
- [x] **"(NY)" column-header labels removed** ("Entry (NY)" ->
      "Entry time", "Closed (NY)" -> "Closed") after the user checked
      the displayed time against their actual Exness account and found
      it already matches broker time -- the label was inaccurate, not
      the underlying value, so only the header text changed.
- [x] **Full production DB column audit**, prompted by the user asking
      "what other data am I not seeing." Every column on all 10 real
      tables checked against what the API actually returns:
      - Surfaced (real, populated, non-sensitive, previously hidden):
        `real_position_ticket` (lets a user cross-reference a trade
        against their own Exness terminal directly), `real_fill_time_ny`,
        `equity_before`, and on `broker_credentials`:
        `provisioning_account_label`/`provisioning_claimed_at`.
      - Checked and found NOT worth building: the `notifications`
        table has zero rows and nothing in the codebase ever writes to
        it (the real Telegram alert path bypasses it entirely) --
        would only ever show permanent emptiness.
        `provisioning_machines` is pure VPS/ops infrastructure
        bookkeeping, not trading data.
      - Checked for a live surprise (partial closes -- a real position
        still open at 5pm NY should half-close automatically per
        `position_tracker.py`, invisible on the dashboard if it had
        ever happened): confirmed via direct query, **zero trades have
        ever had a partial close.** Nothing hidden there today.
- [x] **P&L chart redesign** (`72a60e0`, `a2fb11c`) -- diverging
      per-trade bar chart (win/loss shown individually, not just a
      blended cumulative line) alongside the existing running-total
      line; x-axis keyed on trade sequence number instead of raw date
      so two same-day trades (27/08, 02/09 sibling pairs) never
      collide/crowd on the axis. Live rendering bug (tallest bars
      overflowing their fixed-height box, found via screenshot) fixed
      same night by giving the hidden Y-axis an explicit domain.
- [x] **Built, then explicitly reverted on request**: a "Running
      equity" column/field (`4a21c53`) that reconstructed a genuine
      running account-equity chain per trade (since `equity_after` is
      only ever populated for a trade that went through the normal
      pipeline -- every orphan/reconciled trade leaves it null,
      producing the confusing "current equity shows up on the OLDEST
      trade's row" bug the user caught live). The user asked to remove
      it entirely (`96abadb`) rather than keep it -- **worth knowing
      before rebuilding this**: the derivation logic
      (`buildRunningEquity()`, deleted) was correct and tested (walks
      trades in true chronological close-time order, cascades real
      profit forward), the removal was a product decision, not a bug
      in the feature itself.

**Known gaps NOT fixed, still open:**
- [ ] **Lot size / trade volume.** Confirmed via code search: not
      persisted anywhere in the schema (not on `Trade`, not in any
      journaled `Event.details`). Genuinely needs a migration + backend
      capture change, not a display fix -- explicitly not attempted
      without a separate go-ahead.
- [x] **`setup_context` (Trend / Risk in pips) only shown on the
      trader-facing `TradeDetail.tsx`, not `AdminTradeDetail.tsx`.**
      Fixed 2026-09-06 (commit `fcd677f`): mirrored the same conditional
      Trend/Risk(pips) rows onto `AdminTradeDetail.tsx`. Frontend-only,
      not yet deployed to the live VPS.

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

## Formerly "deliberately deferred," now done

- [x] **Windows auto-logon / reboot survival for the VPS.** DONE and
      verified live 2026-09-05 with a REAL reboot (a pending Windows
      Update install, not a synthetic test). Sysinternals `Autologon.exe`
      configured for the `Administrator` account (encrypted via LSA
      secrets, not the raw registry method's plaintext password).
      Startup-folder shortcuts added for BOTH MT5 terminals (`MT5-Tony`
      and `MT5-6cf5919a`, the reference account) -- neither had one
      before, confirmed via an empty Startup folder. Both NSSM bridge
      services already had `SERVICE_AUTO_START`. **Confirmed end to
      end**: box came back with no login prompt at all, both MT5
      terminals open and already logged into their accounts, both
      bridge health checks (`/health` on 8001 and 8002) showed
      `connected: true` immediately, all without anyone touching the
      keyboard. Real snag hit along the way: the reference account's
      actual config lived at `C:\bridge\bridge\accounts\6cf5919a\
      config.json` (the NEW checkout), not `C:\bridge\accounts\...`
      (the old one, which turned out to hold the already-known orphan
      `05315ccf` config instead) -- found via
      `nssm get bridge-6cf5919a AppEnvironmentExtra`'s own
      `BRIDGE_CONFIG_PATH`, not guessed from folder-naming convention.
      **Security tradeoff, stated plainly**: auto-logon means anyone
      with console/RDP access to this box now gets an already-logged-
      in desktop, no password prompt -- accepted given the alternative
      (a stranded, non-recovering bridge after any reboot) but a real
      tradeoff, not a free fix. Full setup recipe in `OPS_COMMANDS.md`.

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
      **2026-09-05: this groundwork was actually already done once**
      (2026-08-31, `NEW_MODEL_GUIDE.md`, not committed to git) --
      exactly "check how `fvg` was wired + how to prepare/integrate
      other models," already written up in detail: what's universal
      regardless of a new model's nature (output contract, registration,
      one-process-per-unit, shadow-mode proving period, some form of
      pre-live validation) versus what was specific to `fvg`'s own
      shape (a locked reference implementation + golden-master
      bit-for-bit reproduction -- only applies if a future model is the
      *same kind* of thing). **Re-verify it's still accurate before
      relying on it** -- a lot has changed since Aug 31 (the whole
      multi-user fan-out engine didn't exist yet); the core points look
      still-correct on a re-read, but weren't re-checked line-by-line
      against the current codebase for this pass. Still genuinely
      blocked on the same thing: real model specs from the user.
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
      was still dormant at the time. **healthchecks.io half: DONE,
      live 2026-09-05.** Two checks created ("the bot - api" 8min
      period/6min grace, "bot-shadow-runner" 10min/5min -- tightened
      down from an initial 1-hour grace that would have delayed a real
      alert by over an hour). `docker-compose.yml` reworked to
      interpolate each service's URL from `.env`
      (`HEALTHCHECKS_PING_URL_API`/`HEALTHCHECKS_PING_URL_SHADOW_RUNNER`)
      rather than hardcoding the real URL into the tracked file --
      matches the `${POSTGRES_PASSWORD}` pattern already used
      elsewhere in that file. Both checks confirmed green with real
      pings landing after deploy.

      **Self-caused outage, same day, caught and fixed within
      minutes**: this setup added `HEALTHCHECKS_PING_URL_API`/
      `HEALTHCHECKS_PING_URL_SHADOW_RUNNER` to `.env.example`/`.env`
      without declaring either on `app.core.config.Settings` --
      `env_file: ./.env` loads `.env` wholesale into every service, and
      `Settings` rejects any var it doesn't know about. Both `api` and
      `shadow_runner` crashed on the next full restart (two deploys
      later, not immediately -- never fully root-caused why the first
      restart didn't also crash). Fixed by declaring both as optional
      fields, matching the existing `postgres_password`/`bridge_url`
      pattern. New regression test (`tests/app/test_config.py`) catches
      this whole class of bug for any future `.env.example` addition --
      verified the test genuinely fails against the broken code, not
      just passes against the fix. See
      `settings_extra_forbidden_outage_2026_09_05` memory for the full
      writeup, including a false-positive mistake in the test's own
      first draft.

      Covered so far, built incrementally:
        - [x] `safety_check_failed` events -> Telegram alert
        - [x] process/service down -> healthchecks.io dead-man's-switch
              (api: 60s heartbeat via lifespan background task;
              shadow_runner: pinged once per run_forever() loop
              iteration) -- wired to real check URLs and verified live
              2026-09-05
        - [x] `order_placement_failed` events -> Telegram alert
              (2026-08-31)
        - [x] `orphan_position_recovered`/`orphan_trade_recorded` ->
              Telegram alert (2026-09-04)
        - [x] Real trade ACTIVITY, not just failures, added 2026-09-05
              on user request: `pending_order_placed`, `candidate_filled`,
              `real_trade_closed` (💰/📉), `daily_loss_threshold_crossed`,
              `manual_close_requested`/`manual_cancel_requested`. Found
              and fixed two more real gaps while wiring these in --
              same class as the write-path audit, just missed spots:
              a real trade closing naturally (position_tracker.py's
              `_handle_vanished()`) and the manual close/cancel API
              endpoints both journaled successfully but never actually
              called `alert_for_event()` on the success path, only on
              a journal-failure fallback. 16 new/updated tests.
        - [ ] **Alerts show the raw user_id UUID, not a human-readable
              label.** Found 2026-09-05 while verifying multi-account
              correctness (confirmed every alert message DOES include
              `user={user_id}`, so multiple simultaneous accounts would
              never produce an ambiguous message -- just not a readable
              one). Fine with one real account; would get confusing to
              read at a glance once a second real account (the pending
              friend's account, still blocked on their credentials)
              goes live. Fix: look up and show the user's email instead
              of/alongside the raw UUID in `alert_for_event()`.
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
