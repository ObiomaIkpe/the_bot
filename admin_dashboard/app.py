"""
Admin dashboard for the SMC/ICT trading bot. Read-only, standalone --
run with: streamlit run app.py

Requires two env vars (see README.md):
  DATABASE_URL   -- same Postgres connection string the main app uses
  MAIN_REPO_PATH -- path to the main bot repo's root (for its models)
"""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from db import get_session
from queries import (
    get_event_chain_for_trade,
    get_model_configs,
    get_recent_events,
    get_safety_failures,
    get_trades,
    is_real_action_event,
)

st.set_page_config(page_title="Trading Bot Admin", layout="wide")

MODELS = ["fvg", "ob", "fvg_ob"]

# ---------- auto-refresh ----------
# Simple, dependency-free auto-refresh: a small JS meta-refresh tag.
# Toggle it off if you're mid-investigation and don't want the page
# jumping around under you.
with st.sidebar:
    st.title("Trading Bot Admin")
    auto_refresh = st.checkbox("Auto-refresh every 30s", value=True)
    if auto_refresh:
        st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)
    st.caption(f"Last loaded: {datetime.now().strftime('%H:%M:%S')}")
    st.divider()
    model_filter = st.selectbox("Model", ["all"] + MODELS)
    model_filter = None if model_filter == "all" else model_filter

session = get_session()

tab_feed, tab_trades, tab_safety, tab_models = st.tabs(
    ["Live Event Feed", "Trades", "Safety Checks", "Models"]
)

# ---------- Live Event Feed ----------
with tab_feed:
    st.subheader("Recent events")
    col1, col2 = st.columns([1, 3])
    with col1:
        hours_back = st.slider("Hours back", 1, 72, 12)
    events = get_recent_events(
        session, model=model_filter, since=datetime.utcnow() - timedelta(hours=hours_back), limit=1000
    )
    if not events:
        st.info("No events in this window.")
    else:
        rows = [
            {
                "timestamp": e.timestamp,
                "model": e.model,
                "event_type": e.event_type,
                "real_action": is_real_action_event(e.event_type),
                "is_shadow": e.is_shadow,
                "details": e.details,
            }
            for e in events
        ]
        df = pd.DataFrame(rows)

        def _highlight(row):
            if row["event_type"] == "safety_check_failed":
                return ["background-color: #4a1010"] * len(row)
            if row["real_action"]:
                return ["background-color: #143314"] * len(row)
            return [""] * len(row)

        st.caption(
            "🟩 real broker action &nbsp;&nbsp; 🟥 safety_check_failed &nbsp;&nbsp; "
            f"({len(events)} events shown)",
            unsafe_allow_html=True,
        )
        st.dataframe(df.style.apply(_highlight, axis=1), use_container_width=True, height=600)

# ---------- Trades ----------
with tab_trades:
    st.subheader("Trades")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        shadow_filter = st.selectbox("Shadow / real", ["all", "shadow only", "real only"])
    with c2:
        outcome_filter = st.selectbox("Outcome", ["all", "win", "loss", "scratch", "open"])
    with c3:
        days_back = st.number_input("Days back", min_value=1, max_value=365, value=30)
    with c4:
        st.write("")

    is_shadow_arg = None
    if shadow_filter == "shadow only":
        is_shadow_arg = True
    elif shadow_filter == "real only":
        is_shadow_arg = False
    outcome_arg = None if outcome_filter in ("all", "open") else outcome_filter

    trades = get_trades(session, model=model_filter, is_shadow=is_shadow_arg, outcome=outcome_arg, days_back=days_back)
    if outcome_filter == "open":
        trades = [t for t in trades if t.outcome is None]

    if not trades:
        st.info("No trades match these filters.")
    else:
        trade_rows = [
            {
                "entry_time_ny": t.entry_time_ny,
                "model": t.model,
                "is_shadow": t.is_shadow,
                "direction": t.direction,
                "entry": t.entry_price,
                "stop": t.stop_price,
                "target": t.target_price,
                "exit": t.exit_price,
                "outcome": t.outcome,
                "real_status": t.real_status,
                "real_profit": t.real_profit,
                "trade_id": str(t.trade_id),
            }
            for t in trades
        ]
        st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, height=350)

        st.divider()
        st.subheader("Trade drill-down")
        selected_id = st.selectbox(
            "Select a trade_id to see its full event chain",
            options=[r["trade_id"] for r in trade_rows],
        )
        selected_trade = next(t for t in trades if str(t.trade_id) == selected_id)

        colA, colB = st.columns(2)
        with colA:
            st.markdown("**Simulated outcome**")
            st.json(
                {
                    "direction": selected_trade.direction,
                    "entry": selected_trade.entry_price,
                    "stop": selected_trade.stop_price,
                    "target": selected_trade.target_price,
                    "exit": selected_trade.exit_price,
                    "outcome": selected_trade.outcome,
                    "realized_r": selected_trade.realized_r,
                    "setup_context": selected_trade.setup_context,
                }
            )
        with colB:
            st.markdown("**Real broker outcome** (null if shadow / never filled)")
            st.json(
                {
                    "real_status": selected_trade.real_status,
                    "real_fill_price": selected_trade.real_fill_price,
                    "real_close_price": selected_trade.real_close_price,
                    "real_close_reason": selected_trade.real_close_reason,
                    "real_profit": selected_trade.real_profit,
                    "partial_close_price": selected_trade.partial_close_price,
                    "partial_close_profit": selected_trade.partial_close_profit,
                }
            )

        chain = get_event_chain_for_trade(session, selected_trade)
        st.markdown(
            f"**Full event chain for {selected_trade.entry_time_ny.date()} "
            f"({selected_trade.model}, {len(chain['day_events'])} events that day)**"
        )
        st.caption(
            "Rows outlined below are the specific fill/close events matched to "
            "this trade (by direction + price, the same way the bot itself "
            "links them -- there's no direct foreign key)."
        )
        chain_rows = []
        for e in chain["day_events"]:
            tag = ""
            if chain["matched_fill"] and e.event_id == chain["matched_fill"].event_id:
                tag = "➡️ THIS TRADE'S FILL"
            if chain["matched_close"] and e.event_id == chain["matched_close"].event_id:
                tag = "➡️ THIS TRADE'S CLOSE"
            chain_rows.append(
                {"timestamp": e.timestamp, "event_type": e.event_type, "match": tag, "details": e.details}
            )
        st.dataframe(pd.DataFrame(chain_rows), use_container_width=True, height=400)

# ---------- Safety Checks ----------
with tab_safety:
    st.subheader("Safety check failures")
    st.caption(
        "Every fail-safe catch in the live path (DB errors, bridge errors, "
        "order rejections) journals here -- this is the queryable version "
        "of 'how many times has X failed this week', not just a log line."
    )
    hours_back_safety = st.slider("Hours back ", 1, 24 * 14, 24 * 7, key="safety_hours")
    failures = get_safety_failures(session, since=datetime.utcnow() - timedelta(hours=hours_back_safety))
    if not failures:
        st.success("No safety_check_failed events in this window.")
    else:
        fail_rows = [
            {
                "timestamp": e.timestamp,
                "model": e.model,
                "check_name": e.details.get("check_name"),
                "error": e.details.get("error"),
                "details": e.details,
            }
            for e in failures
        ]
        df_fail = pd.DataFrame(fail_rows)
        st.dataframe(df_fail, use_container_width=True, height=300)

        st.markdown("**Failure counts by check_name** (repeated failures are the ones worth investigating first)")
        counts = df_fail.groupby("check_name").size().sort_values(ascending=False)
        st.bar_chart(counts)

# ---------- Models ----------
with tab_models:
    st.subheader("Model configs")
    configs = get_model_configs(session)
    if not configs:
        st.info("No model_configs rows found.")
    else:
        cfg_rows = [
            {
                "model_name": c.model_name,
                "status": c.status,
                "risk_pct": c.risk_pct,
                "magic_number": c.magic_number,
                "max_concurrent_positions": c.max_concurrent_positions,
            }
            for c in configs
        ]
        st.dataframe(pd.DataFrame(cfg_rows), use_container_width=True)
        st.caption(
            "status: disabled = nothing runs · shadow = journals only, no real orders · "
            "active = the only state that places real orders."
        )

session.close()
