"""
One-off, run-by-hand script -- historical reconciliation, Piece A
(2026-09-04 design, see misty-seeking-crescent.md's "Historical
reconciliation -- Aug 10 through Sept 4, 2026" section for the full
plan). Reconstructs the raid/MSS/FVG/candidate detection journal for
every day in that window that isn't already journaled -- narrative
ONLY, never a real trade record and never a real order (reuses
_decide_day(historical=True)'s existing guard, the same structural
protection the live cross-day-recovery path already relies on -- see
runner.py's own docstring on _replay_historical_day()).

Real trade reconciliation (actual fills/exits/profit against the
broker) is deliberately NOT part of this script -- that's Piece B of
the same plan, a separate, higher-risk piece touching the live bridge,
requiring its own dedicated validation session. This script only ever
writes narrative events, exactly like _recover_cross_day_gap()'s own
replay step already does for a recent gap -- just reaching much
further back.

Idempotent, safe to re-run: skips any date that already has journaled
events (get_last_event_timestamp_for_date), so re-running this after a
partial run, or after ordinary polling has since covered part of the
range, does not duplicate anything.

Run via: docker compose run --rm shadow_runner python -m shadow_runner.scripts.backfill_narrative_aug10_sept4_2026
"""
import datetime
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from shadow_runner.bridge_client import BridgeClient
from shadow_runner.config import ShadowRunnerConfig
from shadow_runner.persistence import get_last_event_timestamp_for_date
from shadow_runner.runner import ShadowRunner

NY_TZ = ZoneInfo("America/New_York")
START_DATE = datetime.date(2026, 8, 10)

# ~25 calendar days of M5 bars, generous buffer over the ~7000-7200
# actually expected (see the plan's own estimate) -- pagination stops
# itself early once enough are collected or a page comes back short,
# so asking for more than needed here costs nothing extra in practice.
TOTAL_BARS_NEEDED = 9000


def dates_in_range(start_date: datetime.date, today: datetime.date) -> list[datetime.date]:
    """Every calendar date from start_date up to (not including) today --
    same `while d < today: ...` pattern _recover_cross_day_gap() already
    uses for its own missed_dates computation (runner.py)."""
    dates = []
    d = start_date
    while d < today:
        dates.append(d)
        d += datetime.timedelta(days=1)
    return dates


def dates_to_replay(db, model: str, candidate_dates: list[datetime.date]) -> list[datetime.date]:
    """Filters candidate_dates down to only those with no journaled
    events yet -- the idempotency guard that makes this script safe to
    re-run any number of times without duplicating already-covered days
    (whether from a prior partial run of this same script, or from
    ordinary polling having since covered part of the range)."""
    return [d for d in candidate_dates if get_last_event_timestamp_for_date(db, model, d) is None]


def run_backfill(runner: ShadowRunner, db, symbol: str, model: str, to_replay: list[datetime.date],
                  total_bars_needed: int = TOTAL_BARS_NEEDED) -> None:
    """The actual replay loop -- fetches bars once (paginated, reaching
    as far back as needed), then replays each date with bars via the
    already-tested _replay_historical_day()/_decide_day(historical=True)
    (never modified by this script, see the module docstring). A date
    with zero bars in the fetch (weekend/holiday, or genuinely beyond
    reach) is left as an honest gap, same convention
    _recover_cross_day_gap() already established -- no fabricated event."""
    if not to_replay:
        print("Nothing to do -- every date in range already has journaled events.")
        return

    print(f"Fetching up to {total_bars_needed} bars ({symbol}, M5)...")
    candles = runner.bridge.get_candles_paginated(symbol, "M5", total_bars_needed)
    if candles:
        print(f"Got {len(candles)} bars, spanning {candles[0]['time_ny']} -> {candles[-1]['time_ny']}")
    else:
        print("Got 0 bars")

    for date in to_replay:
        day_bars = [b for b in candles if b["time_ny"].date() == date]
        if not day_bars:
            print(f"{date}: no bars available (weekend/holiday, or beyond this fetch's reach) -- honest gap, skipping.")
            continue
        print(f"{date}: replaying {len(day_bars)} bars...")
        runner._replay_historical_day(date, day_bars)

    print("DONE")


def main():
    config = ShadowRunnerConfig()
    bridge = BridgeClient(config.bridge_url)
    runner = ShadowRunner(config, bridge, SessionLocal)

    today = datetime.datetime.now(NY_TZ).date()
    missed_dates = dates_in_range(START_DATE, today)

    db = SessionLocal()
    try:
        to_replay = dates_to_replay(db, config.model, missed_dates)
    finally:
        db.close()

    print(f"Range: {START_DATE} -> {today} ({len(missed_dates)} calendar days)")
    print(f"Already journaled, skipping: {len(missed_dates) - len(to_replay)} days")
    print(f"To replay: {len(to_replay)} days")

    run_backfill(runner, db, config.symbol, config.model, to_replay)


if __name__ == "__main__":
    main()
