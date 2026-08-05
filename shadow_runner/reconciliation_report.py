"""
Phase 4 step 3 Part 3: runnable reconciliation report.

Run inside the Hetzner container (same pattern as any other one-off
script against this project's DB):

    python -m shadow_runner.reconciliation_report --user-id <uuid> --model fvg

Queries every trade for this (user, model) that has real fill data
(real_fill_price IS NOT NULL -- i.e. a real order genuinely filled, not
just a shadow-mode journal entry), computes entry/exit slippage and the
signal-to-fill timing gap for each, and prints both per-trade detail and
aggregate summary stats.

Prints a clear "0 trades found" message rather than an empty/confusing
report if nothing qualifies yet -- expected output until FVG (or any
model) is actually flipped to 'active' and has real fills.
"""
import argparse
import datetime

from app.core.database import SessionLocal
from app.models import Event, Trade
from shadow_runner.reconciliation import (
    entry_slippage_pips,
    exit_slippage_pips,
    real_realized_r,
    summarize_slippage,
    timing_gap_seconds,
)

BAR_DURATION = datetime.timedelta(minutes=5)


def _find_triggering_event(db, trade: Trade) -> Event | None:
    """Finds the pending_order_placed event that caused this trade's
    real order -- its timestamp is the triggering bar's OPEN time (see
    day_orchestrator.py's convention); add BAR_DURATION to get the
    bar's CLOSE time, the actual "earliest the signal could have been
    known" moment timing_gap_seconds() needs."""
    candidates = (
        db.query(Event)
        .filter(
            Event.user_id == trade.user_id,
            Event.model == trade.model,
            Event.event_type == "pending_order_placed",
        )
        .all()
    )
    for e in candidates:
        details = e.details or {}
        if (
            details.get("direction") == trade.direction
            and abs(details.get("entry", -1e9) - trade.entry_price) < 1e-9
        ):
            return e
    return None


def build_report(db, user_id: str, model: str) -> dict:
    trades = (
        db.query(Trade)
        .filter(Trade.user_id == user_id, Trade.model == model, Trade.real_fill_price.isnot(None))
        .order_by(Trade.entry_time_utc.asc())
        .all()
    )

    per_trade = []
    entry_slippages = []
    exit_slippages = []
    timing_gaps = []
    negative_timing_gap_count = 0

    for t in trades:
        entry_slip = entry_slippage_pips(t.direction, t.entry_price, t.real_fill_price)
        entry_slippages.append(entry_slip)

        exit_slip = None
        real_r = None
        if t.real_close_price is not None:
            exit_slip = exit_slippage_pips(t.direction, t.exit_price, t.real_close_price)
            exit_slippages.append(exit_slip)
            real_r = real_realized_r(t.direction, t.real_fill_price, t.stop_price, t.real_close_price)

        gap_seconds = None
        triggering_event = _find_triggering_event(db, t)
        if triggering_event is not None and t.real_fill_time_utc is not None:
            bar_close = triggering_event.timestamp + BAR_DURATION
            gap_seconds = timing_gap_seconds(bar_close, t.real_fill_time_utc)
            if gap_seconds < 0:
                negative_timing_gap_count += 1
            else:
                timing_gaps.append(gap_seconds)

        per_trade.append(
            {
                "trade_id": str(t.trade_id),
                "entry_time_ny": t.entry_time_ny,
                "direction": t.direction,
                "entry_slippage_pips": round(entry_slip, 2),
                "exit_slippage_pips": round(exit_slip, 2) if exit_slip is not None else None,
                "simulated_realized_r": round(t.realized_r, 3) if t.realized_r is not None else None,
                "real_realized_r": round(real_r, 3) if real_r is not None else None,
                "timing_gap_seconds": gap_seconds,
                "real_status": t.real_status,
            }
        )

    return {
        "user_id": user_id,
        "model": model,
        "trade_count": len(trades),
        "per_trade": per_trade,
        "entry_slippage_summary": summarize_slippage(entry_slippages),
        "exit_slippage_summary": summarize_slippage(exit_slippages),
        "timing_gap_summary_seconds": summarize_slippage(timing_gaps),
        "negative_timing_gap_count": negative_timing_gap_count,  # see print_report()'s warning
    }


def print_report(report: dict) -> None:
    print("=" * 70)
    print(f"RECONCILIATION REPORT -- user={report['user_id']} model={report['model']}")
    print("=" * 70)

    if report["trade_count"] == 0:
        print(
            "\nNo trades with real fill data found yet. This is expected until "
            "this model is flipped to 'active' in model_configs and has at least "
            "one real fill -- not an error."
        )
        return

    print(f"\nTrades with real fill data: {report['trade_count']}\n")

    print("Per-trade detail:")
    for row in report["per_trade"]:
        gap_str = f"{row['timing_gap_seconds']:.1f}s" if row["timing_gap_seconds"] is not None else "unknown"
        print(
            f"  {row['entry_time_ny']} {row['direction']:5s} "
            f"entry_slip={row['entry_slippage_pips']:+.2f}pips "
            f"exit_slip={row['exit_slippage_pips']}pips "
            f"sim_R={row['simulated_realized_r']} real_R={row['real_realized_r']} "
            f"gap={gap_str} status={row['real_status']}"
        )

    print("\nEntry slippage summary (pips, positive = cost you money):")
    print(f"  {report['entry_slippage_summary']}")
    print("\nExit slippage summary (pips, positive = cost you money):")
    print(f"  {report['exit_slippage_summary']}")
    print("\nSignal-to-fill timing gap summary (seconds):")
    print(f"  {report['timing_gap_summary_seconds']}")

    if report["negative_timing_gap_count"] > 0:
        print(
            f"\nWARNING: {report['negative_timing_gap_count']} trade(s) had a NEGATIVE "
            f"timing gap (fill recorded as happening before the triggering bar even "
            f"closed) -- excluded from the timing summary above. This points to a "
            f"timestamp bug somewhere (bar-close-time calculation, or the real fill "
            f"time itself), not a real 0-latency fill -- investigate before trusting "
            f"any timing numbers from this report."
        )


def main():
    parser = argparse.ArgumentParser(description="Real-vs-simulated reconciliation report")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = build_report(db, args.user_id, args.model)
    finally:
        db.close()
    print_report(report)


if __name__ == "__main__":
    main()