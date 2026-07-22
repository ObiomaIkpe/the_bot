# Phase 1 tooling: golden-master extraction

`golden_master_events.jsonl` (12MB) is **not committed** -- it's fully
reproducible from the locked model + raw HistData CSVs, and 12MB of
generated output isn't worth the repo weight when regenerating it takes
one command. `golden_master_trades.jsonl` (120KB) is committed, since
it's small and is the thing future diff tooling will most often be
checked against quickly without needing the full event trail.

## Regenerating golden_master_events.jsonl

1. Download EUR/USD 1-minute data from HistData.com, 2016 through the
   present. You'll get a mix of two file formats depending on year:
   - **2016-2023**: "MT" format (`DAT_MT_EURUSD_M1_YYYY.csv`)
   - **2024-onward**: "ASCII" format (`DAT_ASCII_EURUSD_M1_YYYY.csv`)

2. Convert the MT-format files to match the ASCII format (this is
   required -- `extract_golden_master.py`'s loader only recognizes the
   ASCII filename pattern and will silently skip MT-format files with no
   error, processing far less data than intended):

   ```
   cd /path/to/all/your/csv/files
   python phase1/convert_mt_to_ascii.py
   ```

   This writes converted files into `./converted/`. It prints a row-count
   check (rows in == rows out) for every file -- confirm every file shows
   `[OK]` before proceeding.

3. Copy the converted files alongside the original (already-ASCII) files
   into one directory, then run the extraction from inside it:

   ```
   cp converted/DAT_ASCII_EURUSD_M1_*.csv /path/to/combined_data/
   cp DAT_ASCII_EURUSD_M1_2024.csv DAT_ASCII_EURUSD_M1_2025.csv ... /path/to/combined_data/
   cd /path/to/combined_data
   python /path/to/repo/phase1/extract_golden_master.py
   ```

4. **Before trusting the output for anything**, check the printed sanity
   check reads:
   ```
   Total qualifying setups: 603
   Win rate (excl. scratches): 66.2%
   Ending equity: 497.42  Return: 397.4%
   ```
   If it doesn't match, something has diverged from the locked
   `FVG_model.py` -- stop and diff before using the event log for
   anything. See `PHASE1_VALIDATION.md` for what was checked when this
   was last run successfully.

5. Copy the resulting `golden_master_events.jsonl` into
   `phase1/golden_master/` locally. It won't show up in `git status`
   (gitignored) -- that's expected.
