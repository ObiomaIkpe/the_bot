"""
ANALYSIS SCRIPT -- same-day multi-candidate frequency, for the "should we
relax the one-trade-per-day rule" question.

WHAT THIS IS: a careful, minimal modification of extract_golden_master.py
(itself verbatim from the locked FVG_model.py plus log_event() calls).
The ONLY behavioral change from extract_golden_master.py: the original
script's inner logic sets `trade_found = True` and immediately `break`s
out of the day's raid-scanning loop the moment the FIRST candidate fills
-- meaning it never even looks at whether a LATER raid that same day
would also have filled. This script removes that early exit so every
candidate in the kill zone gets evaluated, and records all of them, not
just the first to fill.

Every other line of trading logic -- raid detection, MSS confirmation,
FVG detection, entry/stop/target calculation, fill search, win/loss/
scratch determination -- is IDENTICAL to extract_golden_master.py /
FVG_model.py. If you diff this against extract_golden_master.py and find
changes beyond "don't break early, record every filled candidate instead
of just the first," that's a bug in this file.

WHY THIS MATTERS BEYOND JUST A COUNT: "how many days had 2+ profitable
candidates" is only half the question. The other half -- the one that
actually determines whether relaxing the one-trade-per-day rule is a
free win or a real tradeoff -- is whether those extra candidates were
open AT THE SAME TIME as the day's currently-selected trade, or fully
SEQUENTIALLY (one closed before the next opened). Two positions open
simultaneously on the same instrument is real added peak risk (more
capital exposed at once, correlated exposure since it's the same pair);
two positions that never overlap in time is a much closer to a genuinely
free incremental edge. This script reports both, separately.

NOT RUNNABLE IN THIS SANDBOX -- same constraint as extract_golden_master.py:
no network access, no DAT_ASCII_EURUSD_M1_*.csv files here. Run this in
the same environment where extract_golden_master.py (or FVG_model.py)
already runs, against the same 10.5 years of data files.

Usage:
    python analyze_same_day_multi_candidate.py

Output: prints a report to stdout. Also writes
same_day_multi_candidate_report.json with the full per-day breakdown,
for further inspection.
"""
import pandas as pd
import numpy as np
import glob
import datetime
import bisect
import json
from zoneinfo import ZoneInfo

PIP = 0.0001
PIVOT_N = 2
SWING_N = 2
MIN_STOP_PIPS = 5

# ---------- Load & prep data (verbatim from extract_golden_master.py) ----------
files = sorted(glob.glob("DAT_ASCII_EURUSD_M1_*.csv"))
frames = []
for fp in files:
    d = pd.read_csv(fp, sep=";", names=["ts", "open", "high", "low", "close", "volume"])
    frames.append(d)
df = pd.concat(frames, ignore_index=True)
df["ts"] = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S")
df["ts_utc"] = df["ts"] + pd.Timedelta(hours=5)
df["ts"] = df["ts_utc"].dt.tz_localize("UTC").dt.tz_convert(ZoneInfo("America/New_York")).dt.tz_localize(None)
df = df.drop(columns=["ts_utc"]).set_index("ts").sort_index()
df = df[~df.index.duplicated(keep="first")]
print(f"Loaded {len(files)} files, {len(df)} 1-min bars")

df_5m = df.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
print(f"Resampled to {len(df_5m)} 5-min bars")

# ---------- Daily trend (verbatim) ----------
daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
d_highs, d_lows = daily["high"].values, daily["low"].values
n_days = len(daily)
swing_high_idx, swing_low_idx = [], []
for i in range(PIVOT_N, n_days - PIVOT_N):
    if d_highs[i] == max(d_highs[i - PIVOT_N:i + PIVOT_N + 1]):
        swing_high_idx.append(i)
    if d_lows[i] == min(d_lows[i - PIVOT_N:i + PIVOT_N + 1]):
        swing_low_idx.append(i)


def daily_trend_as_of(trade_date):
    idx_lookup = daily.index[daily["ts"].dt.date == trade_date]
    if len(idx_lookup) == 0:
        return None
    cutoff = idx_lookup[0]
    ch = [i for i in swing_high_idx if i + PIVOT_N < cutoff]
    cl = [i for i in swing_low_idx if i + PIVOT_N < cutoff]
    if len(ch) < 2 or len(cl) < 2:
        return None
    h1, h2 = d_highs[ch[-2]], d_highs[ch[-1]]
    l1, l2 = d_lows[cl[-2]], d_lows[cl[-1]]
    if h2 > h1 and l2 > l1:
        return "up"
    if h2 < h1 and l2 < l1:
        return "down"
    return None


def find_fvg(highs, lows, i, direction):
    if i < 2:
        return None
    if direction == "bear":
        if lows[i - 2] > highs[i]:
            return {"top": lows[i - 2], "bottom": highs[i], "frame_idx": i - 2}
    else:
        if highs[i - 2] < lows[i]:
            return {"top": lows[i], "bottom": highs[i - 2], "frame_idx": i - 2}
    return None


# ---------- FOMC dates (verbatim) ----------
FOMC_DATES = {
    datetime.date(2016, 1, 27), datetime.date(2016, 3, 16), datetime.date(2016, 4, 27),
    datetime.date(2016, 6, 15), datetime.date(2016, 7, 27), datetime.date(2016, 9, 21),
    datetime.date(2016, 11, 2), datetime.date(2016, 12, 14), datetime.date(2017, 2, 1),
    datetime.date(2017, 3, 15), datetime.date(2017, 5, 3), datetime.date(2017, 6, 14),
    datetime.date(2017, 7, 26), datetime.date(2017, 9, 20), datetime.date(2017, 11, 1),
    datetime.date(2017, 12, 13), datetime.date(2018, 1, 31), datetime.date(2018, 3, 21),
    datetime.date(2018, 5, 2), datetime.date(2018, 6, 13), datetime.date(2018, 8, 1),
    datetime.date(2018, 9, 26), datetime.date(2018, 11, 8), datetime.date(2018, 12, 19),
    datetime.date(2019, 1, 30), datetime.date(2019, 3, 20), datetime.date(2019, 5, 1),
    datetime.date(2019, 6, 19), datetime.date(2019, 7, 31), datetime.date(2019, 9, 18),
    datetime.date(2019, 10, 30), datetime.date(2019, 12, 11), datetime.date(2020, 1, 29),
    datetime.date(2020, 3, 18), datetime.date(2020, 4, 29), datetime.date(2020, 6, 10),
    datetime.date(2020, 7, 29), datetime.date(2020, 9, 16), datetime.date(2020, 11, 5),
    datetime.date(2020, 12, 16), datetime.date(2021, 1, 27), datetime.date(2021, 3, 17),
    datetime.date(2021, 4, 28), datetime.date(2021, 6, 16), datetime.date(2021, 7, 28),
    datetime.date(2021, 9, 22), datetime.date(2021, 11, 3), datetime.date(2021, 12, 15),
    datetime.date(2022, 1, 26), datetime.date(2022, 3, 16), datetime.date(2022, 5, 4),
    datetime.date(2022, 6, 15), datetime.date(2022, 7, 27), datetime.date(2022, 9, 21),
    datetime.date(2022, 11, 2), datetime.date(2022, 12, 14), datetime.date(2023, 2, 1),
    datetime.date(2023, 3, 22), datetime.date(2023, 5, 3), datetime.date(2023, 6, 14),
    datetime.date(2023, 7, 26), datetime.date(2023, 9, 20), datetime.date(2023, 11, 1),
    datetime.date(2023, 12, 13), datetime.date(2024, 1, 31), datetime.date(2024, 3, 20),
    datetime.date(2024, 5, 1), datetime.date(2024, 6, 12), datetime.date(2024, 7, 31),
    datetime.date(2024, 9, 18), datetime.date(2024, 11, 7), datetime.date(2024, 12, 18),
    datetime.date(2025, 1, 29), datetime.date(2025, 3, 19), datetime.date(2025, 5, 7),
    datetime.date(2025, 6, 18), datetime.date(2025, 7, 30), datetime.date(2025, 9, 17),
    datetime.date(2025, 10, 29), datetime.date(2025, 12, 10), datetime.date(2026, 1, 28),
    datetime.date(2026, 3, 18), datetime.date(2026, 4, 29), datetime.date(2026, 6, 17),
}

all_days = sorted(set(df_5m.index.date))

# Per-day report: {date: [ {direction, entry, stop, target, outcome,
#   exit_price, raid_bar_index, fill_bar_index, close_bar_index}, ... ] }
# -- EVERY candidate that filled, not just the first / currently-selected one.
per_day_filled_candidates = {}

for d in all_days:
    d = pd.Timestamp(d)

    if d.date() in FOMC_DATES:
        continue
    trend = daily_trend_as_of(d.date())
    if trend not in ("up", "down"):
        continue

    ref_start = d + pd.Timedelta(hours=5)
    session_start = d + pd.Timedelta(hours=7)
    session_end = d + pd.Timedelta(hours=10)
    day_end = d + pd.Timedelta(hours=17)
    full = df_5m.loc[ref_start:day_end]
    if len(full) < 12:
        continue
    full = full.reset_index()
    ts_col = full.columns[0]

    ss_positions = full.index[full[ts_col] >= session_start]
    if len(ss_positions) == 0:
        continue
    session_start_idx = ss_positions[0]
    se_positions = full.index[full[ts_col] >= session_end]
    session_end_idx = se_positions[0] if len(se_positions) > 0 else len(full)

    highs, lows = full["high"].values, full["low"].values
    n = len(full)

    piv_high_all, piv_low_all = [], []
    for k in range(SWING_N, n - SWING_N):
        if highs[k] == max(highs[k - SWING_N:k + SWING_N + 1]):
            piv_high_all.append(k)
        if lows[k] == min(lows[k - SWING_N:k + SWING_N + 1]):
            piv_low_all.append(k)

    day_candidates = []  # every FILLED candidate this day, regardless of outcome

    # CHANGED FROM extract_golden_master.py: no `trade_found`/early break --
    # every raid bar in the kill zone gets evaluated, not just the first
    # one that leads to a fill.
    for i in range(session_start_idx, session_end_idx):
        if trend == "up":
            pl_pos = bisect.bisect_left(piv_low_all, i - SWING_N)
            ph_pos = bisect.bisect_left(piv_high_all, i - SWING_N)
            if pl_pos == 0 or ph_pos == 0:
                continue
            raid_level = lows[piv_low_all[pl_pos - 1]]
            if lows[i] >= raid_level:
                continue
            recent_high_level = highs[piv_high_all[ph_pos - 1]]
            closes = full["close"].values
            for j in range(i + 1, min(i + 10, n)):
                if closes[j] > recent_high_level:
                    fvg = find_fvg(highs, lows, j, "bull")
                    if not fvg:
                        continue
                    entry_price = (fvg["top"] + fvg["bottom"]) / 2
                    stop = lows[fvg["frame_idx"]]
                    risk = entry_price - stop
                    if risk / PIP < MIN_STOP_PIPS:
                        continue
                    for p in range(j + 1, n):
                        if lows[p] <= entry_price:
                            if p < 6:
                                break
                            window_highs = highs[p - 6:p]
                            extreme_idx = p - 6 + int(np.argmax(window_highs))
                            target = (highs[extreme_idx] + lows[extreme_idx]) / 2
                            if target <= entry_price:
                                break
                            outcome, exit_price, close_bar = None, None, None
                            for q in range(p, n):
                                if lows[q] <= stop:
                                    outcome, exit_price, close_bar = "loss", stop, q
                                    break
                                if highs[q] >= target:
                                    outcome, exit_price, close_bar = "win", target, q
                                    break
                            if outcome is None:
                                outcome, exit_price, close_bar = "scratch", full["close"].values[-1], n - 1
                            realized_r = (exit_price - entry_price) / risk if risk else 0.0
                            day_candidates.append(dict(
                                direction="long", entry=float(entry_price), stop=float(stop),
                                target=float(target), outcome=outcome, exit_price=float(exit_price),
                                realized_r=float(realized_r),
                                raid_bar_index=int(i), fill_bar_index=int(p), close_bar_index=int(close_bar),
                            ))
                            break
                    break  # move to next raid bar i (still no early day-level break)

        else:
            pl_pos = bisect.bisect_left(piv_low_all, i - SWING_N)
            ph_pos = bisect.bisect_left(piv_high_all, i - SWING_N)
            if pl_pos == 0 or ph_pos == 0:
                continue
            raid_level = highs[piv_high_all[ph_pos - 1]]
            if highs[i] <= raid_level:
                continue
            recent_low_level = lows[piv_low_all[pl_pos - 1]]
            closes = full["close"].values
            for j in range(i + 1, min(i + 10, n)):
                if closes[j] < recent_low_level:
                    fvg = find_fvg(highs, lows, j, "bear")
                    if not fvg:
                        continue
                    entry_price = (fvg["top"] + fvg["bottom"]) / 2
                    stop = highs[fvg["frame_idx"]]
                    risk = stop - entry_price
                    if risk / PIP < MIN_STOP_PIPS:
                        continue
                    for p in range(j + 1, n):
                        if highs[p] >= entry_price:
                            if p < 6:
                                break
                            window_lows = lows[p - 6:p]
                            extreme_idx = p - 6 + int(np.argmin(window_lows))
                            target = (highs[extreme_idx] + lows[extreme_idx]) / 2
                            if target >= entry_price:
                                break
                            outcome, exit_price, close_bar = None, None, None
                            for q in range(p, n):
                                if highs[q] >= stop:
                                    outcome, exit_price, close_bar = "loss", stop, q
                                    break
                                if lows[q] <= target:
                                    outcome, exit_price, close_bar = "win", target, q
                                    break
                            if outcome is None:
                                outcome, exit_price, close_bar = "scratch", full["close"].values[-1], n - 1
                            realized_r = (entry_price - exit_price) / risk if risk else 0.0
                            day_candidates.append(dict(
                                direction="short", entry=float(entry_price), stop=float(stop),
                                target=float(target), outcome=outcome, exit_price=float(exit_price),
                                realized_r=float(realized_r),
                                raid_bar_index=int(i), fill_bar_index=int(p), close_bar_index=int(close_bar),
                            ))
                            break
                    break

    if day_candidates:
        per_day_filled_candidates[str(d.date())] = day_candidates


# ---------- Aggregate the actual question ----------
days_with_2plus_filled = 0
days_with_2plus_winners = 0
days_with_2plus_positive_r = 0
sequential_extra_winner_days = 0     # 2+ winners, NONE overlap in time with the primary (earliest raid_bar) one
overlapping_extra_winner_days = 0    # 2+ winners, at least one overlaps in time with the primary one
extra_sequential_r_sum = 0.0         # sum of realized_r from sequential-safe extra winners only

for date, candidates in per_day_filled_candidates.items():
    if len(candidates) < 2:
        continue
    days_with_2plus_filled += 1

    winners = [c for c in candidates if c["outcome"] == "win"]
    positive_r = [c for c in candidates if c["realized_r"] > 0]
    if len(winners) >= 2:
        days_with_2plus_winners += 1
    if len(positive_r) >= 2:
        days_with_2plus_positive_r += 1

    if len(winners) < 2:
        continue

    # "Primary" = earliest raid_bar_index, matching the model's own
    # (raid_bar, mss_bar) priority rule -- this is the ONE trade the
    # current one-trade-per-day rule actually takes.
    primary = min(candidates, key=lambda c: (c["raid_bar_index"], c["fill_bar_index"]))
    other_winners = [c for c in winners if c is not primary]
    if not other_winners:
        continue

    any_overlap = any(
        not (c["close_bar_index"] < primary["fill_bar_index"] or c["fill_bar_index"] > primary["close_bar_index"])
        for c in other_winners
    )
    if any_overlap:
        overlapping_extra_winner_days += 1
    else:
        sequential_extra_winner_days += 1
        extra_sequential_r_sum += sum(c["realized_r"] for c in other_winners)

total_tradeable_days = sum(1 for _ in per_day_filled_candidates)  # days with >=1 filled candidate

print("\n" + "=" * 70)
print("SAME-DAY MULTI-CANDIDATE ANALYSIS")
print("=" * 70)
print(f"Days with >=1 filled candidate (i.e. current 603-trade days' pool): {total_tradeable_days}")
print(f"Days with 2+ candidates that FILLED (any outcome):                  {days_with_2plus_filled}")
print(f"  Of those, days with 2+ that were outright WINS:                  {days_with_2plus_winners}")
print(f"  Of those, days with 2+ that had ANY positive realized R:         {days_with_2plus_positive_r}")
print()
print(f"Of the {days_with_2plus_winners} multi-winner days:")
print(f"  Extra winner(s) fully SEQUENTIAL (no overlap w/ the day's primary trade): {sequential_extra_winner_days}")
print(f"  Extra winner(s) OVERLAPPING in time with the primary trade (real added risk): {overlapping_extra_winner_days}")
print()
print(f"Sum of realized R from sequential-safe extra winners only: {extra_sequential_r_sum:.2f}R")
print(f"(This is the closest thing to a 'free' incremental-edge number --")
print(f" the overlapping-day winners are NOT included here, since taking")
print(f" them would have meant genuinely more capital at risk at once,")
print(f" not a free addition.)")

with open("same_day_multi_candidate_report.json", "w") as f:
    json.dump(per_day_filled_candidates, f, indent=2)
print("\nFull per-day breakdown written to same_day_multi_candidate_report.json")