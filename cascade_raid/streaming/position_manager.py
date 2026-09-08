"""
position_manager.py -- reimplementation of the inline simulation loop
in both reference model scripts (CASCADE_RAID_3min_model_v1_LOCKED.py /
CASCADE_RAID_3min_3slot_propfirm_v2.py -- identical loop logic, differ
only in MAX_CONCURRENT_POSITIONS). Handles: pending-order fill
detection, fixed 30/60-pip stop/target, the week-end (Friday 17:00 NY)
hold cutoff, the concurrency cap, and the consecutive-loss circuit
breaker.

A GENUINE FINDING, not a translation artifact -- READ BEFORE CHANGING
THIS FILE'S STRUCTURE:

An earlier version of this module tried to reshape this into a single
global "on_bar() called once per bar, in true real-time order" event
loop, the same way signal_detector.py's pending-MSS-watch redesign
worked. That version FAILED bit-for-bit validation against the
reference, and tracing the failure down turned up something important:
the reference's breaker doesn't just process trades "in entry order"
as a stylistic choice -- it has a genuine, unavoidable FORWARD-LOOKING
dependency baked into the locked model's own definition, confirmed
both by the failing test and by the reference doc's own Section 7.4:

    "the loss-streak counter updates in the order trades were opened
    -- not necessarily the order they actually closed in real time...
    it reacts as soon as the second one (by entry order) is a loss...
    regardless of how long the third and fourth trades take to finish
    running."

Concretely: if signal J is discovered before signal K, the reference
ALWAYS fully resolves J -- fill search, then stop/target/cutoff,
however many bars or weeks that takes -- before it even LOOKS at K's
own accept/reject decision. If J takes 3 weeks to resolve and K's own
order_start is only 2 days after J's, the reference's decision for K
still uses J's fully-known FUTURE outcome. A genuinely live system
processing bars in real time cannot know J's eventual outcome yet at
that point -- this is not solvable by a cleverer streaming design; the
locked reference's own specification has a look-ahead dependency in
it. That's a real, open question for whoever wires this into live
execution (Phase 2, not this module) -- the honest options are: accept
a small, documented divergence from the exact backtest in the rare
case this actually matters (an early trade still open while a later
signal's breaker-gated decision is due), or hold a later decision
until the queue ahead of it clears (which could delay a real trade
by however long an earlier one takes to resolve). Not decided here --
flagged for that decision explicitly rather than silently picked.

WHAT THIS MODULE ACTUALLY DOES: since Phase 1's job is proving exact
reproduction of the ALREADY-KNOWN historical backtest (not real-time
prediction), it reproduces the reference's own signal-sequential
structure directly -- process one signal fully (its own fill search,
then its own exit search, both via genuine bar-by-bar for-loops, never
a vectorized/whole-array shortcut) before moving to the next, exactly
matching the reference's own order of operations and therefore its
exact breaker semantics. Concurrency is tracked via each open
position's own known exit_idx, filtered with the reference's own
INCLUSIVE `>=` semantics (a position exiting on bar X still occupies
its slot when deciding whether a signal at bar X can be accepted or
filled).
"""
PIP = 0.0001
FIXED_SL_PIPS = 30.0
FIXED_TP_PIPS = 60.0
ORDER_FILL_EXPIRY_BARS = 150  # the actual constant both locked scripts hardcode inline --
                                # NOT shared_infrastructure's FILL_EXPIRY=30, which the
                                # locked scripts never import or use


class PositionManager:
    def __init__(
        self,
        max_concurrent_positions: int,
        breaker_threshold: int = 2,
        breaker_skip: int = 6,
        fixed_sl_pips: float = FIXED_SL_PIPS,
        fixed_tp_pips: float = FIXED_TP_PIPS,
        risk_pct: float = 0.01,
        order_fill_expiry_bars: int = ORDER_FILL_EXPIRY_BARS,
    ):
        self.max_concurrent_positions = max_concurrent_positions
        self.breaker_threshold = breaker_threshold
        self.breaker_skip = breaker_skip
        self.fixed_sl_pips = fixed_sl_pips
        self.fixed_tp_pips = fixed_tp_pips
        self.risk_pct = risk_pct
        self.order_fill_expiry_bars = order_fill_expiry_bars

        self._open_exits: list[int] = []  # exit_idx of currently-tracked positions
        self._consecutive_losses = 0
        self._skip_remaining = 0

        self.trades: list[tuple[dict, float]] = []  # (trade_dict, risk_pct), in signal-processing order

    def process_signals(self, signals_sorted: list[dict], high, low, close, n_total_bars: int, cutoff_idx_fn) -> None:
        """`signals_sorted`: bias-aligned signals already sorted by
        signal_time (== signal_idx), matching the reference's own
        `aligned_sorted`. `high`/`low`/`close`: full M1 series
        (indexable by bar position). `cutoff_idx_fn(fill_idx) -> int`:
        the week-end-hold cutoff bar index for a fill at `fill_idx`."""
        for sig in signals_sorted:
            order_start = sig["signal_idx"] + 1
            if order_start >= n_total_bars:
                continue

            self._open_exits = [e for e in self._open_exits if e >= order_start]
            if self._skip_remaining > 0:
                self._skip_remaining -= 1
                continue
            if len(self._open_exits) >= self.max_concurrent_positions:
                continue

            direction = sig["direction"]
            entry_price = sig["fvg_near"]
            fill_idx = None
            for j in range(order_start, min(n_total_bars, order_start + self.order_fill_expiry_bars)):
                if direction == "short" and high[j] >= entry_price:
                    fill_idx = j
                    break
                if direction == "long" and low[j] <= entry_price:
                    fill_idx = j
                    break
            if fill_idx is None:
                continue

            self._open_exits = [e for e in self._open_exits if e >= fill_idx]
            if len(self._open_exits) >= self.max_concurrent_positions:
                continue

            stop_price = entry_price + self.fixed_sl_pips * PIP if direction == "short" else entry_price - self.fixed_sl_pips * PIP
            target_price = entry_price - self.fixed_tp_pips * PIP if direction == "short" else entry_price + self.fixed_tp_pips * PIP
            cutoff_idx = min(cutoff_idx_fn(fill_idx), n_total_bars - 1)

            outcome = exit_price = exit_idx = None
            for j in range(fill_idx + 1, cutoff_idx + 1):
                stop_hit = (high[j] >= stop_price) if direction == "short" else (low[j] <= stop_price)
                target_hit = (low[j] <= target_price) if direction == "short" else (high[j] >= target_price)
                if stop_hit:
                    outcome, exit_price, exit_idx = "loss", stop_price, j
                    break
                if target_hit:
                    outcome, exit_price, exit_idx = "win", target_price, j
                    break
            if outcome is None:
                exit_idx = cutoff_idx
                outcome = "scratch"
                exit_price = close[exit_idx]

            trade = dict(
                direction=direction, entry=entry_price, stop=stop_price, target=target_price,
                risk_pips=self.fixed_sl_pips, outcome=outcome, exit_price=exit_price,
                held_past_close=False, half_exit_price=None,
                fill_idx=fill_idx, exit_idx=exit_idx,
            )
            self.trades.append((trade, self.risk_pct))
            self._open_exits.append(exit_idx)

            if outcome == "loss":
                self._consecutive_losses += 1
                if self._consecutive_losses >= self.breaker_threshold:
                    self._skip_remaining = self.breaker_skip
                    self._consecutive_losses = 0
            elif outcome == "win":
                self._consecutive_losses = 0
            # scratch: no-op, matches the reference exactly (only if/elif on loss/win)
