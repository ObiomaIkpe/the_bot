#!/usr/bin/env python3
"""
dxy_builder.py
==============
Downloads Dukascopy tick data for the six DXY pairs, computes the
ICE US Dollar Index at every tick, then resamples to 1-minute OHLC bars.

HOW IT WORKS
  1. Downloads tick-level bid/ask data from Dukascopy (free, no account needed).
  2. Computes the mid-price (average of bid and ask) for each tick.
  3. Merges all six pairs on a shared UTC timeline, forward-filling any gaps.
  4. Applies the official ICE DXY formula at every tick.
  5. Resamples to 1-minute OHLC — High and Low are real, computed from ticks.

OUTPUT FILES
  dxy_1min.csv           — final merged dataset (UTC, Open/High/Low/Close)
  dxy_1min_chunks/       — per-month CSV checkpoints (restart-safe)
  dukascopy_cache/       — raw .bi5 files so re-runs skip re-downloading

TIMING
  First run: ~8-12 hours for 10 years (mostly network download).
  Rerun:     ~5-10 minutes (reads from cache, no downloads).

DISK SPACE
  Cache:  ~3-5 GB  for 10 years of raw .bi5 files.
  Output: ~200 MB  for the final CSV.

All timestamps are UTC.
"""

import lzma
import logging
import time
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests


# ══════════════════════════════════════════════════════════════════
#  ① CONFIG  — the only section you need to edit
# ══════════════════════════════════════════════════════════════════

START_DATE = datetime(2014,  1,  1, tzinfo=timezone.utc)
END_DATE   = datetime(2024, 12, 31, tzinfo=timezone.utc)
OUTPUT_CSV = Path("dxy_1min.csv")


# ══════════════════════════════════════════════════════════════════
#  ② DXY FORMULA  (ICE, unchanged since 1973)
#
#  DXY = 50.14348112
#        × EURUSD^(-0.576)   ← negative because USD is the quote currency
#        × USDJPY^( 0.136)   ← positive because USD is the base currency
#        × GBPUSD^(-0.119)
#        × USDCAD^( 0.091)
#        × USDSEK^( 0.042)
#        × USDCHF^( 0.036)
# ══════════════════════════════════════════════════════════════════

DXY_SCALE = 50.14348112

# (exponent in the formula, decimal places Dukascopy uses in raw integer storage)
PAIRS = {
    'EURUSD': (-0.576, 5),
    'USDJPY': ( 0.136, 3),
    'GBPUSD': (-0.119, 5),
    'USDCAD': ( 0.091, 5),
    'USDSEK': ( 0.042, 5),
    'USDCHF': ( 0.036, 5),
}


# ══════════════════════════════════════════════════════════════════
#  ③  INTERNALS  — don't touch below this line
# ══════════════════════════════════════════════════════════════════

CACHE_DIR = Path("dukascopy_cache")
CHUNK_DIR = Path("dxy_1min_chunks")

_URL = (
    "https://datafeed.dukascopy.com/datafeed/"
    "{pair}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
)

# Each tick record is 20 bytes, big-endian:
#   4 bytes: millisecond offset from the start of the hour  (uint32)
#   4 bytes: ask price as a raw integer                     (uint32)
#   4 bytes: bid price as a raw integer                     (uint32)
#   4 bytes: ask volume                                     (float32, unused here)
#   4 bytes: bid volume                                     (float32, unused here)
_BI5_DTYPE = np.dtype([
    ('ms',      '>u4'),
    ('ask_raw', '>u4'),
    ('bid_raw', '>u4'),
    ('ask_vol', '>f4'),
    ('bid_vol', '>f4'),
])


# ── Download ──────────────────────────────────────────────────────

def _download(pair: str, dt: datetime) -> bytes:
    """Fetch one hour of tick data. Returns b'' on 404 or network error."""
    url = _URL.format(
        pair=pair,
        y=dt.year,
        m=dt.month - 1,   # ← Dukascopy months are 0-indexed (Jan = 00)
        d=dt.day,
        h=dt.hour,
    )
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            return b'' if r.status_code == 404 else r.content
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2 ** attempt)
    return b''


# ── Decode ────────────────────────────────────────────────────────

def _decode(raw: bytes, pair: str, hour_start: datetime) -> pd.DataFrame:
    """
    Decompress one .bi5 blob and return a DataFrame with columns:
      timestamp (UTC DatetimeIndex), mid (float)
    """
    if not raw:
        return pd.DataFrame()
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        return pd.DataFrame()

    # Truncate to a clean 20-byte boundary (safety check for partial downloads)
    if len(data) % 20 != 0:
        data = data[:len(data) - len(data) % 20]

    arr = np.frombuffer(data, dtype=_BI5_DTYPE)
    if arr.size == 0:
        return pd.DataFrame()

    _, dec = PAIRS[pair]
    divisor = 10.0 ** dec
    mid = (arr['ask_raw'].astype('f8') + arr['bid_raw'].astype('f8')) / (2.0 * divisor)

    base_ms = np.int64(int(hour_start.timestamp() * 1000))
    ts = pd.to_datetime(base_ms + arr['ms'].astype('i8'), unit='ms', utc=True)

    return pd.DataFrame({'timestamp': ts, 'mid': mid})


# ── Load one pair / one month ─────────────────────────────────────

def _load_pair_month(pair: str, year: int, month: int) -> pd.Series:
    """
    Returns all ticks for one pair in one calendar month as a Series.
    Files are cached to CACHE_DIR so they're only downloaded once.
    """
    _, n_days = monthrange(year, month)
    frames = []

    for day in range(1, n_days + 1):
        for hour in range(24):
            dt = datetime(year, month, day, hour, tzinfo=timezone.utc)
            if dt < START_DATE or dt > END_DATE:
                continue

            cfile = CACHE_DIR / f"{pair}_{year}{month:02d}{day:02d}{hour:02d}.bi5"

            if cfile.exists():
                raw = cfile.read_bytes()   # already cached — fast
            else:
                raw = _download(pair, dt)
                cfile.write_bytes(raw)     # cache it (even b'' = empty hour)
                time.sleep(0.08)           # ~12 requests/second — be polite

            df = _decode(raw, pair, dt)
            if not df.empty:
                frames.append(df)

    if not frames:
        return pd.Series(dtype='f8', name=pair)

    s = pd.concat(frames, ignore_index=True).set_index('timestamp')['mid']
    s.name = pair
    return s.sort_index()


# ── DXY formula (log form) ────────────────────────────────────────

def _compute_dxy(wide: pd.DataFrame) -> pd.Series:
    """
    Compute DXY at every row using the log form of the ICE formula.

    Why log form?
      DXY is a product of six numbers each raised to a power.
      Computing it in log space (ln then exp) avoids floating-point
      overflow/underflow and is faster on large arrays.

      ln(DXY) = ln(50.14348112) + Σ exponent_i × ln(price_i)
      DXY     = exp(ln(DXY))
    """
    log_dxy = np.log(DXY_SCALE)
    for pair, (exp, _) in PAIRS.items():
        log_dxy = log_dxy + exp * np.log(wide[pair])
    result = np.exp(log_dxy)
    result.name = 'dxy'
    return result


# ── Process one month ─────────────────────────────────────────────

def process_month(year: int, month: int) -> pd.DataFrame:
    """
    Full pipeline for one calendar month:
      1. Download/load ticks for all six pairs.
      2. Outer-join → every unique tick timestamp gets a row.
      3. Forward-fill → use last known price for any pair that hasn't ticked yet.
      4. Compute DXY at every tick.
      5. Resample to 1-minute OHLC.

    Returns a DataFrame with columns Open, High, Low, Close (UTC DatetimeIndex).
    """
    # ── 1. load all pairs ──────────────────────────────────────────
    pair_series = []
    for pair in PAIRS:
        s = _load_pair_month(pair, year, month)
        logging.info(f"    {pair}: {len(s):>9,} ticks")
        pair_series.append(s)

    # ── 2. merge via outer join ────────────────────────────────────
    # After concat with axis=1, we get a DataFrame where:
    #   - Every unique timestamp from any pair is a row.
    #   - NaN means that pair didn't tick at that exact moment.
    wide = pd.concat(pair_series, axis=1)
    wide.sort_index(inplace=True)

    # ── 3. forward-fill ───────────────────────────────────────────
    # SEK and CHF are thin — can go minutes without a tick.
    # ffill carries the last known price forward until the next tick.
    wide.ffill(inplace=True)

    # Drop rows at the very start before all six pairs have had at least one tick.
    wide.dropna(inplace=True)

    if wide.empty:
        return pd.DataFrame()

    # ── 4. DXY at every tick ──────────────────────────────────────
    dxy = _compute_dxy(wide)
    logging.info(
        f"    Merged: {len(dxy):,} ticks  |  "
        f"DXY range: {dxy.min():.3f} – {dxy.max():.3f}"
    )

    # ── 5. resample to 1-minute OHLC ─────────────────────────────
    # Because we computed DXY at every individual tick, the High and Low
    # here are real values the index actually reached — not approximations.
    ohlc = dxy.resample('1min').ohlc()
    ohlc.dropna(inplace=True)
    ohlc.columns = ['Open', 'High', 'Low', 'Close']
    return ohlc


# ── Helpers ───────────────────────────────────────────────────────

def _iter_months(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


# ══════════════════════════════════════════════════════════════════
#  ④  MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(message)s',
        datefmt='%H:%M:%S',
    )

    CACHE_DIR.mkdir(exist_ok=True)
    CHUNK_DIR.mkdir(exist_ok=True)

    months = list(_iter_months(START_DATE, END_DATE))
    logging.info(
        f"DXY builder  {START_DATE.date()} → {END_DATE.date()}  "
        f"({len(months)} months to process)"
    )
    logging.info("Each month is checkpointed to dxy_1min_chunks/")
    logging.info("If interrupted, restart and already-done months will be skipped.")

    for i, (year, month) in enumerate(months, 1):
        chunk = CHUNK_DIR / f"{year}{month:02d}.csv"

        if chunk.exists():
            logging.info(f"[{i}/{len(months)}]  {year}-{month:02d}  (already done, skipping)")
            continue

        logging.info(f"[{i}/{len(months)}]  {year}-{month:02d}")
        try:
            ohlc = process_month(year, month)
        except Exception as exc:
            logging.error(f"  ERROR in {year}-{month:02d}: {exc}  — skipping")
            continue

        if ohlc.empty:
            logging.warning(f"  No data for {year}-{month:02d} — skipping")
            continue

        ohlc.to_csv(chunk)
        logging.info(f"  ✓  {len(ohlc):,} candles written to {chunk.name}")

    # ── Merge all monthly chunks into one final CSV ────────────────
    logging.info("All months done. Merging into final CSV...")
    chunk_files = sorted(CHUNK_DIR.glob("*.csv"))

    if not chunk_files:
        logging.error("No chunk files found — something went wrong above.")
    else:
        frames = [
            pd.read_csv(f, index_col=0, parse_dates=True)
            for f in chunk_files
        ]
        merged = pd.concat(frames).sort_index()
        merged.index.name = 'timestamp'
        merged.to_csv(OUTPUT_CSV)
        logging.info(f"Done — {len(merged):,} total candles → {OUTPUT_CSV}")