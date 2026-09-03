# One detection engine, many accounts -- multi-user trade fan-out

Design only, written 2026-09-02 -- not committed, not yet being built.
See the note at the bottom for status.

## Context

The intended design was never "one bot per user" -- it's one shared
detection engine per *model* that finds a setup once, and that same
trade fires across every user's own account that has that model
enabled, sized to their own balance. Users opt out per model, not by
needing their own separate bot. `ModelConfig.status`
(`app/models/model_config.py`) already models this exactly --
active/shadow/disabled, per user, per model -- but nothing today
actually *uses* it that way.

Current reality, confirmed by tracing the code: `shadow_runner` is
hardcoded to exactly one user via env vars (`SHADOW_RUNNER_USER_ID`,
`BRIDGE_URL` -- `shadow_runner/config.py`). One container = one
account, full stop. The gap is entirely in *orchestration* -- the
piece that decides who a detected trade gets executed for.

**The good news, confirmed while investigating**: `OrderManager`
(`shadow_runner/order_manager.py`) is already correctly scoped -- one
instance = one model + one user + one bridge, not a singleton, and its
own module docstring already says so. `compute_lot_size`/
`_compute_volume` already take `balance`/`risk_pct` as plain inputs,
not architecturally tied to "the one bridge." Every user who connects
a broker account already gets their own fully independent bridge
worker (own port, own MT5 terminal, own process on the Windows VPS) --
this already scales per-user today, per `app/core/provisioning.py`.
Nothing about execution needs rearchitecting. The one genuinely missing
piece is a query that doesn't exist yet: "who has this model active,
and what's their bridge connection."

**Decisions made discussing this before any design work:**
- Detection's reference price feed reuses the current real account's
  bridge for now (simplest, zero new infrastructure) -- built so this
  is a config value with **no coupling to that account's identity**,
  so swapping to a dedicated order-less reference account later is a
  one-line config change, not a rework.
- Build and prove the new fan-out engine fully separately first. The
  real live account's actual cutover to it is its own separate,
  deliberate step, later -- not part of this plan.
- This is a design-only pass -- write the plan, do not start building.

## Design

### 1. New query: who's subscribed to a model right now

New function (`shadow_runner/persistence.py`, alongside the other
per-model DB helpers) -- something like
`get_active_subscribers(db, model_name) -> list[SubscriberInfo]`,
joining three tables that already have everything needed but have
never been joined this way:

```
ModelConfig (model_name=X, status='active')
  -> User
  -> BrokerCredential (user_id match, is_active=True, bridge_url IS NOT NULL)
```

Returns, per subscriber: `user_id`, `bridge_url`, `magic_number`,
`risk_pct`. Queried **fresh every time it's needed, never cached** --
same "checked fresh" discipline `OrderManager._is_user_paused()`
already follows, so a user disabling a model or losing their bridge
connection takes effect on the very next check, not after some stale
window.

### 2. Widen "one OrderManager" to "one per subscriber," at the exact
   spots that currently assume there's only one

This is the core of the change, and it's smaller than it sounds --
almost every piece of existing machinery (`DayOrchestrator`,
`DaySelectionGate`, `OrderManager` itself) stays exactly as it is. Only
the handful of places that currently hold/reference a *single*
`OrderManager` widen to a dict keyed by `user_id`:

- **`CurrentDay.order_manager`** (`shadow_runner/day_state.py`) ->
  **`CurrentDay.order_managers: dict[user_id, OrderManager]`**.
- **`_decide_day()`** (`shadow_runner/runner.py`): today, `if
  self.model_config is not None: cd.order_manager = OrderManager(...)`
  constructs exactly one. Becomes: call the new subscriber query once,
  and construct one `OrderManager` per subscriber (their own
  `BridgeClient(their bridge_url)`, their own `risk_pct`/
  `magic_number`), storing each in `cd.order_managers[user_id]`. This
  is the ONE place the subscriber list gets snapshotted for the day --
  matches how detection already only decides once per day, not
  per-poll.
- **`combined_sink`** (`_decide_day()`): today, `if cd.order_manager is
  not None: cd.order_manager.on_trade_candidate_ready(e)`. Becomes a
  loop over `cd.order_managers.values()`, **each call wrapped in its
  own try/except** -- one subscriber's broken/down bridge must never
  block or crash execution for the others. Reuses
  `_emit_check_failure`'s existing pattern, just needs the failing
  user's `user_id` attached to the emitted event so it's traceable to
  the right account.
- **`_check_order_manager_fills`/`_check_order_manager_close`/
  `_check_daily_loss_threshold`** (`runner.py`'s per-poll checks,
  called from `poll_once()`): each currently operates on
  `cd.order_manager` singular -- widen to loop over
  `cd.order_managers.values()`, same per-user try/except isolation.
  `attach_target()` needs the loop to track *which* user(s) actually
  got a fill this specific poll (today's `check_for_fills()` returns a
  bool; needs to identify the user too), since only a filled
  subscriber's `OrderManager` gets a target attached, not all of them.
- **`_write_trade()`/`_finalize_day()`**: today writes one `trades` row
  from `cd.order_manager.get_real_outcome()`. Becomes: write one row
  **per subscriber whose `OrderManager` actually has a real outcome**
  (most subscribers most days will have no real outcome at all --
  their candidate never filled, or the model wasn't tradeable for them
  that day -- exactly mirroring how `is_shadow`/real-action events
  already distinguish "this actually happened" from "this didn't").

### 3. Deployment model changes from "one container per user" to "one
   container per model"

Once this is built, adding a new real trader stops requiring **any**
`shadow_runner`-side change at all -- just their own bridge connection
(self-service provisioning already handles that) and an active
`ModelConfig` row (already exists, just needs `status` flipped to
`active`). This directly resolves the exact gap that prompted this
whole investigation ("is every user's account actually being watched")
and the friend's-account item in `PENDING_ITEMS.md` ("architecture
already supports it" was only ever true on the bridge side -- this
closes the other half).

### 4. Trade story and admin view impact

The trader-facing trade story page pairs one shared narrative (raid ->
MSS -> FVG -> candidate, from `events`) with one outcome (a `trades`
row). That pairing stays conceptually valid, but the two halves need
to be explicitly decoupled: the narrative is shared across every
subscriber, the outcome becomes per-subscriber. A regular user's trade
story view must filter to **their own outcome only** -- this is a
privacy boundary now, not just a query convenience.

The admin cross-user Trades/story view reshapes from "one row = the
day's trade" to one shared narrative header per model per day, with a
nested list of per-subscriber outcomes underneath it: who fired, who
filled, who didn't, and what each closed at. Divergent outcomes across
subscribers are real information, not an edge case to hide -- one
account's order can fill while another's doesn't (slow bridge, price
moved past entry first, margin/lot-size rejection), and even when
everyone fills, P&L differs per account by `risk_pct`/balance against
an identical target/stop.

**Everything explicit, nothing silent -- confirmed with the user.**
Extends the same philosophy already used for
`duplicate_fill_closed`/`orphan_position_recovered`: a subscriber whose
candidate does NOT fill gets its own journaled event
(`subscriber_no_fill` -- see "Open questions, resolved" below for the
full event-taxonomy decision), not silence. This is what lets the admin view show "5
subscribed, 4 filled, 1 missed -- here's why" instead of an absence
nobody can query for. Directly motivated by the whole reason this plan
exists: a visibility gap that went undetected for a week (see
`PENDING_ITEMS.md`'s "Real bugs found 2026-09-02").

### 5. Narrative event ownership -- nullable `Event.user_id`

Surfaced during implementation of piece 2 (widening `_write_trade()`),
not caught during the original design pass: `Event.user_id` and
`Trade.user_id` are both `NOT NULL` today -- every row belongs to
exactly one person. That's fine for `Trade`, since the plan already
writes one row per subscriber who has a real outcome (each naturally
owned by that subscriber, no change needed there). It's a real,
previously-unresolved gap for the **narrative** events -- `raid_detected`,
`mss_confirmed`, `fvg_formed`, `trade_candidate_ready`,
`day_trend_determined`, `day_skipped_*` -- which get written once per
model per day, before any subscriber-specific fan-out happens. Whose
`user_id` do those belong to once "the one hardcoded account" no longer
exists?

**Decided: `Event.user_id` becomes nullable.** Narrative-type events get
written with `user_id = NULL`, scoped by `(model, date)` alone rather
than by any person. This was chosen over the alternative (attributing
the shared narrative to whichever account's bridge is serving as the
reference price feed) for a concrete reason found while comparing the
two: `User.events` cascades on delete
(`cascade="all, delete-orphan"`) -- attributing narrative ownership to
the reference account would mean deleting that one account's `User` row
retroactively destroys every subscriber's shared narrative history for
that model, not just going forward. The nullable approach has no such
coupling, needs no new authorization carve-out for subscribers reading
a shared narrative that technically belongs to someone else, and
creates no permanent historical seam if the reference bridge is ever
swapped later (see the deferred dedicated-reference-account idea,
below).

**Real scope, not a footnote -- its own sub-piece, not folded silently
into piece 2:**
- A migration: `events.user_id` NOT NULL -> nullable.
- Four existing persistence functions (`shadow_runner/persistence.py`)
  currently take `(db, user_id, model)` and filter narrative-type
  events by a specific user as a stand-in for "the one hardcoded
  account": `get_last_event_timestamp`, `get_last_event_timestamp_for_date`,
  `get_recent_swing_history`, `event_type_exists`. These are honestly
  already model-scoped concepts (detection state -- swing history,
  bootstrap markers, gap-detection timestamps -- is inherently shared
  across every subscriber, not personal to one). Signatures change to
  drop the per-user scoping for their narrative-relevant call sites;
  every caller in `runner.py` updates accordingly.
- The test fixtures in `test_cross_day_recovery.py`, `test_bootstrap.py`,
  `test_shadow_runner_recovery.py`, and `test_cold_start_bugfixes.py`
  all construct narrative-shaped `Event` rows with an explicit
  `user_id` today (this month's bug-2 fix tests against exactly this
  shape) -- these need rework to construct `user_id=None` rows instead,
  real changes to already-shipped, already-hardened test coverage, not
  greenfield.

This sits ahead of piece 2 in build order, not after -- `combined_sink`
(piece 2's core change) is exactly the code that writes narrative
events, so it needs to know to write them with `user_id=NULL` from the
start rather than being reworked twice.

## Open questions, resolved

Surfaced during plan review, before any code gets written -- each one
answered and locked in here rather than left to be decided mid-build.

**1. The live-account transition path (highest risk of the seven).**
The current hardcoded single-user `shadow_runner` keeps running
completely untouched in production for the entire build. The new
multi-subscriber engine is built and proven as a fully separate path,
tested only against test/shadow accounts -- the real account's
`ModelConfig` never gets flipped to run under the new engine until the
old process is deliberately stopped first. Cutover is one conscious
step (stop old, start new with the real account now included as a
subscriber), never a gradual overlap. Treated as a hard rule, not a
preference: running both against the same live account at once is
exactly the shape of bug this project already got bitten by once.

**2. Mid-day subscription changes.** A new subscriber turning a model
on mid-day misses that day entirely -- picked up at the next day's
snapshot, same as detection already only deciding once per day. A
subscriber turning a model off mid-day keeps having any already-open
or pending order for them managed to completion (fill tracked, target
attached, closed normally) -- never abandoned just because the toggle
flipped -- they simply aren't admitted into tomorrow's snapshot.
Abandoning an in-flight order on opt-out would recreate the exact
"unmanaged real position" shape both of this month's bugs were about.

**3. Reference bridge as a new single point of failure.** No new
failover infrastructure gets built now -- a dedicated reference-only
bridge stays exactly as already scoped in "Explicitly out of scope"
below, built only if it's ever actually needed. What does get added:
if the reference bridge can't be reached when detection tries to run
for the day, fire the same kind of Telegram alert already used for the
cross-day-gap work, so a dead reference feed is visible same-day
instead of discovered a week later.

**4. Daily-loss-threshold isolation.** No new design work -- this
falls out of the per-subscriber widening for free, since
`_is_user_paused()` and the loss check already operate per
`OrderManager` instance. Just needs an explicit test proving it (see
Verification), not new logic.

**5. Event taxonomy, decided up front.** Follows the exact precedent
set by both bug fixes: new top-level `REAL_ACTION_EVENT_TYPES` only
for genuine outcomes; failures reuse `safety_check_failed` with a
distinct `check_name`, same as `duplicate_fill_close_failed`. Two new
things needed: `subscriber_no_fill` (new top-level type -- an expected
real outcome, not a failure, replacing the earlier "TBD" name in
section 4 above) and a new distinct `check_name` (e.g.
`subscriber_order_placement_failed`) for a subscriber-specific
execution/bridge error during fan-out, reusing `safety_check_failed`
rather than inventing a parallel event type for it.

**6. Rollout acceptance criteria.** Journal-only/shadow mode (zero real
orders, regardless of subscriber count) against at least 2-3 real
connected accounts -- not just unit-test fakes -- for a minimum of two
full trading weeks, deliberately including at least one restart during
that window (to also exercise the cross-day-recovery interaction) and
at least one weekend/holiday boundary. Zero unexplained discrepancies
between the per-subscriber journal and each account's real state.
Only then does the real account get added as a subscriber.

**7. Admin UI scope.** Fast-follow, not bundled into this build. The
engine change (pieces 1-3 above) is already the high-stakes part --
multiple real accounts, real money. The nested-outcome admin UI is
purely observational; shipping it a week later costs nothing, while
bundling it in only inflates the size of an already-large, high-stakes
change for no safety benefit.

## Explicitly out of scope for this pass

- **Building any of this yet** -- this is the design, not the
  implementation. A separate go-ahead starts actual coding.
- **Cutting the real live account over** to the new engine, even once
  built -- proven separately first (unit tests, likely a shadow/
  journal-only run across multiple test accounts), then the real
  account's move to it is its own deliberate step, same discipline as
  every other live-money change this project has made.
- **The dedicated order-less reference bridge** (Option 2 from the
  price-feed discussion) -- deferred until actually needed; the config
  is being built swappable specifically so this doesn't require rework
  later.
- **Extending the cross-day recovery work** (`recover_on_startup()`,
  `_replay_historical_day()` -- see `PENDING_ITEMS.md`'s "Real bugs
  found 2026-09-02", already fixed and shipped) to rebuild N
  `OrderManager`s instead of one on restart -- a natural, incremental
  extension of that same pattern once this lands, not solved here.

## Verification (once building actually starts)

- New tests for `get_active_subscribers()` -- multiple users, some
  with the model disabled, some with no active bridge credential, some
  with both -- confirm only the correctly-eligible set comes back.
- New tests mirroring `test_order_manager_wiring.py`'s existing
  approach, extended to N subscribers: a real `trade_candidate_ready`
  event must reach every subscribed user's own fake bridge, and must
  NOT reach a disabled/unsubscribed user's bridge at all.
- A test proving one subscriber's bridge failure (exception on
  `place_pending_order` or similar) doesn't stop or corrupt execution
  for the other subscribers in the same poll.
- A test proving a subscriber whose candidate does NOT fill gets its
  own explicit journaled event (not silence), while a subscriber who
  does fill does not also get that event.
- A test proving a regular user's trade story query returns only their
  own outcome row, never another subscriber's, even when both fired
  from the same shared narrative that day.
- Full suite against the established baseline before considering any
  of this done, same discipline as every other change this session.

## Build notes (2026-09-03) -- three gaps found during implementation

Found while actually building pieces 1-2, not caught during design
review. Each was raised and resolved with the user before being built,
same discipline as the rest of this plan -- recorded here so the design
sections above and what actually got built don't silently diverge.

1. **Narrative events needed their own migration** (piece "1.5", not in
   the original numbering) -- `Event.user_id`/`Trade.user_id` were both
   `NOT NULL`; the shared detection narrative (raid/MSS/FVG/candidate)
   has no single natural owner once it can fan out to N subscribers.
   Resolved: `Event.user_id` and `Trade.user_id` both made nullable
   (migrations 0020, 0021) -- NULL means "shared, ownerless," not
   broken. Chosen over attributing the shared rows to the reference-feed
   account specifically because `User.events`/`User.trades` cascade on
   delete: that alternative would let deleting one account destroy
   every subscriber's shared narrative AND the model's whole shadow-mode
   trade/equity history, not just going forward. Four existing
   consumers (`GET /events`, the trade story page, `/admin/events`,
   `/admin/trades`) needed fixing so none of them silently dropped or
   misattributed the newly-ownerless rows -- an inner join, in
   particular, would have quietly deleted every shared row from two of
   those views.
2. **`_write_trade()` never actually addressed what happens with zero
   subscribers.** The original design text only covered "one row per
   subscriber with a real outcome" -- it never said what happens to the
   model's own always-on simulated trade record (how shadow-mode model
   evaluation has always worked, proving a model before anyone risks
   real money on it) once there's no more single hardcoded account to
   attribute it to. Resolved as part of gap 1: an ownerless "shadow" row
   is written every tradeable day regardless of subscriber count, with
   its own fixed, notional risk/equity tracking, deliberately decoupled
   from any real account's numbers.
3. **`PositionTracker` was completely missing from the original design**
   -- it manages a real position AFTER it fills (the 5pm partial-close
   risk reduction, and multi-day tracking to natural resolution),
   entirely separate from `OrderManager`, and the design section never
   mentioned widening it. Left as-is, real order placement would have
   correctly fanned out to every subscriber while overnight risk
   management silently kept working for only one account -- the same
   class of "silently unmonitored real position" shape as this month's
   two live bugs. Resolved: widened to one `PositionTracker` per
   subscriber, built for everyone at startup (so a restart doesn't lose
   an already-open multi-day position) and topped up at every
   `_decide_day()` (so a new subscriber gets coverage without needing a
   restart).

A fourth, smaller thing fell out of building gap 3 correctly: since all
subscribers' `OrderManager`s share one event list per day, each event
now needs tagging with which subscriber it belongs to
(`_origin_user_id`) before it's written, or every subscriber's real
fills/closes/failures would get attributed to whichever id happened to
be hardcoded -- fixed as part of the same pass, not a separate gap.

## Status

**Pieces 1, 1.5, and 2 are built, tested, and on `main`.** 396 passed /
1 skipped, same pre-existing unrelated baseline failures as before this
work, zero regressions. Piece 1 (`get_active_subscribers()`), piece 1.5
(nullable narrative ownership), and piece 2 (`OrderManager`/
`PositionTracker` widened to per-subscriber, dual shadow+real-outcome
trade writing) are all done.

**Nothing has been deployed to the live VPS.** Per the transition-path
rule locked in during design review, the real account keeps running the
old single-user code untouched in production until a deliberate,
separate cutover -- this build has had zero effect on live trading so
far. Still ahead: the deployment-model shift (section 3 above -- one
container per model instead of per user), the rollout acceptance
criteria (two weeks journal-only across real test accounts before the
real account moves over), and the admin UI's nested per-subscriber
trade story (section 4 above, deliberately scoped as a fast-follow, not
part of the engine build).
