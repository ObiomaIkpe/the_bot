"""
validate_against_reference.py -- runs the full Cascade Raid streaming
pipeline (signal detection -> HTF bias filter -> position management)
against real EURUSD HistData and compares the resulting trades against
the reference package's own trade-log CSVs.

Usage:
    venv/bin/python -m cascade_raid.validate_against_reference \\
        --data-glob '/path/to/DAT_ASCII_EURUSD_M1_*.csv' \\
        --reference-csv '/path/to/CASCADE_RAID_3min_trade_log_full.csv' \\
        --variant 4slot

KNOWN, EXPECTED LIMITATION for this dev environment: the available
HistData doesn't cover the full 2016-01 -> 2026-07 window the reference
was locked against (2016, 2021-2023 are missing outright; see
PENDING_ITEMS.md for the exact gap). Running this against a partial,
non-contiguous window means the very first few trades in each
contiguous block can genuinely mismatch the reference for reasons that
are NOT bugs -- the swing-detection pools warm up within ~10-15 bars
(fast, bounded), but the circuit breaker's `skip_remaining`/
`consecutive_losses` state carries no such natural reset: this script
starts every run at skip_remaining=0, while the reference's real state
at that exact historical moment could have been mid-cooldown from
trades before the window even starts. Report mismatches; investigate
whether each one is explained by this known warm-up gap or is a real
bug before assuming either.
"""
import argparse
import csv
import datetime

from cascade_raid.data_pipeline import (
    build_daily,
    build_m3,
    build_monthly,
    build_weekly,
    load_histdata_m1,
    next_friday_close,
)
from cascade_raid.streaming.htf_bias import FractalBiasTracker, full_agreement
from cascade_raid.streaming.position_manager import PositionManager
from cascade_raid.streaming.signal_detector import detect_signals

VARIANTS = {
    "4slot": dict(max_concurrent_positions=4, breaker_threshold=2, breaker_skip=6),
    "3slot": dict(max_concurrent_positions=3, breaker_threshold=2, breaker_skip=6),
}


def _asof_bias(query_time, bar_times, bias_values):
    """Most recent bar strictly before query_time -- mirrors the
    reference's own asof_bias() (searchsorted side='right' - 1)."""
    pos = bar_times.searchsorted(query_time, side="right") - 1
    if pos < 0:
        return None
    return bias_values[pos]


def detect_and_filter_signals(m1, m3):
    print(f"  {len(m1)} M1 bars -> {len(m3)} M3 bars")

    raw_signals = detect_signals(m3["high"].values, m3["low"].values, m3["close"].values)
    for s in raw_signals:
        s["signal_time"] = m3.index[s["signal_idx"]]
    print(f"  {len(raw_signals)} raw signals detected")

    daily = build_daily(m1)
    weekly = build_weekly(daily)
    monthly = build_monthly(m1)

    monthly_tracker = FractalBiasTracker(wing=1)
    monthly_bias_values = [monthly_tracker.update(c) for c in monthly["close"].values]
    weekly_tracker = FractalBiasTracker(wing=1)
    weekly_bias_values = [weekly_tracker.update(c) for c in weekly["close"].values]
    daily_tracker = FractalBiasTracker(wing=1)
    daily_bias_values = [daily_tracker.update(c) for c in daily["close"].values]

    aligned = []
    for s in raw_signals:
        m_bias = _asof_bias(s["signal_time"], monthly.index, monthly_bias_values)
        w_bias = _asof_bias(s["signal_time"], weekly.index, weekly_bias_values)
        d_bias = _asof_bias(s["signal_time"], daily.index, daily_bias_values)
        agreed = full_agreement(m_bias, w_bias, d_bias)
        sig_dir_word = "bullish" if s["direction"] == "long" else "bearish"
        if agreed == sig_dir_word:
            aligned.append(s)
    print(f"  {len(aligned)} bias-aligned signals")
    return sorted(aligned, key=lambda s: s["signal_time"])


def run_variant(m1, m3, variant_params) -> list[dict]:
    aligned_sorted = detect_and_filter_signals(m1, m3)

    m1_index = m1.index
    for s in aligned_sorted:
        s["signal_idx"] = m1_index.searchsorted(s["signal_time"])

    pm = PositionManager(**variant_params)
    pm.process_signals(
        aligned_sorted,
        m1["high"].values, m1["low"].values, m1["close"].values,
        len(m1),
        cutoff_idx_fn=lambda fill_idx: m1_index.searchsorted(next_friday_close(m1_index[fill_idx]), side="right") - 1,
    )

    trades = []
    for t, _risk_pct in pm.trades:
        trades.append(dict(
            direction=t["direction"],
            entry=t["entry"],
            stop=t["stop"],
            target=t["target"],
            outcome=t["outcome"],
            exit_price=t["exit_price"],
            entry_dt=m1_index[t["fill_idx"]],
            exit_dt=m1_index[t["exit_idx"]],
        ))
    return trades


def load_reference_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(dict(
                direction=row["direction"],
                entry=round(float(row["entry"]), 5),
                stop=round(float(row["stop"]), 5),
                target=round(float(row["target"]), 5),
                outcome=row["outcome"],
                exit_price=round(float(row["exit_price"]), 5),
                entry_dt=row["entry_datetime"],
                exit_dt=row["exit_datetime"],
            ))
    return rows


def compare(mine: list[dict], reference: list[dict], date_from: datetime.date, date_to: datetime.date):
    ref_in_window = [
        r for r in reference
        if date_from <= datetime.datetime.fromisoformat(r["entry_dt"]).date() <= date_to
    ]
    mine_in_window = [t for t in mine if date_from <= t["entry_dt"].date() <= date_to]

    def key(t):
        return (str(t["entry_dt"])[:16], t["direction"], round(t["entry"], 5))

    mine_by_key = {key(t): t for t in mine_in_window}
    ref_by_key = {key(t): t for t in ref_in_window}

    matched = 0
    field_mismatches = []
    for k, ref_t in ref_by_key.items():
        mine_t = mine_by_key.get(k)
        if mine_t is None:
            continue
        matched += 1
        if mine_t["outcome"] != ref_t["outcome"] or round(mine_t["exit_price"], 5) != ref_t["exit_price"]:
            field_mismatches.append((k, ref_t, mine_t))

    missing_from_mine = sorted(set(ref_by_key) - set(mine_by_key))
    extra_in_mine = sorted(set(mine_by_key) - set(ref_by_key))

    print(f"\n  Reference trades in window: {len(ref_in_window)}")
    print(f"  My trades in window:        {len(mine_in_window)}")
    print(f"  Matched by (time, direction, entry): {matched}")
    print(f"  In reference but not mine:  {len(missing_from_mine)}")
    print(f"  In mine but not reference:  {len(extra_in_mine)}")
    print(f"  Matched-but-different-outcome/exit: {len(field_mismatches)}")

    if missing_from_mine[:5]:
        print("\n  First few missing (in reference, not mine):")
        for k in missing_from_mine[:5]:
            print("   ", k, ref_by_key[k])
    if extra_in_mine[:5]:
        print("\n  First few extra (in mine, not reference):")
        for k in extra_in_mine[:5]:
            print("   ", k, mine_by_key[k])
    if field_mismatches[:5]:
        print("\n  First few field mismatches:")
        for k, ref_t, mine_t in field_mismatches[:5]:
            print("   ", k, "ref=", ref_t["outcome"], ref_t["exit_price"], "mine=", mine_t["outcome"], mine_t["exit_price"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-glob", required=True)
    parser.add_argument("--reference-csv", required=True)
    parser.add_argument("--variant", choices=list(VARIANTS), required=True)
    parser.add_argument("--date-from", default="2018-01-01")
    parser.add_argument("--date-to", default="2026-07-31")
    args = parser.parse_args()

    print(f"Loading M1 data from {args.data_glob} ...")
    m1 = load_histdata_m1(args.data_glob)
    print(f"  {len(m1)} M1 bars, {m1.index[0]} -> {m1.index[-1]}")
    m3 = build_m3(m1)

    print(f"\nRunning variant={args.variant} ...")
    mine = run_variant(m1, m3, VARIANTS[args.variant])

    print(f"\nLoading reference CSV {args.reference_csv} ...")
    reference = load_reference_csv(args.reference_csv)

    date_from = datetime.date.fromisoformat(args.date_from)
    date_to = datetime.date.fromisoformat(args.date_to)
    compare(mine, reference, date_from, date_to)


if __name__ == "__main__":
    main()
