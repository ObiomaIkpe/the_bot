# Phase 3 Validation — Shadow Mode (Live Data, Autonomous Journaling)

Step 9 of Phase 3's 9-step plan (see `HANDOFF.md`'s "Phase 3 progress"
for steps 1-8). Step 8 (run live, validate) now has real live output to
report on — this document is that report.

## Scope

Phase 3 builds the shadow runner (`shadow_runner/`): a persistent,
unattended process that polls the Phase 2 bridge for real M5 bars,
drives the exact same streaming detection pipeline Phase 1 proved
reproduces the locked batch model bar-for-bar (`PHASE1_VALIDATION.md`),
and journals every detection/decision event durably to Postgres. Phase 3
itself is journal-only by design — whether a detected trade becomes a
real broker order is Phase 4's concern (`ModelConfig.status`), not
this runner's.

## What was built (steps 1-7)

1. `api` turned into a real, persistent `docker-compose` service on
   Hetzner.
2. Network path from Hetzner to the VPS bridge, across a real dedicated-IP
   migration (`PHASE2_VALIDATION.md`'s addendum).
3. `DaySelectionGate` (`phase1/streaming/day_selection_gate.py`) as real
   code: FOMC exclusion, trend determination, 5am-5pm NY session
   windowing.
4. Closed the event-type gap — `VALID_EVENT_TYPES` extended to cover
   every event the streaming pipeline can actually emit, plus
   `events.is_shadow` added ahead of Phase 4.
5. The shadow runner itself (`shadow_runner/`) — `DayOrchestrator`
   waits until 10am's bar data actually exists before constructing
   (its `session_end_idx` isn't knowable earlier), then backfills the
   whole morning in one pass.
6. Mid-day-restart recovery — full design and two real bugs found on
   first deployment documented separately in `PHASE3_RESTART_RECOVERY.md`
   (summarized below, since they're central to this validation).
7. Deployed as its own container, same image as `api`, different
   `command:`.

## Real bugs found and fixed on first live deployment

The first live deployment happened to cold-start on a weekend (market
closed since Friday) — a timing accident that surfaced two real bugs
neither design review nor the unit tests caught. Full detail in
`PHASE3_RESTART_RECOVERY.md`'s addendum 2; summarized here since this
is exactly what step 8 (run live, validate) exists to catch:

1. **The cold-start trend bootstrap's own marker event was tricking the
   "did today already start" check into a false positive on every
   startup**, seconds after the container came up. Fixed by having
   `get_last_event_timestamp_for_date()` explicitly skip
   `trend_history_bootstrapped` rows.
2. **Cold-starting when the bridge's most-recent bars belong to an
   already-finished day** (Friday's tail end, seen on a Sunday)
   produced a misleading `insufficient_bars` verdict on a day that was
   never going to be journaled properly anyway, instead of just waiting
   for genuinely current data. Fixed by discarding stale bars on true
   cold start, before any `CurrentDay` gets constructed.

Both fixed and confirmed via real logs on Hetzner the same session, not
just re-run unit tests. Since then: no further bugs, no manual
intervention needed.

## Verification — unattended live running + two autonomous real trades

**As reported 2026-08-29**: ~5 weeks of clean, unattended live running
since the two bugs above were fixed. `CurrentDay` construction, the
10am decision, and backfill have all run correctly against genuinely
live bars for five consecutive weeks, unattended — not just engineered
test data or short-lived manual observation.

During the most recent week of that run, `fvg`'s `ModelConfig.status`
was `active` in production (a Phase 4 setting, not a Phase 3 one) — so
the two trade candidates the shadow runner detected that week went
straight to real (demo) orders through the bridge rather than
journal-only entries:

| Trade | Detection | Order placement | Fill | Close |
|---|---|---|---|---|
| 1 | Autonomous, zero manual intervention | Real demo order via `bridge/app/main.py`'s order endpoints | Filled | Closed at **take-profit** |
| 2 | Autonomous, zero manual intervention | Real demo order via `bridge/app/main.py`'s order endpoints | Filled | Closed at **stop-loss** |

Both outcome paths (`shadow_runner/order_manager.py`, "Phase 4 step
2c") exercised under real autonomous operation, not just the 37 unit
tests that covered this before — see `HANDOFF.md`'s Phase 4 entry for
the full pipeline this validates end-to-end (detection → order → fill
→ TP/SL close).

**What n=2 does and doesn't say:** this confirms the *mechanical*
question step 8 was actually asking — that the pipeline built in steps
1-7 works correctly against real, live, unattended conditions, both
win and loss paths included. It says nothing about the strategy's
statistical edge; that's explicitly deferred to the real-money gate
(`PENDING_ITEMS.md`'s "Real-money gate" section — out-of-sample
validation and slippage/spread modeling, neither done yet, both
required before any of this trades real money).

## Status: Phase 3 complete

All 9 steps done. Ready for Phase 4's own validation to build on this
(designated-live-model real order placement — already live and
reported in `HANDOFF.md`, not a separate phase gate here).

## Carried-forward gotchas / open items

- **No recovery for anything before "today."** If the runner is down
  across a day boundary, that day's activity is simply never
  journaled — only "today, in progress" ever gets recovered. Unchanged
  since `PHASE3_RESTART_RECOVERY.md` was written; five weeks of clean
  running means this has not yet been exercised for real.
- **No test against a real Postgres instance for the recovery logic
  itself** (`tests/shadow_runner/test_shadow_runner_recovery.py` and
  friends use a fake in-memory DB stand-in). The real proof is this
  document — five weeks of live behavior against Hetzner's actual
  database — not a new test suite.
- **Bridge authentication still deferred** — reachability is gated
  only by a Windows Firewall IP restriction to the Hetzner box, not
  anything the bridge itself checks. Acceptable for the current
  single-consumer setup; revisit before this expands beyond a small,
  fixed set of trusted callers.
- **Partially closed since this report**: `PHASE3_RESTART_RECOVERY.md`
  originally flagged "no persistent heartbeat/health check to detect a
  stuck-but-not-crashed process" as explicitly not built. The
  monitoring/alerting work in `PENDING_ITEMS.md` (`app/core/healthchecks.py`,
  pinged once per `run_forever()` loop iteration) now covers exactly
  this case — a hung iteration simply stops pinging, and
  healthchecks.io pages on the stale check. Dormant until real
  credentials are configured (see `PENDING_ITEMS.md`'s monitoring/alerting
  entry); not yet exercised against a real hang.
