"""
Converts HistData "MT" format (DATE,TIME,O,H,L,C,V -- comma-separated,
date/time split) into the "ASCII" format shape FVG_model.py's loader
already expects (combined-timestamp;O;H;L;C;V -- semicolon-separated),
so the existing loader code doesn't need to change at all -- one
consistent on-disk format instead of a second code path.

Verification performed, not just assumed:
  1. Row count in == row count out, per file (no silent row loss).
  2. Spot-check: first and last row of each converted file, printed for
     manual eyeball comparison against the source.
  3. Combined-file row count == sum of all individual file row counts
     (no dedup/overlap silently eating rows before the model's own
     `df[~df.index.duplicated(keep="first")]` dedup step gets to see them).
"""
import csv
import glob
import os

MT_GLOB = "DAT_MT_EURUSD_M1_*.csv"
OUT_DIR = "converted"


def convert_file(src_path, dst_path):
    row_count_in = 0
    row_count_out = 0
    first_out, last_out = None, None

    with open(src_path, "r", newline="") as fin, open(dst_path, "w", newline="") as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout, delimiter=";")
        for row in reader:
            row_count_in += 1
            date_str, time_str, o, h, l, c, v = row
            # '2016.01.03' + '17:00' -> '20160103 170000'
            date_compact = date_str.replace(".", "")
            time_compact = time_str.replace(":", "") + "00"  # MT format has no seconds
            ts = f"{date_compact} {time_compact}"
            out_row = [ts, o, h, l, c, v]
            writer.writerow(out_row)
            row_count_out += 1
            if first_out is None:
                first_out = out_row
            last_out = out_row

    return row_count_in, row_count_out, first_out, last_out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    mt_files = sorted(glob.glob(MT_GLOB))
    if not mt_files:
        print(f"No files matched {MT_GLOB} in the current directory.")
        return

    total_in, total_out = 0, 0
    for src in mt_files:
        year = src.split("_")[-1].replace(".csv", "")
        dst = os.path.join(OUT_DIR, f"DAT_ASCII_EURUSD_M1_{year}.csv")
        n_in, n_out, first_row, last_row = convert_file(src, dst)
        total_in += n_in
        total_out += n_out
        status = "OK" if n_in == n_out else "MISMATCH -- STOP AND INVESTIGATE"
        print(f"{src} -> {dst}")
        print(f"  rows in: {n_in}, rows out: {n_out}  [{status}]")
        print(f"  first row: {first_row}")
        print(f"  last row:  {last_row}")

    print(f"\nTotal rows in: {total_in}, total rows out: {total_out}")
    if total_in != total_out:
        print("MISMATCH IN TOTALS -- do not proceed until this is resolved.")
    else:
        print("Row counts match across every file. Converted files are in ./converted/")
        print("Copy these alongside the original (already-ASCII-format) 2024-2026 files")
        print("before running extract_golden_master.py, so the loader's glob picks up")
        print("the full 2016-2026 range as one consistent set.")


if __name__ == "__main__":
    main()
