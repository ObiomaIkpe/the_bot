"""
Phase 4 step 3 Part 3: real-vs-simulated reconciliation -- the actual
measurement Option B exists for (this phase's chat history: "place
orders as soon as noticed, measure real slippage rather than
optimizing blind"). Pure calculation functions here, kept separate
from report.py's DB-querying/printing, so the math itself is directly
unit-testable against synthetic data without needing any real trade to
exist yet.

SIGN CONVENTION, READ THIS BEFORE TRUSTING A NUMBER FROM THIS MODULE
------------------------------------------------------------------------
Every "slippage_pips" value below is FAVORABLE/UNFAVORABLE framed, not
raw price-difference framed:
    positive slippage_pips = cost you money (real price was WORSE than
        what the simulation intended)
    negative slippage_pips = free money (real price was BETTER than
        what the simulation intended)
This is deliberately NOT "real minus simulated" directly, because that
raw difference flips meaning between long and short trades (a higher
real fill price is bad for a long, good for a short) -- reporting the
raw difference would silently mislead anyone reading it without also
mentally tracking direction. Favorable/unfavorable framing means
"positive is always bad" regardless of direction, matching how a
trader actually wants to read a slippage report.
"""
import datetime

PIP = 0.0001  # matches phase1/streaming/trade_attempt.py's own convention


def entry_slippage_pips(direction: str, entry_price: float, real_fill_price: float) -> float:
    """
    entry_price: the SIMULATION's intended entry (FVG midpoint).
    real_fill_price: what the broker actually filled at.
    """
    diff = real_fill_price - entry_price
    if direction == "long":
        # Long: paying MORE than intended (higher fill) is unfavorable.
        return diff / PIP
    else:
        # Short: selling for LESS than intended (lower fill) is unfavorable.
        return -diff / PIP


def exit_slippage_pips(direction: str, simulated_exit_price: float, real_close_price: float) -> float:
    """
    simulated_exit_price: what the SIMULATION's own bar-by-bar logic
        determined the exit would have been (Trade.exit_price).
    real_close_price: what the broker actually closed at (either the
        real stop-loss/take-profit level, or a partial-close price).
    """
    diff = real_close_price - simulated_exit_price
    if direction == "long":
        # Long: closing LOWER than the simulation expected is unfavorable.
        return -diff / PIP
    else:
        # Short: closing HIGHER than the simulation expected is unfavorable.
        return diff / PIP


def real_realized_r(direction: str, real_fill_price: float, stop_price: float, real_close_price: float) -> float | None:
    """
    Same generic R-multiple calculation shadow_runner/persistence.py's
    compute_realized_r() uses for the SIMULATED trade, applied here to
    the REAL prices instead -- lets the two be compared directly
    (Trade.realized_r vs this function's output), same denominator
    logic (risk = |entry - stop|), just fed real numbers.
    """
    risk = abs(real_fill_price - stop_price)
    if risk == 0:
        return None
    if direction == "long":
        pnl = real_close_price - real_fill_price
    else:
        pnl = real_fill_price - real_close_price
    return pnl / risk


def timing_gap_seconds(bar_close_time_utc: datetime.datetime, real_fill_time_utc: datetime.datetime) -> float:
    """
    bar_close_time_utc: the earliest moment the signal could have been
        known -- the CLOSE of the bar that triggered
        trade_candidate_ready (NOT the bar's open/timestamp -- a bar
        isn't fully formed, and therefore isn't actually knowable,
        until it closes).
    real_fill_time_utc: when the real order actually filled, per the
        broker's own position-open timestamp.

    Positive = normal (fill happened after the bar closed, as expected).
    Negative would indicate something is wrong with either timestamp
    (a fill can't happen before the signal that caused it existed) --
    report.py flags this rather than silently accepting a negative gap.
    """
    return (real_fill_time_utc - bar_close_time_utc).total_seconds()


def summarize_slippage(values: list[float]) -> dict:
    """Basic descriptive stats for a list of slippage_pips values (entry
    or exit) -- mean, median, min, max, count. Returns all-None if the
    list is empty, rather than raising, so a reconciliation report with
    zero eligible trades so far still prints cleanly."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    median = (
        sorted_vals[n // 2] if n % 2 == 1
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    )
    return {
        "count": n,
        "mean": sum(values) / n,
        "median": median,
        "min": min(values),
        "max": max(values),
    }