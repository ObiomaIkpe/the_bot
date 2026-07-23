# Streaming state machine components

This documents the pieces of the Phase 1 streaming state machine as
they're built, one at a time. Each piece is a standalone,
independently-tested reimplementation of one part of the locked batch
model's logic (`FVG_model.py`) -- rewritten to work on one bar at a
time (as a live process would receive data) instead of on a full
dataframe already sitting in memory.

---

## DailySwingDetector (`daily_swing_detector.py`)

### What problem this solves

The locked model's daily trend filter needs to know, for any given day,
whether that day was a confirmed "swing high" or "swing low" -- a local
turning point in price. The batch version answers this by looking at
the whole array of daily bars at once:

```python
PIVOT_N = 2
for i in range(PIVOT_N, n_days - PIVOT_N):
    if d_highs[i] == max(d_highs[i-PIVOT_N : i+PIVOT_N+1]):
        swing_high_idx.append(i)
```

In plain terms: **day `i` is a confirmed swing high if it's the highest
of the 5 days centered on it** -- itself, the 2 days before, and the 2
days after.

A live process can't do this the same way, because it never has "the
whole array" -- it only ever has "every day up to today." The entire
job of this class is to produce the exact same answer as the batch
version, but honestly, one day at a time, without ever looking at a day
that hasn't happened yet.

### The core idea, visually

```
  Day 0      Day 1      Day 2      Day 3      Day 4
 +------+   +------+   +------+   +------+   +------+
 |      |   |      |   |HIGH- |   |      |   |      |
 |      |   |      |   |EST   |   |      |   |      |
 +------+   +------+   +------+   +------+   +------+
     |__________|___________|__________|__________|
              5-day window: 2 before, 2 after

  Day 2 can't be confirmed as a swing high until Day 4 has
  arrived -- "highest of these five" is a claim about all
  five, and Day 4 is one of them.
```

This delay is not a limitation introduced by going streaming -- the
batch version has exactly the same delay (day `i` needs `i+PIVOT_N` to
exist before it's even eligible for the loop). It's just invisible in
the batch code, because the whole array already exists when the loop
runs. Making the delay explicit is the actual point of this class.

### Interface

```python
det = DailySwingDetector(pivot_n=2)

for day in ordered_daily_bars:
    events = det.on_new_day(day.timestamp, day.high, day.low)
    # events is a list of 0, 1, or 2 dicts:
    #   {"event_type": "daily_swing_high_confirmed", "timestamp": ..., "price": ..., "day_index": ...}
    #   {"event_type": "daily_swing_low_confirmed",  "timestamp": ..., "price": ..., "day_index": ...}
```

- Feed it exactly one day at a time, in chronological order. Never a
  batch, never out of order.
- Most calls to `on_new_day` return an empty list -- that's normal, not
  an error. Confirmations only happen once every `2*pivot_n + 1` days
  of history exist.
- When something IS confirmed, it's always about the day sitting
  `pivot_n` days behind the one just fed in -- never about "today."

### Code walkthrough (matches the diagram above)

| Code | What it's doing |
|---|---|
| `self._window = deque(maxlen=2*pivot_n+1)` | The row of 5 boxes. A `deque` with a max length automatically drops the oldest day once a 6th arrives -- it always holds exactly "the last 5 days seen." |
| `self._window.append(...)` | A new day walks in on the right. |
| `if len(self._window) < self._window.maxlen: return []` | Not enough boxes yet -- there's no "middle" to judge until the row is full. This is why nothing happens for the first 4 days. |
| `self._window[self.pivot_n]` | Grabs the **middle** box -- day 2 in the picture -- not the day that just arrived. |
| `if candidate_high == max(highs):` | The actual judgment: is that middle box the tallest of all 5? |

If a swing ever gets confirmed at the wrong day, too early, or too
late, this table is the map for where to look -- specifically, confirm
`self._window[self.pivot_n]` is really the middle element, and confirm
nothing anywhere lets `on_new_day` see a 6th day before judging the
current 5. That second property (no lookahead) is enforced structurally:
`on_new_day` only ever receives one day's `(timestamp, high, low)` at a
time -- there is no reference anywhere in the class to a full array it
could accidentally peek into.

### Known behavior worth knowing about, not bugs

- **Ties are not broken.** If two days in the same 5-day window have
  the exact same high, both get confirmed as swing highs -- matching
  the batch model's bare `==` comparison, which has no tie-breaking
  either. If you ever want tie-breaking added, that's a real design
  change to the underlying strategy, not a bug fix -- don't add it here
  without deciding that deliberately.
- **The very last `pivot_n` days of any finite run never get a verdict.**
  In the historical replay used for validation, this matches the batch
  loop's own boundary (`range(pivot_n, n_days - pivot_n)` never
  evaluates the last `pivot_n` days either). In true live operation
  there's no "end of data," so this boundary condition doesn't actually
  apply the same way -- it only matters when replaying a fixed
  historical range for testing.

### Validation status

- **5 hand-constructed edge case tests** (`tests/test_daily_swing_detector.py`):
  no events before the window fills, correct day confirmed, confirmation
  delay is real (not lookahead), swing lows work symmetrically, ties are
  handled the way the batch model handles them.
- **Exact-match check against real data**: run against the actual
  3,249 daily bars derived from the real 2016-2026 EUR/USD dataset,
  diffed against the golden master's `daily_swing_high_confirmed` /
  `daily_swing_low_confirmed` events. Result: **893 / 893 events
  matched exactly** -- same event type, same timestamp, same day index,
  same price, for every single one.

### What's NOT covered by this class

This only handles the **daily** swing detection that feeds the trend
filter. See `IntradaySwingDetector` below for the 5-minute version that
feeds raid detection.

---

## IntradaySwingDetector (`intraday_swing_detector.py`)

### What's different from DailySwingDetector

Same core mechanism -- a centered window, a candidate that can't be
judged until enough bars after it have arrived -- applied to 5-minute
bars instead of days. Two real differences, not just a smaller number
plugged in:

1. **It resets every trading day.** The batch model recomputes
   `piv_high_all`/`piv_low_all` from scratch for each day's `full`
   dataframe -- nothing about yesterday influences today. This class
   makes that explicit with `start_new_day()`, which must be called
   once before the first bar of each new day.
2. **The window in real time is much shorter.** "2 before, 2 after" on
   5-minute bars is a 20-minute span, not 2 days.

This class deliberately does NOT share an implementation with
`DailySwingDetector`, even though the core logic is nearly identical --
see the top of `intraday_swing_detector.py` for why (avoiding
re-risking already-proven code for a code-cleanliness win).

Also, unlike `DailySwingDetector`, this class knows nothing about
session times (5 AM start, the Kill Zone, etc.) -- it only knows "bars
fed since the last `start_new_day()` call." Whatever feeds it is
responsible for deciding which days qualify at all (the batch model
skips FOMC days, no-trend days, and days with too little data before
running swing detection) and for starting a new day at the right
moment.

### Interface

```python
det = IntradaySwingDetector(swing_n=2)

for day in qualifying_days:  # caller decides which days qualify
    det.start_new_day()
    for bar in day.bars_from_5am_to_5pm_ny:
        events = det.on_new_bar(bar.timestamp, bar.high, bar.low)
        # events: same shape as DailySwingDetector's, but "bar_index"
        # (counts from 0 within the current day) instead of "day_index"
```

### Validation status

- **5 hand-constructed tests** (`tests/test_intraday_swing_detector.py`):
  window-fill delay, tie handling, plus two new ones specific to this
  class -- `start_new_day()` actually clears memory, and `bar_index`
  resets to 0 each day rather than continuing to climb.
- **Exact-match check against real data**: required first replicating
  the batch model's full day-selection logic (FOMC exclusion, trend
  filter, minimum-bar-count, session-start checks) to determine which
  days even get evaluated -- then running the detector over each
  qualifying day's 5 AM-5 PM bars. Result: **63,664 / 63,664 events
  matched exactly** (31,929 swing highs + 31,735 swing lows), every
  timestamp/bar_index/price identical.

### What's NOT covered by this class

- Which days qualify for swing detection at all -- that logic currently
  only exists in the validation script, not as a reusable component.
  It'll need to become one when the full state machine is assembled.
- Raid detection itself -- this class only produces the swing points
  raid detection will reference as "the most recent confirmed swing."
  Not yet built.

---

## RaidDetector (`raid_detector.py`)

### What this does, and why it's a bigger step than the last two

The first two components each detected one thing in isolation. This is
the first one that **combines** what they produce: a trend direction
(from the daily logic) and a stream of confirmed swings (from
`IntradaySwingDetector`), to decide whether a liquidity raid just
happened during the Kill Zone.

### Causality proof: why "check first, then update state" reproduces the batch model's cutoff exactly

The batch model uses `bisect.bisect_left(piv_low_all, i - SWING_N)` to
find the most recent confirmed swing usable at bar `i` -- which
requires the swing's index `c` to satisfy `c < i - SWING_N`.

`RaidDetector.on_new_bar()` checks the raid condition using state built
from previous bars, THEN folds in the current bar's new confirmations
for future use. Here's why that reproduces the exact same cutoff:

- A swing candidate `c` gets confirmed (emitted by `IntradaySwingDetector`)
  exactly when bar `c + SWING_N` is fed.
- `RaidDetector` folds that confirmation into its state at the END of
  processing bar `c + SWING_N` -- after that bar's own check.
- So the state is updated to include `c` starting from bar `c + SWING_N + 1` onward.
- Therefore, at bar `i`'s check (before folding in bar `i`'s own new
  confirmations), the state includes candidate `c` if and only if
  `c + SWING_N + 1 <= i`, i.e. `c <= i - SWING_N - 1`, i.e. **`c < i - SWING_N`**.

That's exactly the batch model's condition. The ordering in the code
(check, then update) isn't a stylistic choice -- it's the entire
mechanism that makes the causality correct. Reversing that order would
silently introduce a one-bar lookahead bug.

### The other easy-to-miss detail: both swing types are required, regardless of direction

Even an uptrend raid -- which only directly uses the confirmed swing
**low** as its trigger level -- still requires a confirmed swing
**high** to exist before any check happens at all. The batch model
does this because the swing high is needed immediately afterward for
MSS detection (not yet built): `if pl_pos == 0 or ph_pos == 0: continue`
gates on both, even though only one is used in the very next line.
Skipping this would make `RaidDetector` fire raids the batch model
would have silently skipped.

### Interface

```python
raid_det = RaidDetector()

for day in qualifying_days:
    raid_det.start_new_day()
    for bar in day.bars_from_5am_to_5pm_ny:  # ALL bars, not just Kill Zone ones
        swing_events = intraday_swing_det.on_new_bar(bar.timestamp, bar.high, bar.low)
        in_kz = kill_zone_start <= bar.index < kill_zone_end
        raid_events = raid_det.on_new_bar(
            bar.timestamp, bar.index, bar.high, bar.low,
            direction=trend, in_kill_zone=in_kz, new_swing_events=swing_events,
        )
```

Note this class must be fed **every bar from 5 AM**, not just Kill Zone
bars -- swings from before the Kill Zone are valid raid references, so
swing state has to track the whole day even though raid checks only
fire during the 7-10 AM window.

### Validation status

- **7 hand-constructed tests** (`tests/test_raid_detector.py`): no raid
  without any confirmed swings, the causality ordering itself (a
  same-bar confirmation is provably not usable), confirmation becomes
  usable the very next bar, the "both swing types required" gate,
  no firing outside the Kill Zone, the downtrend mirror, and
  `start_new_day()` actually clearing state.
- **Exact-match check against real data**: found **100% of the golden
  master's 14,727 `raid_detected` events with zero misses.** It also
  produced 2,312 additional raids beyond that -- **every single one
  individually verified** to occur on a day that already had a
  completed trade in the golden master, at a bar index after golden
  master's last logged raid that day. This is not a bug: it's the
  batch model's `if trade_found: break` (stop scanning a day once a
  trade completes) -- a day-level behavior that belongs to whatever
  assembles full trades later, not to raid detection in isolation.

### What's NOT covered by this class

- **Stopping a day's checks once a trade completes.** This is the
  direct cause of the 2,312 extra events above. Whatever orchestrates
  the full pipeline (raid → MSS → FVG → fill) will need to implement
  "stop processing this day once a trade is found," matching the
  batch model's one-trade-per-day cap (see `PROJECT_DESCRIPTION.md`
  §9, bug #3 -- removing this cap previously caused an artifactual
  +130.5% return).
- MSS detection, FVG detection, entry/stop/target logic -- none of
  this exists yet. `RaidDetector` only answers "did a raid happen,"
  not what happens after one does.

### Update: `mss_reference_level` field added

After this component was validated, its `raid_detected` event was
extended with one additional field, `mss_reference_level` -- the
confirmed swing on the OPPOSITE side from the one that triggered the
raid (already tracked internally; this just exposes it). This is a
pure addition: it doesn't change any field that was part of the
100%-match validation above, and that validation was re-run afterward
to confirm nothing regressed (same zero misses, same 2,312 explained
extras). `MSSWatch` (below) needs this value to know what level to
watch for.

---

## MSSWatch (`mss_watch.py`)

### What this is, and why it's shaped differently from the others

The previous three components each run continuously, fed one bar at a
time for as long as the stream lasts. `MSSWatch` is different: it's a
short-lived, **per-raid** object. Every time `RaidDetector` fires a
raid, a new `MSSWatch` gets created for that specific raid, watches
only the next ~9 bars, and is then discarded. This matches the batch
model's actual shape -- MSS search is a bounded lookahead tied to one
raid, not a standing process.

### Two details confirmed directly from the batch code, both easy to miss

1. **The window is bounded by the day's bars, not the Kill Zone.** The
   batch code's `min(i + 10, n)` uses `n` = the day's full bar count
   (5 AM-5 PM), not the Kill Zone's 10 AM end. A raid detected near the
   Kill Zone boundary can have its MSS search extend past 10 AM.
2. **MSS can confirm more than once per raid.** The batch loop doesn't
   stop at the first bar whose close crosses the level -- it keeps
   checking every subsequent bar in the window (via `continue` when no
   valid FVG follows), so several `mss_confirmed` events can fire for
   one raid if price stays beyond the level for multiple bars without
   yet producing a valid trade.

### Interface

```python
# spawned fresh for each raid_detected event
watch = MSSWatch(
    raid_bar_index=raid["bar_index"],
    direction=raid["direction"],  # "bull" or "bear"
    reference_level=raid["mss_reference_level"],
)

j = raid["bar_index"] + 1
while j < len(day_bars) and not watch.is_expired(j):
    events = watch.on_new_bar(day_bars[j].timestamp, j, day_bars[j].close)
    # events: list of 0 or 1 mss_confirmed dicts
    j += 1
```

### Validation status

- **6 hand-constructed tests** (`tests/test_mss_watch.py`): no
  confirmation on the raid's own bar, confirms correctly within the
  window, can confirm more than once, expires correctly at the window
  boundary, bear direction mirrors bull, and no false confirmation when
  the close doesn't actually cross the level.
- **Exact-match check against real data**: spawned a watch for every
  raid the full pipeline detected (including the already-known 2,312
  extra raids) and diffed against the golden master's 6,382
  `mss_confirmed` events. **Zero misses** -- every golden MSS event was
  found. Extras beyond that were fully traced to two already-understood
  causes, not a new logic problem:
  - Extras originating from one of the 2,312 already-known extra raids
    (the day-level gap documented above).
  - **1,074 extras from otherwise-genuine golden-master raids** -- each
    one individually verified to occur at a bar index strictly after
    golden master's own last logged MSS bar for that same raid. This
    is a narrower version of the same underlying gap: once a raid's
    search finds a valid trade (FVG + fill), the batch model stops
    checking that raid's remaining window entirely. `MSSWatch` doesn't
    know a trade filled, so it keeps watching until its window
    naturally expires.

### What's NOT covered by this class

- **Stopping a raid's own search once a trade fills within it.** This
  is the direct cause of the 1,074 extras above -- symmetric to
  `RaidDetector`'s day-level gap, but scoped to a single raid's window.
  Whatever orchestrates the full pipeline needs both: stop a raid's
  MSS search once a trade fills, AND stop a whole day once any raid
  produces a completed trade.
- FVG detection, entry/stop/target/fill logic -- `MSSWatch` only
  answers "did structure shift," not what happens after.

---

## FVGDetector (`fvg_detector.py`)

### What's different here

Every previous component had to wait for something -- a swing needed
future bars to confirm, MSS needed a bounded lookahead window. FVG
detection needs none of that: the check only ever compares the candle
2 bars back to the current candle, and both are already fully known
the instant the current bar arrives. There's no confirmation delay,
nothing here becomes "true later." The only reason this is a class at
all, rather than a bare function, is to carry the rolling 3-bar buffer
so callers don't have to manage it themselves.

The batch model only ever calls `find_fvg()` at the exact bar an MSS
confirmation just fired. This class mirrors that split deliberately:
`on_new_bar()` updates the rolling buffer unconditionally (call it for
every bar), while `check_fvg()` is a separate call the caller makes
only when it wants to actually test -- i.e., right when `MSSWatch`
fires. Calling `check_fvg()` at some other bar isn't wrong in itself
(it'll correctly report whether a gap exists there), it's just not
something the batch model would have looked at, so it wouldn't
correspond to anything in the golden master.

### Interface

```python
fvg_det = FVGDetector()

for bar in day_bars:
    fvg_det.on_new_bar(bar.index, bar.high, bar.low)  # every bar, unconditionally
    mss_events = watch.on_new_bar(bar.timestamp, bar.index, bar.close)
    for mss_e in mss_events:
        fvg_e = fvg_det.check_fvg(bar.timestamp, mss_e["direction"])
        # fvg_e is None, or a fvg_found event dict
```

### Validation status

- **5 hand-constructed tests** (`tests/test_fvg_detector.py`): no
  result before 3 bars are fed, correct bull FVG with correct
  top/bottom/frame_idx, no false positive when candles overlap
  normally, bear direction mirrors bull, and the rolling window
  genuinely slides forward (compares against 2-bars-back, not "the
  first bar ever fed").
- **Exact-match check against real data**: called `check_fvg()` at
  every MSS confirmation the full pipeline produced (including the
  already-known extras from both earlier gaps). **Zero misses** --
  every one of the golden master's 2,138 `fvg_found` events was found.
  All 520 extra events were traced back to the same two already-
  documented causes (extra raids, and MSS searches that continued past
  a fill) -- **no new gap was introduced by this component.** This is
  worth noting explicitly: it would have been easy for a rolling-window
  bug to hide inside what looked like "just more of the same" extras --
  tracing every single one confirmed that isn't what happened here.

### What's NOT covered by this class

- Entry price, stop, target, minimum-stop filtering, fill detection --
  none of this exists yet. `FVGDetector` only answers "is there a gap
  here," not what a trade built on it would look like.

---

## TradeAttempt (`trade_attempt.py`)

### What this covers

Everything that happens to ONE fair value gap once it's found: the
5-pip minimum-stop rejection, waiting for the limit order at the FVG
midpoint to be touched, computing the dynamic candle-midpoint target at
fill time, and tracking the trade through to win / loss / end-of-day
scratch.

### Three details confirmed directly from the batch code

1. **The target's 6-bar lookback uses the day's full price history,
   not "bars since this FVG."** If the fill happens within 6 bars of
   the FVG, some of the target window genuinely predates the FVG --
   even bars from the raid-to-MSS leg. This is why the class must be
   **seeded** with the last 6 bars (INCLUDING the FVG/MSS bar itself)
   at construction, rather than starting empty.
2. **Only one fill attempt ever happens per FVG.** If the first touch
   of the entry price produces an invalid target, the batch model
   abandons the FVG entirely -- it never re-evaluates a later touch.
3. **The outcome check starts AT the fill bar itself** (the batch's
   `q` loop starts at `p`, inclusive) -- one bar can fill the order
   and immediately hit stop or target.

### Validation status

- **12 unit tests** (`tests/test_trade_attempt.py`) covering every
  distinct path: rejection, fill + target computation, win, loss,
  same-bar fill+close, permanent abandonment (no retry), insufficient
  history, scratch, and the full short-direction mirror.
- Full-history validation: part of the complete-pipeline 603/603
  exact match (see DayOrchestrator below).

---

## DayOrchestrator (`day_orchestrator.py`)

### The bug this component exists to fix -- kept on record deliberately

The first attempt at wiring the five components together (validation-
script code, never shipped) produced **318 trades instead of 603**,
with 29 field mismatches among the trades it did find. Root cause: it
assumed "one raid's search at a time" -- once a raid began its MSS
search, no new raid was considered until that search resolved. The
batch model doesn't work that way. Its outer loop makes EVERY Kill
Zone bar a fresh raid candidate with its own full search; a raid whose
search comes up completely empty simply yields to the next bar's
candidacy. Nearly half the golden master's trades come from exactly
that situation -- an earlier raid's search failing and a later raid
winning the day.

The 29 field mismatches were a second, independent bug in the same
wiring: `TradeAttempt` seeds were built from bars up to the MSS bar
minus one, but the batch's target window (`highs[p-6:p]`) includes the
MSS bar itself when the fill comes soon after it.

### The scheduling rule, stated precisely

1. Every raid spawns its own candidate search; candidates run in
   parallel.
2. Within a raid, every MSS confirmation that yields a valid FVG
   passing the min-stop check spawns its own `TradeAttempt`; attempts
   also run in parallel.
3. The day's trade is the attempt with the **lexicographically
   smallest (raid_bar, mss_bar)** key among those that actually
   FILLED. Not the earliest fill in wall-clock time: a raid-24 attempt
   filling at bar 50 beats a raid-25 attempt filling at bar 40,
   because the batch model would have found raid 24's fill first and
   never evaluated raid 25 at all.
4. "Filled" means filled -- a loss or scratch still wins the day.
   Unfilled, abandoned, and min-stop-rejected attempts never win.

This also finally closes the two "stop early" gaps documented under
`RaidDetector` and `MSSWatch`: with the day's winner resolved by
priority selection at finalize time, the extra raids/MSS events those
components produce beyond the batch model's early-exit point simply
lose the selection -- they never become trades.

### Interface

```python
orch = DayOrchestrator(trend, session_start_idx, session_end_idx)  # fresh per day
for bar in day_bars:  # every bar from 5 AM, in order
    orch.on_new_bar(bar.timestamp, bar.index, bar.high, bar.low, bar.close)
trade = orch.finalize(last_timestamp, final_close)  # None, or the day's trade dict
```

### Validation status

- **4 unit tests** (`tests/test_day_orchestrator.py`) pinning the
  priority rules: earlier candidate key beats earlier wall-clock fill,
  unfilled attempts never win, open winners scratch at finalize, no
  filled attempts means no trade.
- **The decisive full-history validation: 603/603 trades, exact.**
  Every date, direction, entry, stop, target, outcome, exit price, and
  risk-pips figure identical to the golden master at 1e-9 tolerance,
  across the full 10.5-year dataset. 389 wins / 199 losses / 15
  scratches -- the locked batch model's result, reproduced bar by bar
  with no lookahead.
