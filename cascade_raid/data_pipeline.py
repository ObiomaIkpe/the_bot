"""
data_pipeline.py -- HistData ASCII M1 ingestion and multi-timeframe
candle construction, for Cascade Raid's own real-data validation run.

Written fresh, independent of both the reference package's own
data_pipeline.py and phase1/'s HistData-handling code, per the "no
shared code between models" rule -- see cascade_raid/streaming/README.md.
The underlying conventions this encodes (HistData's fixed-EST offset,
the 17:00 ET forex trading-day boundary, a Sunday-start trading week)
are documented facts about the data/market, not proprietary logic
copied from either of those, so independently reimplementing them here
is expected to look conceptually similar without being the same code.
"""
import glob
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

FIXED_EST = timezone(timedelta(hours=-5))  # HistData's own fixed offset, no DST
NY_TZ = ZoneInfo("America/New_York")


def load_histdata_m1(csv_glob_pattern: str) -> pd.DataFrame:
    """Load and concatenate one or more HistData ASCII M1 CSVs
    (semicolon-delimited: YYYYMMDD HHMMSS;open;high;low;close;volume),
    correct from HistData's fixed-EST timestamps to true DST-aware
    America/New_York time, return OHLC indexed by tz-aware NY time."""
    files = sorted(glob.glob(csv_glob_pattern))
    if not files:
        raise FileNotFoundError(f"No files matched pattern: {csv_glob_pattern}")

    frames = [
        pd.read_csv(f, sep=";", header=None, names=["dt", "open", "high", "low", "close", "vol"])
        for f in files
    ]
    full = pd.concat(frames, ignore_index=True)
    full["dt"] = pd.to_datetime(full["dt"], format="%Y%m%d %H%M%S")
    full["dt_utc"] = full["dt"].dt.tz_localize(FIXED_EST).dt.tz_convert("UTC")
    full["dt_ny"] = full["dt_utc"].dt.tz_convert(NY_TZ)

    full = full.set_index("dt_ny").sort_index()
    full = full[["open", "high", "low", "close"]]
    return full[~full.index.duplicated(keep="first")]


def build_m3(m1: pd.DataFrame) -> pd.DataFrame:
    return m1.resample("3min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def build_daily(m1: pd.DataFrame) -> pd.DataFrame:
    """Forex trading-day convention: a day runs 17:00 ET -> next 17:00
    ET, labeled by its start date. Drops degenerate DST-transition stub
    days (a handful of near-empty days, not the normal Friday close)."""
    trading_date = (m1.index - pd.Timedelta(hours=17)).normalize()
    m1_ = m1.copy()
    m1_["trading_date"] = trading_date

    daily = m1_.groupby("trading_date").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")
    )
    daily.index.name = "trading_date"

    bar_counts = m1_.groupby("trading_date").size()
    is_friday = daily.index.dayofweek == 4
    stub = (bar_counts < 200) & (~is_friday)
    return daily[~stub]


def build_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Sunday-start trading week. Week-start arithmetic done in
    tz-naive space -- a tz-aware, DST-observing DatetimeIndex plus a
    fixed multi-day pd.Timedelta silently produces a wrong wall-clock
    date across a DST transition, since Timedelta is a fixed physical
    duration, not a calendar offset."""
    naive_dates = daily.index.tz_localize(None)
    dow = naive_dates.dayofweek  # Monday=0 ... Sunday=6
    week_start = naive_dates - pd.to_timedelta((dow + 1) % 7, unit="D")
    d = daily.copy()
    d["week_start"] = week_start
    weekly = d.groupby("week_start").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
    )
    weekly.index = weekly.index.tz_localize(NY_TZ)
    return weekly


def build_monthly(m1: pd.DataFrame) -> pd.DataFrame:
    return m1.resample("ME").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def next_friday_close(ts: pd.Timestamp) -> pd.Timestamp:
    """Friday 17:00 NY close of ts's own trading week (next week's if ts
    is already past this week's Friday 17:00). Tz-naive arithmetic, per
    the same DST-safety note as build_weekly()."""
    naive = ts.tz_localize(None) if ts.tzinfo is not None else ts
    days_ahead = (4 - naive.weekday()) % 7  # Monday=0 ... Friday=4
    candidate = (naive.normalize() + pd.Timedelta(days=days_ahead)).replace(hour=17, minute=0, second=0)
    if candidate <= naive:
        candidate += pd.Timedelta(days=7)
    return candidate.tz_localize(NY_TZ)
