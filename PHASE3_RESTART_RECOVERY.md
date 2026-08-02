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

## Addendum — cold-start trend bootstrap (added after first live deployment)

The two recoveries above only cover things a *prior run of this system*
already knew. They don't help on the very first deployment ever, when
there's no prior run to recover from at all. That gap showed up
immediately in practice: the first real deployment came up cleanly,
correctly found nothing to recover, and then correctly reported
`no_trend` for the only real day it had data for -- because with zero
swing history, there's nothing to compute a trend from yet, and
`DaySelectionGate` normally needs ~9 real trading days to accumulate
enough on its own.

### What's built

A third, one-time step: `ShadowRunner._bootstrap_trend_history_if_needed()`,
called at the very start of `recover_on_startup()`, before the two
recoveries above. It fetches up to 5,000 M5 bars from the bridge
(~17 trading days, the bridge's max per call), aggregates them into
NY-calendar-day highs/lows itself (deliberately not using MT5's D1
candle directly -- same UTC-vs-NY bucketing trap flagged earlier in this
phase), and feeds the result through `DaySelectionGate.on_day_closed()`
in chronological order, giving real trend data from day one instead of
two weeks in.

### The duplication risk this had to guard against

Naively re-running this fetch-and-inject on every restart would
duplicate the same historical swing-confirmation events every time the
container restarts. Guarded against with a one-time marker event
(`trend_history_bootstrapped`, written once per user+model, checked
first on every future startup).

A second, subtler case is also guarded against: if this bootstrap code
gets deployed onto a system that's *already* been running for real for
a while (real swing history already exists, no marker yet because the
feature didn't exist when it started accumulating that history), the
code detects the existing real data and writes the marker retroactively
**without** injecting anything on top of it -- avoids creating
duplicate/overlapping historical days.

### What this does NOT cover

- Only bootstraps once, ever. If the bootstrap fetch itself fails (bridge
  down at startup), it logs an error and does NOT write the marker --
  meaning it'll simply retry on the next restart. No partial-bootstrap
  state is possible; it's all-or-nothing per attempt.
- The ~17 trading days fetched depend entirely on the bridge's 5,000-bar
  cap. If that cap ever changes, or if fewer than the ~9 real days
  needed for a first swing confirmation happen to be available (e.g. a
  brand-new broker account with limited history), bootstrap could
  complete having accumulated fewer than 2 confirmed highs/lows -- in
  which case `DaySelectionGate` correctly falls back to reporting
  `no_trend` until enough real days close, same as it would have without
  bootstrap at all. Not a bug, just a smaller-than-ideal head start in
  that edge case.
- No test against the real bridge or real Postgres -- verified with the
  same fake-DB/fake-bridge approach as everything else in this document
  (`tests/test_bootstrap.py`, 3 tests covering all three branches: fresh
  cold start, already-bootstrapped, and pre-existing-real-history).

## Quick reference: files involved

| File | What it adds for recovery |
|---|---|
| `phase1/streaming/day_selection_gate.py` | `seed_trend_history()` method |
| `shadow_runner/persistence.py` | `get_recent_swing_history()`, `get_last_event_timestamp_for_date()`, `event_type_exists()` |
| `shadow_runner/runner.py` | `recover_on_startup()`, `_bootstrap_trend_history_if_needed()` |
| `shadow_runner/main.py` | calls `recover_on_startup()` before `run_forever()` |
| `app/models/event.py` | `trend_history_bootstrapped` added to `VALID_EVENT_TYPES` |
| `tests/test_shadow_runner_recovery.py` | 4 tests covering both original recovery paths |
| `tests/test_bootstrap.py` | 3 tests covering the cold-start bootstrap's three branches |

## Addendum 2 — two real bugs found on first live deployment, both fixed and confirmed

The very first live deployment to Hetzner happened to start cold on a
weekend (market closed since Friday). That specific timing surfaced two
real bugs neither the design discussion nor the unit tests had caught --
worth recording exactly what happened, since it's a good example of why
step 8 (run live, validate) is a real, separate step and not a formality.

### Bug 1: the bootstrap marker polluted the "has today started" check

**Symptom, from the first deployment's actual log:**
```
Bootstrap: fed 21 historical days, confirmed 5 swing events
Recovery: seeded trend history (2 confirmed highs, 2 confirmed lows) from prior events
WARNING Recovery: 2026-08-02 already has journaled events up to 2026-08-02 08:17:42 --
  a prior run must have stopped partway through today. NOT replaying...
```
This fired on a container that had been running for *seconds*. Root
cause: `_bootstrap_trend_history_if_needed()` writes its own
`trend_history_bootstrapped` marker event, timestamped today (since it
just ran). `get_last_event_timestamp_for_date()` -- which exists to
detect "did a PRIOR run already journal part of today" -- had no way to
tell that marker apart from real trading activity, so it always
triggered its own false positive immediately after every bootstrap.

**Fix:** `get_last_event_timestamp_for_date()` now explicitly skips
`trend_history_bootstrapped` rows when scanning for today's activity.
Covered by `tests/test_cold_start_bugfixes.py`'s first two tests (marker
alone doesn't count; a real event on the same day still gets detected
correctly).

**Confirmed fixed, next deployment's log:**
```
Bootstrap: already done previously, skipping
Recovery: seeded trend history (2 confirmed highs, 2 confirmed lows) from prior events
Recovery: no events journaled yet for 2026-08-02 -- replaying from 5am NY through now
```

### Bug 2: cold-starting on a weekend tried to judge Friday from a fragment

**Symptom, same first-deployment log:**
```
2026-07-31: skipped (insufficient_bars)
```
Confusing on its face -- the runner started on a Sunday, so why is it
evaluating Friday at all? Root cause: with the market closed since
Friday, the bridge's "most recent bars" response was necessarily just
Friday's last ~20 candles (nothing more recent exists yet). The runner
saw those bars were dated differently from "no current day yet," treated
that as a day starting, and tried to build a full day's decision out of
a ~100-minute fragment -- technically-correct verdict
(`insufficient_bars`), but for the wrong reason, on a day that was never
going to be properly journaled anyway.

**Fix:** `_process_bar()` now checks, only on true cold start (no
`current_day` yet), whether the incoming bar's date is strictly before
today's real NY date. If so, it's logged and discarded -- no `CurrentDay`
gets constructed at all. Bars dated today or later are unaffected.
Covered by `tests/test_cold_start_bugfixes.py`'s last two tests (a stale
bar creates nothing; a genuinely current bar still starts a day
normally).

**Confirmed fixed, next deployment's log:**
```
Ignoring stale bar at 2026-07-31 15:20:00 (date 2026-07-31, before today 2026-08-02) --
  waiting for genuinely current data
Ignoring stale bar at 2026-07-31 15:25:00 ...
[... one line per stale bar, no misleading day-skip verdict, no CurrentDay constructed ...]
```

### One test-suite side effect worth knowing about

Fixing bug 2 broke two pre-existing tests in `test_runner_orchestration.py`
(`test_day_rollover_finalizes_previous_day_and_feeds_daily_swing` and
`test_decision_deferred_until_10am_then_backfills_everything_at_once`).
Not a regression -- both tests used a hardcoded fake date
(`2026-07-01`) as their starting point, which the new guard correctly
started treating as "the past" once real wall-clock comparisons were
added. Fixed by anchoring `establish_trend()`'s starting date off the
real current date instead of a fixed constant. Worth remembering for any
future test that constructs bars with a hardcoded date: if it's meant to
represent "today" for the code under test, it needs to actually track
real time, not a snapshot of whatever date happened to be convenient
when the test was written.

### Current status as of this addendum

Both fixes deployed and confirmed via real logs on Hetzner (not just unit
tests). The system is correctly waiting out the rest of the weekend with
no misleading log output. Next real checkpoint: confirm normal live
operation once the market reopens (Sunday evening / Monday NY time) --
first bars with today's actual date should construct a fresh
`CurrentDay`, and around 10am NY the first real `day_trend_determined`
or `day_skipped_*` decision should appear, this time informed by the
bootstrapped historical trend data rather than starting from zero.