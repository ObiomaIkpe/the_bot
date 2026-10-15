#!/usr/bin/env python3
"""
fx_builder.py
=============
Downloads Dukascopy tick data for GBPUSD, EURUSD, and XAUUSD (spot gold)
and resamples to 1-minute OHLC bars.

IMPORTANT — run this from the SAME FOLDER as dxy_builder.py.
  If you already ran that script, EURUSD and GBPUSD are fully cached.
  Only XAUUSD will need downloading (~4-6 hours for 10 years).

OUTPUT FILES (one CSV per pair, written to the same folder as this script):
  eurusd_1min.csv
  gbpusd_1min.csv
  xauusd_1min.csv

Each has columns: timestamp (UTC), Open, High, Low, Close
High and Low are true values computed from individual ticks.

DISK SPACE:
  New cache files for XAUUSD: ~1-2 GB
  Output CSVs: ~60 MB each
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
#  ① CONFIG  — edit these before running
# ══════════════════════════════════════════════════════════════════

START_DATE = datetime(2014,  1,  1, tzinfo=timezone.utc)
END_DATE   = datetime(2024, 12, 31, tzinfo=timezone.utc)
OUTPUT_DIR = Path(".")   # where the final CSVs are written

# Pairs to build, and the number of decimal places Dukascopy uses
# when storing prices as raw integers.
#
# Examples:
#   EURUSD price 1.08500 → stored as integer 108500  (dec=5, divide by 1e5)
#   GBPUSD price 1.28000 → stored as integer 128000  (dec=5, divide by 1e5)
#   XAUUSD price 1850.25 → stored as integer 185025  (dec=2, divide by 1e2)
#
# ⚠ XAUUSD NOTE: the dec=2 value for gold is based on Dukascopy's standard
# format. The script will print a few sample prices when it starts on XAUUSD
# so you can confirm they look right (should be ~$250–$2500 range for 2014-2024).
# If the numbers are off by a factor of 10 (e.g. 18502.5 or 185.025), change
# dec to 3 or 1 respectively and re-run — chunks are checkpointed so it's fast.

INSTRUMENTS = {
    'EURUSD': 5,
    'GBPUSD': 5,
    'XAUUSD': 2,
}


# ══════════════════════════════════════════════════════════════════
#  INTERNALS — don't change anything below
# ══════════════════════════════════════════════════════════════════

# Shared with dxy_builder.py — EURUSD and GBPUSD files are already here.
CACHE_DIR = Path("dukascopy_cache")

_URL = (
    "https://datafeed.dukascopy.com/datafeed/"
    "{pair}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
)

# 20-byte binary record layout (big-endian):
#   ms       uint32  — millisecond offset from start of hour
#   ask_raw  uint32  — ask price as raw integer
#   bid_raw  uint32  — bid price as raw integer
#   ask_vol  float32 — volume (not used here)
#   bid_vol  float32 — volume (not used here)
_BI5_DTYPE = np.dtype([
    ('ms',      '>u4'),
    ('ask_raw', '>u4'),
    ('bid_raw', '>u4'),
    ('ask_vol', '>f4'),
    ('bid_vol', '>f4'),
])


# ── Download ──────────────────────────────────────────────────────

def _download(pair: str, dt: datetime) -> bytes:
    """Fetch one hour of tick data from Dukascopy. Returns b'' on failure."""
    url = _URL.format(
        pair=pair,
        y=dt.year,
        m=dt.month - 1,   # ← 0-indexed months on Dukascopy (Jan=00, Dec=11)
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

def _decode(raw: bytes, dec: int, hour_start: datetime) -> pd.DataFrame:
    """Decompress one .bi5 blob → DataFrame with columns (timestamp, mid)."""
    if not raw:
        return pd.DataFrame()
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError:
        return pd.DataFrame()

    # Safety: truncate to a clean 20-byte boundary
    if len(data) % 20 != 0:
        data = data[:len(data) - len(data) % 20]

    arr = np.frombuffer(data, dtype=_BI5_DTYPE)
    if arr.size == 0:
        return pd.DataFrame()

    divisor = 10.0 ** dec
    mid = (arr['ask_raw'].astype('f8') + arr['bid_raw'].astype('f8')) / (2.0 * divisor)

    base_ms = np.int64(int(hour_start.timestamp() * 1000))
    ts = pd.to_datetime(base_ms + arr['ms'].astype('i8'), unit='ms', utc=True)

    return pd.DataFrame({'timestamp': ts, 'mid': mid})


# ── Load one pair / one month ─────────────────────────────────────

def _load_pair_month(pair: str, dec: int, year: int, month: int) -> pd.Series:
    """
    Returns all ticks for one pair in one calendar month as a Series
    (UTC DatetimeIndex, float values = mid price).
    Reads from cache when available; downloads and caches otherwise.
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
                raw = cfile.read_bytes()
            else:
                raw = _download(pair, dt)
                cfile.write_bytes(raw)   # cache b'' too (marks empty hours)
                time.sleep(0.08)         # ~12 requests/sec — polite rate-limit

            df = _decode(raw, dec, dt)
            if not df.empty:
                frames.append(df)

    if not frames:
        return pd.Series(dtype='f8', name=pair)

    s = pd.concat(frames, ignore_index=True).set_index('timestamp')['mid']
    s.name = pair
    return s.sort_index()


# ── Sanity check for XAUUSD ──────────────────────────────────────

def _verify_xauusd(dec: int) -> bool:
    """
    Fetch a small sample of XAUUSD ticks and print them so you can confirm
    the decimal places are correct before starting the full download.
    Returns True if the prices look reasonable, False if they look off.
    """
    logging.info("  Fetching a small XAUUSD sample to verify price format...")
    # Use a date when gold was clearly in the $1200-$1400 range
    sample_dt = datetime(2018, 1, 2, 9, tzinfo=timezone.utc)
    raw = _download('XAUUSD', sample_dt)
    df  = _decode(raw, dec, sample_dt)

    if df.empty:
        logging.warning("  Could not fetch sample — continuing anyway.")
        return True

    sample_prices = df['mid'].head(5).tolist()
    logging.info(f"  Sample XAUUSD prices (should be ~$1300 for Jan 2018):")
    for p in sample_prices:
        logging.info(f"    {p:.4f}")

    # Gold in Jan 2018 was ~$1300. Check we're in the right ballpark.
    avg = df['mid'].mean()
    if 800 < avg < 3500:
        logging.info("  ✓ Prices look correct.")
        return True
    else:
        logging.error(
            f"  ✗ Average price {avg:.2f} is outside the expected $800–$3500 range.\n"
            f"    This probably means 'dec' is wrong. Try changing INSTRUMENTS['XAUUSD']\n"
            f"    from {dec} to {dec+1} or {dec-1} and re-run."
        )
        return False


# ── Process one pair / one month ──────────────────────────────────

def process_pair_month(pair: str, dec: int, year: int, month: int) -> pd.DataFrame:
    """
    For one pair and one calendar month:
    1. Load tick data (from cache or download).
    2. Resample to 1-minute OHLC.
    Returns DataFrame with columns Open, High, Low, Close (UTC DatetimeIndex).
    """
    s = _load_pair_month(pair, dec, year, month)
    if s.empty:
        return pd.DataFrame()

    logging.info(
        f"    {pair}: {len(s):>9,} ticks  |  "
        f"range: {s.min():.{dec}f} – {s.max():.{dec}f}"
    )

    ohlc = s.resample('1min').ohlc()
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
#  MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(message)s',
        datefmt='%H:%M:%S',
    )

    CACHE_DIR.mkdir(exist_ok=True)
    months = list(_iter_months(START_DATE, END_DATE))

    logging.info(f"fx_builder: {START_DATE.date()} → {END_DATE.date()} ({len(months)} months)")
    logging.info(f"Pairs: {', '.join(INSTRUMENTS)}")
    logging.info(
        "NOTE: EURUSD and GBPUSD files are shared with dxy_builder.py cache — "
        "those should be fast (reading from disk, no downloads)."
    )

    for pair, dec in INSTRUMENTS.items():
        logging.info(f"\n{'═' * 60}")
        logging.info(f"  Processing {pair}")
        logging.info(f"{'═' * 60}")

        # For XAUUSD, do a quick sanity check before starting the full download
        if pair == 'XAUUSD':
            ok = _verify_xauusd(dec)
            if not ok:
                logging.error(f"  Skipping {pair} — fix the decimal places first.")
                continue

        chunk_dir = Path(f"fx_chunks_{pair}")
        chunk_dir.mkdir(exist_ok=True)

        for i, (year, month) in enumerate(months, 1):
            chunk = chunk_dir / f"{year}{month:02d}.csv"

            if chunk.exists():
                logging.info(f"  [{i}/{len(months)}]  {year}-{month:02d}  (already done)")
                continue

            logging.info(f"  [{i}/{len(months)}]  {year}-{month:02d}")
            try:
                ohlc = process_pair_month(pair, dec, year, month)
            except Exception as exc:
                logging.error(f"    ERROR: {exc} — skipping")
                continue

            if ohlc.empty:
                logging.warning("    No data — skipping")
                continue

            ohlc.to_csv(chunk)
            logging.info(f"    ✓  {len(ohlc):,} candles")

        # Merge all monthly chunks into the final CSV for this pair
        chunk_files = sorted(chunk_dir.glob("*.csv"))
        if not chunk_files:
            logging.warning(f"  No chunks for {pair} — skipping merge")
            continue

        logging.info(f"  Merging {len(chunk_files)} chunks for {pair}...")
        frames = [
            pd.read_csv(f, index_col=0, parse_dates=True)
            for f in chunk_files
        ]
        merged = pd.concat(frames).sort_index()
        merged.index.name = 'timestamp'
        out = OUTPUT_DIR / f"{pair.lower()}_1min.csv"
        merged.to_csv(out)
        logging.info(f"  ✓  {len(merged):,} total candles  →  {out}")

    logging.info("\nAll done.")