# Phase 1 validation: golden-master extraction

First deliverable of Phase 1 (streaming state machine). Before writing
any streaming code, the locked FVG model was instrumented to emit a
complete event-by-event trail -- not just the final 603 trades -- so the
streaming state machine can be diffed against it stage-by-stage, per the
project's standing rule that every change gets tested in isolation
before being trusted.

## What was done

- `phase1/convert_mt_to_ascii.py` and `phase1/extract_golden_master.py`
  written. See `phase1/README.md` for what each does and how to
  regenerate the output.
- Constraint on `extract_golden_master.py`: it must not change
  `FVG_model.py`'s trading behavior in any way -- only add logging.
  Verified this by structurally diffing the instrumented file against
  the original after stripping all `log_event(...)` calls; the only
  remaining differences were the docstring header, a few dropped inline
  comments, two semicolon-joined statements split onto separate lines,
  and one variable rename confined to the post-loop reporting section
  (`n` -> `n_trades`, after trading logic has already finished). No
  control flow, condition, or calculation was touched.

## Bug found before running anything

**Data format mismatch would have silently used ~1.5 years of data
instead of 10.5.** The uploaded 2016-2023 files were HistData's "MT"
format (comma-separated, split date/time columns); 2024-2026 were
"ASCII" format (semicolon-separated, combined timestamp).
`FVG_model.py`'s loader globs only the ASCII filename pattern
(`DAT_ASCII_EURUSD_M1_*.csv`) -- running it as-is against a directory
containing both formats would have matched only the 2024-2026 files,
with no error, no warning, and a "successful"-looking but badly wrong
run.

Fixed by writing `convert_mt_to_ascii.py` rather than modifying the
loader itself -- converts MT-format files into the same on-disk shape
the ASCII files are already in, so the existing (already-verified)
loader code in `FVG_model.py` doesn't need to change at all.

Verified before trusting the conversion:
- Row count in == row count out, per file, no silent row loss.
- First/last row of each converted file spot-checked.
- Day-boundary continuity checked across the format transition (both
  conventions roll over at 17:00 NY time, consistent with the same
  underlying HistData timestamp convention).

One data-quality observation, not a bug: 2023 has meaningfully fewer
rows per day (~1037 avg) than 2022 (~1195 avg) despite almost the same
number of trading days. Rows-in matched rows-out exactly for every file,
so this isn't a conversion artifact -- likely genuine gaps in quiet/
illiquid source minutes. Did not end up affecting the result (see
below).

## Result

Ran against the full, real 2016-2026 dataset (not a subset, not
synthetic data):

| Metric | Extracted | Locked (PROJECT_DESCRIPTION.md) | Match |
|---|---|---|---|
| 1-min bars | 3,820,900 | ~3.82 million | Yes |
| 5-min bars | 767,151 | ~767,000 | Yes |
| Trades | 603 | 603 | Yes |
| Win rate | 66.2% | 66.2% | Yes |
| Return | +397.4% | +397.4% | Yes |
| Wins/Losses/Scratches | 389/199/15 | 389/199/15 | Yes |

Year-by-year trade counts (the full table, not just the headline
numbers) matched `PROJECT_DESCRIPTION.md` exactly for every single year,
2016 through 2026 -- including 2023, despite the row-density observation
above.

## Output

- `golden_master_trades.jsonl` (committed, 120KB) -- the 603 trades in
  an easier-to-diff format than the original pickle.
- `golden_master_events.jsonl` (not committed, ~12MB -- see
  `phase1/README.md` for regeneration) -- 93,791 events across 9 types:

  | Event type | Count |
  |---|---|
  | intraday_swing_high_confirmed | 31,929 |
  | intraday_swing_low_confirmed | 31,735 |
  | raid_detected | 14,727 |
  | mss_confirmed | 6,382 |
  | fvg_found | 2,138 |
  | day_trend_determined | 1,951 |
  | day_skipped_no_trend | 1,215 |
  | fvg_rejected_min_stop | 1,201 |
  | order_filled | 603 |
  | trade_closed | 603 |
  | daily_swing_high_confirmed | 452 |
  | daily_swing_low_confirmed | 441 |
  | day_skipped_insufficient_bars | 331 |
  | day_skipped_fomc | 83 |

  Note: 84 FOMC dates are defined in the script's date set, but only 83
  `day_skipped_fomc` events fire -- expected, since the day-skip check
  only runs for days that actually appear in the resampled data (one
  FOMC date apparently doesn't land on a day present in `all_days`, not
  investigated further since it doesn't affect the trade-count match
  above).

## Still outstanding for Phase 1

- The streaming state machine itself -- not yet started.
- Automated trade-by-trade + event-by-event diff tooling between the
  golden master and the streaming version's output.
- No-lookahead-by-construction bar feed (generator interface).
- Per-stage unit tests on boundary cases (swing-pivot arm length, the
  50-minute MSS window edge, min-stop threshold).
- Randomized-slice differential testing.
