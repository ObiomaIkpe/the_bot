# Phase 3 Step 6 — Restart Recovery: What's Actually Covered

This document exists because Option B (real recovery, not just
documenting a gap) was chosen deliberately over the simpler Option A. If
what's built here ever proves insufficient, this is where to start
reading before changing anything.

## The two things that get lost on a restart, and how each is handled

### 1. Trend history (DaySelectionGate's confirmed swing highs/lows)

**Lost on restart:** `DaySelectionGate` keeps the last several confirmed
daily swing highs/lows in memory (`_confirmed_highs`/`_confirmed_lows`),
built up over days via `on_day_closed()`. A restart wipes this back to
empty, and normally it takes several real trading days to rebuild
naturally (the underlying `DailySwingDetector` has a built-in
confirmation delay).

**What's built:** `get_recent_swing_history()` in
`shadow_runner/persistence.py` reads the last 2
`daily_swing_high_confirmed` and last 2 `daily_swing_low_confirmed`
events already sitting in the `events` table (written by prior runs),
and `DaySelectionGate.seed_trend_history()` loads those straight back
in. This happens on every startup, unconditionally, before anything
else.

**Why this is safe:** it's a pure read — nothing gets written twice, so
there's no duplication risk here at all, unlike the session-replay case
below.

**What this does NOT restore:** `DailySwingDetector`'s own internal
5-day rolling window (the buffer it uses to confirm *brand new* swings
going forward). That resets empty on every restart, same as a cold
start. In practice this means: for roughly 4 days after any restart,
the detector won't confirm any new swings on its own — but
`_trend_for_today()` keeps using the seeded historical values during
that gap, so trend decisions stay correct. Only the detector's *ability
to notice a brand-new swing forming* is briefly degraded, not its
existing knowledge.

**If this proves insufficient:** the seeded values are only ever 2
highs + 2 lows deep. If some future logic needs more history than that
(unlikely given how `_trend_for_today()` is written today, but worth
checking if that function ever changes), this function would need
extending to pull more rows and `seed_trend_history()` would need to
accept longer lists.

### 2. Today's in-progress session (bars accumulated, DayOrchestrator state)

**Lost on restart:** `CurrentDay` — the buffer of today's 5am-5pm bars,
whether the day's been decided tradeable yet, and the live
`DayOrchestrator` instance itself (mid-day raid/MSS/FVG tracking) — all
of this lives only in the runner process's memory. A restart loses all
of it.

**What's built:** `recover_on_startup()` in `shadow_runner/runner.py`
checks whether *anything* has been journaled to the `events` table for
today (NY calendar date) yet, via `get_last_event_timestamp_for_date()`:

- **Nothing journaled yet today** → safe to fully recover. Fetches up
  to 200 recent M5 bars from the bridge, filters to today's closed
  session bars, and replays them through the exact same `_process_bar()`
  pipeline live polling uses — meaning the normal 10am-decision-then-
  backfill flow just runs once, all at once, instead of trickling in
  bar-by-bar. Fully equivalent outcome to never having restarted at all
  (for today; see the multi-day caveat above for the swing detector's
  own internal buffer).

- **Something already journaled today** → deliberately does **NOT**
  replay. Logs a clear warning naming the exact gap window (from the
  last journaled event's timestamp to now) and just resumes normal live
  polling from that point forward. Today's journal will have a real gap
  for whatever happened during the outage.

**Why the second case doesn't just replay too:** replaying today's bars
re-runs them through a *fresh* `DayOrchestrator`, which would re-emit
every event that already got written before the crash — raids, MSS
confirmations, FVGs, fills, all of it — a second time. That's silent
data corruption (duplicate rows), which is a worse outcome than an
honestly-logged gap. This tradeoff was deliberate: **correctness over
completeness**.

**If this proves insufficient:** the risk window is specifically
"crashed mid-day after having already journaled at least one event
today." How often that actually happens in practice is unknown until
Phase 3 step 8 (run live, validate) has some real runtime under its
belt. If it turns out to happen often enough to matter, the fix is NOT
to relax the no-duplicate-replay rule — instead, build proper
idempotent writes (e.g. a unique constraint on some natural key per
event, with `ON CONFLICT DO NOTHING`-style upserts) so replay becomes
safe even with partial prior progress. That's a real schema/persistence
change, not a small patch — don't attempt it as a quick fix under
pressure if this scenario actually occurs; come back to this document
first.

## What was deliberately NOT built

- **No recovery for anything before "today."** If the runner is down
  across a day boundary (e.g. down all of Tuesday), Tuesday's trading
  activity is simply never journaled — there's no attempt to backfill
  a fully-missed day. Only "today, in progress" gets recovered.
- **No persistent heartbeat/health check** to detect a stuck-but-not-
  crashed process. This document only covers process restarts (crash,
  redeploy, `docker compose restart`), not a hung process that's still
  technically running but stopped making progress.
- **No test against a real Postgres instance.** All recovery logic
  (`tests/test_shadow_runner_recovery.py`) was verified against a fake,
  in-memory stand-in for the database — this sandbox has no real
  Postgres available. The real proof happens once this runs against
  Hetzner's actual database (Phase 3 step 8).

## Quick reference: files involved

| File | What it adds for recovery |
|---|---|
| `phase1/streaming/day_selection_gate.py` | `seed_trend_history()` method |
| `shadow_runner/persistence.py` | `get_recent_swing_history()`, `get_last_event_timestamp_for_date()` |
| `shadow_runner/runner.py` | `recover_on_startup()` |
| `shadow_runner/main.py` | calls `recover_on_startup()` before `run_forever()` |
| `tests/test_shadow_runner_recovery.py` | 4 tests covering both recovery paths |