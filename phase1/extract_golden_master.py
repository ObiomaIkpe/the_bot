"""
GOLDEN-MASTER EXTRACTION -- instrumented copy of FVG_model.py

Purpose: produce a complete event-by-event trail of everything the LOCKED
FVG model notices during its full 10.5-year run -- not just the final 603
trades, but every confirmed swing, raid, MSS, FVG, fill, and close -- so
the Phase 1 streaming state machine can be checked against this trail at
every stage, not just the final trade list.

CRITICAL CONSTRAINT: this file must not change the model's behavior in
any way. Every line of trading logic below is copied verbatim from
FVG_model.py. The only additions are `log_event(...)` calls, which are
pure side effects (append to a list) and never influence control flow.
If you diff this file against FVG_model.py and find anything other than
added log_event(...) lines and the logging plumbing at the top/bottom,
that's a bug in THIS file, not a license to change the model.

Output: writes `golden_master_events.jsonl` (one JSON object per line, in
the exact chronological order the batch model encountered them) and
`golden_master_trades.jsonl` (the final trade list, same as the original
script's trades_FVG_noFOMC.pkl but in an easier-to-diff format).

NOT RUN YET -- this sandbox has no network access and doesn't have the
DAT_ASCII_EURUSD_M1_*.csv source files, so this has only been
syntax-checked, not executed. Run it in the same environment where
FVG_model.py itself runs, against the same data files.
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
RR = 2.0
MIN_STOP_PIPS = 5

# ---------- Golden-master event logging ----------
events = []


def log_event(event_type, timestamp, **details):
    """
    timestamp should be a pandas.Timestamp (NY-naive, matching the rest of
    this script's convention) or None for events not tied to a specific bar.
    Appended in encounter order -- this IS the chronological order, since
    the outer loop already walks days in order and inner loops walk bars
    forward within each day.
    """
    events.append(
        {
            "event_type": event_type,
            "timestamp": timestamp.isoformat() if timestamp is not None else None,
            **details,
        }
    )


# ---------- Load & prep data (verbatim from FVG_model.py) ----------
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
        # GOLDEN-MASTER LOG: a daily swing high was confirmed
        log_event(
            "daily_swing_high_confirmed",
            daily["ts"].iloc[i],
            price=float(d_highs[i]),
            day_index=int(i),
        )
    if d_lows[i] == min(d_lows[i - PIVOT_N:i + PIVOT_N + 1]):
        swing_low_idx.append(i)
        log_event(
            "daily_swing_low_confirmed",
            daily["ts"].iloc[i],
            price=float(d_lows[i]),
            day_index=int(i),
        )


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
    """3-candle FVG ending at candle i."""
    if i < 2:
        return None
    if direction == "bear":
        if lows[i - 2] > highs[i]:
            return {"top": lows[i - 2], "bottom": highs[i], "frame_idx": i - 2}
    else:
        if highs[i - 2] < lows[i]:
            return {"top": lows[i], "bottom": highs[i - 2], "frame_idx": i - 2}
    return None


# ---------- FOMC dates (verbatim from FVG_model.py) ----------
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
trades = []

for d in all_days:
    d = pd.Timestamp(d)

    if d.date() in FOMC_DATES:
        log_event("day_skipped_fomc", d)
        continue

    trend = daily_trend_as_of(d.date())
    if trend not in ("up", "down"):
        log_event("day_skipped_no_trend", d, trend=trend)
        continue

    log_event("day_trend_determined", d, trend=trend)

    ref_start = d + pd.Timedelta(hours=5)
    session_start = d + pd.Timedelta(hours=7)
    session_end = d + pd.Timedelta(hours=10)
    day_end = d + pd.Timedelta(hours=17)
    full = df_5m.loc[ref_start:day_end]
    if len(full) < 12:
        log_event("day_skipped_insufficient_bars", d, bar_count=int(len(full)))
        continue
    full = full.reset_index()
    ts_col = full.columns[0]

    ss_positions = full.index[full[ts_col] >= session_start]
    if len(ss_positions) == 0:
        log_event("day_skipped_no_session_start", d)
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
            log_event(
                "intraday_swing_high_confirmed",
                full[ts_col].iloc[k],
                price=float(highs[k]),
                bar_index=int(k),
            )
        if lows[k] == min(lows[k - SWING_N:k + SWING_N + 1]):
            piv_low_all.append(k)
            log_event(
                "intraday_swing_low_confirmed",
                full[ts_col].iloc[k],
                price=float(lows[k]),
                bar_index=int(k),
            )

    trade_found = False
    for i in range(session_start_idx, session_end_idx):
        if trade_found:
            break

        if trend == "up":
            pl_pos = bisect.bisect_left(piv_low_all, i - SWING_N)
            ph_pos = bisect.bisect_left(piv_high_all, i - SWING_N)
            if pl_pos == 0 or ph_pos == 0:
                continue
            raid_level = lows[piv_low_all[pl_pos - 1]]
            if lows[i] >= raid_level:
                continue

            # GOLDEN-MASTER LOG: raid detected
            log_event(
                "raid_detected",
                full[ts_col].iloc[i],
                direction="bull",
                raid_level=float(raid_level),
                raid_bar_low=float(lows[i]),
                bar_index=int(i),
            )

            recent_high_level = highs[piv_high_all[ph_pos - 1]]
            closes = full["close"].values
            for j in range(i + 1, min(i + 10, n)):
                if closes[j] > recent_high_level:
                    log_event(
                        "mss_confirmed",
                        full[ts_col].iloc[j],
                        direction="bull",
                        level=float(recent_high_level),
                        close=float(closes[j]),
                        raid_bar_index=int(i),
                        mss_bar_index=int(j),
                    )
                    fvg = find_fvg(highs, lows, j, "bull")
                    if not fvg:
                        continue
                    log_event(
                        "fvg_found",
                        full[ts_col].iloc[j],
                        direction="bull",
                        top=float(fvg["top"]),
                        bottom=float(fvg["bottom"]),
                        frame_idx=int(fvg["frame_idx"]),
                        mss_bar_index=int(j),
                    )
                    entry_price = (fvg["top"] + fvg["bottom"]) / 2
                    stop = lows[fvg["frame_idx"]]
                    risk = entry_price - stop
                    if risk / PIP < MIN_STOP_PIPS:
                        log_event(
                            "fvg_rejected_min_stop",
                            full[ts_col].iloc[j],
                            direction="bull",
                            risk_pips=float(risk / PIP),
                        )
                        continue
                    filled = False
                    outcome, exit_price = None, None
                    for p in range(j + 1, n):
                        if lows[p] <= entry_price:
                            if p < 6:
                                break
                            window_highs = highs[p - 6:p]
                            extreme_rel_idx = int(np.argmax(window_highs))
                            extreme_idx = p - 6 + extreme_rel_idx
                            target = (highs[extreme_idx] + lows[extreme_idx]) / 2
                            if target <= entry_price:
                                break
                            filled = True
                            log_event(
                                "order_filled",
                                full[ts_col].iloc[p],
                                direction="long",
                                entry=float(entry_price),
                                stop=float(stop),
                                target=float(target),
                                fill_bar_index=int(p),
                            )
                            for q in range(p, n):
                                if lows[q] <= stop:
                                    outcome, exit_price = "loss", stop
                                    log_event(
                                        "trade_closed",
                                        full[ts_col].iloc[q],
                                        direction="long",
                                        outcome="loss",
                                        exit_price=float(stop),
                                    )
                                    break
                                if highs[q] >= target:
                                    outcome, exit_price = "win", target
                                    log_event(
                                        "trade_closed",
                                        full[ts_col].iloc[q],
                                        direction="long",
                                        outcome="win",
                                        exit_price=float(target),
                                    )
                                    break
                            break
                    if not filled:
                        continue
                    if outcome is None:
                        outcome, exit_price = "scratch", full["close"].values[-1]
                        log_event(
                            "trade_closed",
                            full[ts_col].iloc[n - 1],
                            direction="long",
                            outcome="scratch",
                            exit_price=float(exit_price),
                        )
                    trades.append(dict(date=d.date(), direction="long", entry=entry_price,
                                        stop=stop, target=target, risk_pips=risk / PIP,
                                        outcome=outcome, exit_price=exit_price))
                    trade_found = True
                    break

        else:
            pl_pos = bisect.bisect_left(piv_low_all, i - SWING_N)
            ph_pos = bisect.bisect_left(piv_high_all, i - SWING_N)
            if pl_pos == 0 or ph_pos == 0:
                continue
            raid_level = highs[piv_high_all[ph_pos - 1]]
            if highs[i] <= raid_level:
                continue

            log_event(
                "raid_detected",
                full[ts_col].iloc[i],
                direction="bear",
                raid_level=float(raid_level),
                raid_bar_high=float(highs[i]),
                bar_index=int(i),
            )

            recent_low_level = lows[piv_low_all[pl_pos - 1]]
            closes = full["close"].values
            for j in range(i + 1, min(i + 10, n)):
                if closes[j] < recent_low_level:
                    log_event(
                        "mss_confirmed",
                        full[ts_col].iloc[j],
                        direction="bear",
                        level=float(recent_low_level),
                        close=float(closes[j]),
                        raid_bar_index=int(i),
                        mss_bar_index=int(j),
                    )
                    fvg = find_fvg(highs, lows, j, "bear")
                    if not fvg:
                        continue
                    log_event(
                        "fvg_found",
                        full[ts_col].iloc[j],
                        direction="bear",
                        top=float(fvg["top"]),
                        bottom=float(fvg["bottom"]),
                        frame_idx=int(fvg["frame_idx"]),
                        mss_bar_index=int(j),
                    )
                    entry_price = (fvg["top"] + fvg["bottom"]) / 2
                    stop = highs[fvg["frame_idx"]]
                    risk = stop - entry_price
                    if risk / PIP < MIN_STOP_PIPS:
                        log_event(
                            "fvg_rejected_min_stop",
                            full[ts_col].iloc[j],
                            direction="bear",
                            risk_pips=float(risk / PIP),
                        )
                        continue
                    filled = False
                    outcome, exit_price = None, None
                    for p in range(j + 1, n):
                        if highs[p] >= entry_price:
                            if p < 6:
                                break
                            window_lows = lows[p - 6:p]
                            extreme_rel_idx = int(np.argmin(window_lows))
                            extreme_idx = p - 6 + extreme_rel_idx
                            target = (highs[extreme_idx] + lows[extreme_idx]) / 2
                            if target >= entry_price:
                                break
                            filled = True
                            log_event(
                                "order_filled",
                                full[ts_col].iloc[p],
                                direction="short",
                                entry=float(entry_price),
                                stop=float(stop),
                                target=float(target),
                                fill_bar_index=int(p),
                            )
                            for q in range(p, n):
                                if highs[q] >= stop:
                                    outcome, exit_price = "loss", stop
                                    log_event(
                                        "trade_closed",
                                        full[ts_col].iloc[q],
                                        direction="short",
                                        outcome="loss",
                                        exit_price=float(stop),
                                    )
                                    break
                                if lows[q] <= target:
                                    outcome, exit_price = "win", target
                                    log_event(
                                        "trade_closed",
                                        full[ts_col].iloc[q],
                                        direction="short",
                                        outcome="win",
                                        exit_price=float(target),
                                    )
                                    break
                            break
                    if not filled:
                        continue
                    if outcome is None:
                        outcome, exit_price = "scratch", full["close"].values[-1]
                        log_event(
                            "trade_closed",
                            full[ts_col].iloc[n - 1],
                            direction="short",
                            outcome="scratch",
                            exit_price=float(exit_price),
                        )
                    trades.append(dict(date=d.date(), direction="short", entry=entry_price,
                                        stop=stop, target=target, risk_pips=risk / PIP,
                                        outcome=outcome, exit_price=exit_price))
                    trade_found = True
                    break

# ---------- Report (verbatim from FVG_model.py -- sanity check the trade count/stats
# still match 603/66.2%/+397.4%/-9.5% before trusting the event log at all) ----------
equity, curve = 100.0, [100.0]
for t in trades:
    risk_amt = equity * 0.01
    if t["outcome"] == "win":
        realized_rr = abs(t["target"] - t["entry"]) / (t["risk_pips"] * PIP)
        equity += risk_amt * realized_rr
    elif t["outcome"] == "loss":
        equity -= risk_amt
    else:
        moved = (t["exit_price"] - t["entry"]) if t["direction"] == "long" else (t["entry"] - t["exit_price"])
        frac = moved / (t["risk_pips"] * PIP)
        equity += risk_amt * frac
    curve.append(equity)

n_trades = len(trades)
wins = sum(1 for t in trades if t["outcome"] == "win")
losses = sum(1 for t in trades if t["outcome"] == "loss")
scratches = sum(1 for t in trades if t["outcome"] == "scratch")

print(f"\nTotal qualifying setups: {n_trades}")
print(f"Wins: {wins}  Losses: {losses}  Scratches: {scratches}")
if wins + losses:
    print(f"Win rate (excl. scratches): {wins/(wins+losses):.1%}")
if n_trades:
    print(f"Ending equity: {curve[-1]:.2f}  Return: {(curve[-1]/100-1):.1%}")

print(f"\nSANITY CHECK -- this must read 603 / 66.2% / +397.4% before the")
print(f"event log below is trusted for anything. If it doesn't match")
print(f"PROJECT_DESCRIPTION.md's verified result, this instrumented copy")
print(f"has diverged from FVG_model.py somewhere -- stop and diff the two")
print(f"files before using golden_master_events.jsonl for anything.")

# ---------- Write outputs ----------
with open("golden_master_events.jsonl", "w") as f:
    for e in events:
        f.write(json.dumps(e) + "\n")
print(f"\nWrote {len(events)} events to golden_master_events.jsonl")

with open("golden_master_trades.jsonl", "w") as f:
    for idx, t in enumerate(trades):
        row = dict(t)
        row["date"] = row["date"].isoformat()
        row["trade_index"] = idx
        f.write(json.dumps(row) + "\n")
print(f"Wrote {len(trades)} trades to golden_master_trades.jsonl")
