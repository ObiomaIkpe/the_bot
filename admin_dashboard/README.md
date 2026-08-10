# Trading bot admin dashboard

Standalone, read-only Streamlit app for watching the bot's live
activity: every journaled event, every trade, and a drill-down from
any trade back to the exact chain of events (raid → MSS → FVG →
candidate → fill → close) that produced it.

## Why Streamlit

It's plain Python — no separate frontend framework or build step to
learn — and gets you real, sortable/filterable tables and
auto-refresh for very little code. This is a personal monitoring tool,
not a product; Streamlit is built for exactly that.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set two environment variables:
   ```
   export DATABASE_URL=postgresql://user:pass@host:5432/trading_bot
   export MAIN_REPO_PATH=/path/to/the_bot-main
   ```
   `MAIN_REPO_PATH` must point at the main bot repo's root (the folder
   containing `app/`) — this dashboard imports the real `Event`,
   `Trade`, and `ModelConfig` SQLAlchemy models from there instead of
   redefining the schema a second time, so it can never silently drift
   out of sync with a real migration.
3. Run it:
   ```
   streamlit run app.py
   ```
   Opens on `http://localhost:8501` by default.

## What it shows

- **Live Event Feed** — every event in a chosen time window, filterable
  by model, colored so real broker actions (order placed/filled,
  safety-check failures) stand out from pure detection/simulation
  events. Auto-refreshes every 30s (toggle in the sidebar).
- **Trades** — filterable trade list (model, shadow vs. real, outcome,
  date range). Selecting one shows the simulated outcome and the real
  broker outcome side by side, plus the full chain of events from that
  trading day with the two events specific to this trade
  (`order_filled` / `trade_closed`) highlighted.
- **Safety Checks** — every `safety_check_failed` event, plus a count
  by `check_name` so a repeated, ongoing failure is obvious at a
  glance rather than buried in a scroll of one-off entries.
- **Models** — the current `status`/`risk_pct`/`magic_number` for
  every model_config row, so you can see at a glance what's actually
  live vs. shadow vs. off.

## Why there's no trade→event foreign key used

There isn't one in the schema — `events` and `trades` are linked the
same way the bot itself links them (see `shadow_runner/runner.py`'s
`_write_trade()`): same user/model/day, then matched by direction and
entry/exit price. `queries.get_event_chain_for_trade()` does exactly
that, on purpose, so this dashboard's idea of "which events belong to
this trade" never diverges from what the live system itself considers
a match.

## Safety

Every DB connection this tool opens sets
`default_transaction_read_only = on` at the Postgres session level the
moment it connects (see `db.py`) — not just "the code never calls
`.commit()`," but the database itself refuses any write attempted
through this tool, even a future bug in the dashboard code.

## No auth (yet)

Per current scope, this runs with no login — intended for local/
internal use only. Don't expose it on a public port as-is. If you
later want to lock it down, Streamlit supports a simple
`streamlit-authenticator` password gate without much code — worth
adding before this ever runs anywhere but your own machine.

## Extending it

- **New event types**: nothing to change — the feed just displays
  whatever's in `Event.event_type`/`.details`; `is_real_action_event()`
  reads from the real source of truth (`app.models.event.REAL_ACTION_EVENT_TYPES`)
  so a newly-added real-action type gets colored correctly with zero
  changes here.
- **A second model going live** (OB or FVG+OB): the Models tab and
  every filter already handle any of `'fvg' / 'ob' / 'fvg_ob'` — no
  changes needed there either. The event feed and trade drill-down are
  also model-agnostic already.
- **Charts / equity curve**: `queries.py` is the one place to add a
  new query function; keep the same read-only discipline (query only,
  never `.add()`/`.commit()`).
