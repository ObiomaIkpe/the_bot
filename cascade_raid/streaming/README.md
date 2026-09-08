# Cascade Raid -- streaming state machine components

Deliberately independent of `phase1/streaming/` (the `fvg` model's own
streaming components) and of `phase1/`'s data-loading scripts, per an
explicit instruction (2026-09-06 session): every model in this project
stands alone, with zero shared code between models, even where the
underlying logic looks similar (both `fvg` and Cascade Raid detect
liquidity sweeps -> market structure shifts -> fair value gaps). No
file in this package imports from `phase1/` or `shadow_runner/`, and
nothing in either of those imports from here.

Sharing *within* Cascade Raid's own two account-type variants (the
4-slot "normal" and 3-slot "prop-firm" versions) is fine -- confirmed
explicitly by the user -- since those aren't different models, just
different risk parameters on the same edge. Both variants are built on
this one shared streaming engine, parameterized by `MAX_CONCURRENT_POSITIONS`/
`BREAKER_THRESHOLD`/`BREAKER_SKIP`.

## Reference material this reimplements

A complete, already-tested, non-streaming (whole-array) reference
implementation was supplied directly by the user (not written by this
project), along with two real trade-by-trade logs (1,056 and 976 rows)
as ground truth to validate against:
  - `CASCADE_RAID_3min_model_v1_LOCKED.py` (4-slot, no prop-firm rules)
  - `CASCADE_RAID_3min_3slot_propfirm_v2.py` (3-slot, prop-firm variant)
  - Shared reference modules: `data_pipeline.py`, `scalp_common.py`,
    `htf_bias_cascade.py` (the reference package's own internal sharing,
    not this project's -- see above)

This package's job is to reproduce that reference's exact behavior, bar
by bar, as a live process would receive data (no full-array lookahead),
proven via `validate_bit_for_bit.py` replaying real HistData through
both the original reference script and this streaming reimplementation
and diffing the resulting trade lists.

## Pieces, in dependency order

- **`fractal_swings.py`** -- the 5-bar (wing=2) fractal swing high/low
  detector used to build the "liquidity pool" a sweep can trigger
  against. Mirrors `scalp_common.find_fractal_swings()`.
- **`signal_detector.py`** -- sweep -> rapid MSS -> FVG -> equilibrium-
  filter signal detection built on top of the fractal detector. Mirrors
  `scalp_common.detect_signals()`.
- **`htf_bias.py`** -- separate, differently-parameterized (wing=1,
  close-only) fractal bias detector for the Monthly/Weekly/Daily trend-
  agreement filter. Mirrors `htf_bias_cascade.py`.
- **`position_manager.py`** -- fill detection, fixed 30/60-pip stop/
  target, week-end (Friday 17:00 NY) hold cutoff, the concurrency cap,
  and the consecutive-loss circuit breaker. Mirrors the inline
  simulation loop in both reference model scripts.
