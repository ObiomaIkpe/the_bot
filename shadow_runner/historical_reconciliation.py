"""
Historical reconciliation Piece B (2026-09-05) -- the harder sibling of
shadow_runner/scripts/backfill_narrative_aug10_sept4_2026.py's Piece A.
Where Piece A reconstructs the DETECTION narrative only, this
reconciles REAL broker deals (actual fills/exits/profit) against it --
closing the "full historical reconciliation" gap deliberately deferred
since the original cross-day-recovery plan (see PENDING_ITEMS.md's
"Real bugs found 2026-09-02" section, and this file's own design in
misty-seeking-crescent.md's "Historical reconciliation" plan).

Deliberately NOT built into orphan_recovery.py -- that module's own
module docstring explicitly scopes it to still-open positions only;
this is a different, closed-trade problem needing a different bridge
capability (a date-range deals query, BridgeClient.get_deals_history(),
not a by-ticket lookup).

MANDATORY LIVE VALIDATION BEFORE THIS IS CONSIDERED DONE (see the plan
doc's B.5 section) -- not just tests: (1) confirm
mt5.history_deals_get(date_from, date_to)'s real inclusive/exclusive
boundary behavior against a live call, it isn't documented; (2) deploy
/history/deals alone and cross-check its output against the real MT5
terminal/mobile app for a known range; (3) run this module's own
reconcile_deals() in a dry-run (print, no db.commit()) against the
real account's actual Aug 10 -> Sept 4 deals, reviewed by a human,
before ever letting it write to the real database.
"""
import logging

from app.models import Event
from shadow_runner.order_manager import TARGET_LOOKBACK_BARS, compute_target
from shadow_runner.persistence import (
    get_current_equity,
    trade_exists_for_ticket,
    write_reconciled_historical_trade,
)

log = logging.getLogger("shadow_runner.historical_reconciliation")

# Deliberately NOT trade_story.py's 1e-9 float tolerance -- that value
# compares two SIMULATED numbers computed by the same deterministic
# replay logic, which should be bit-identical. Here we're comparing a
# REAL market fill against a THEORETICAL candidate price -- a limit
# order fills at its requested price "or better," but real broker
# execution still has some variance (spread/requote nuances), so an
# exact-to-the-9th-decimal match would essentially never occur and
# every real deal would wrongly fall through to "unmatched." 5 pips is
# a deliberately generous bound for a limit-order fill (which shouldn't
# see real slippage the way a market order can) -- matches
# shadow_runner/reconciliation.py's own PIP=0.0001 convention.
_PRICE_MATCH_TOLERANCE = 5 * 0.0001


def _group_deals_by_position(deals: list[dict]) -> dict:
    by_position = {}
    for d in deals:
        by_position.setdefault(d["position_id"], []).append(d)
    return by_position


def _find_matching_candidate_event(db, model: str, direction: str, entry_price: float, entry_date) -> Event | None:
    """
    Look for a Piece A-replayed trade_candidate_ready event on the
    deal's entry date, matching direction + a real-world price
    tolerance (see _PRICE_MATCH_TOLERANCE's own comment on why this is
    NOT trade_story.py's tight simulated-vs-simulated tolerance).
    trade_candidate_ready is always ownerless (user_id IS NULL) --
    shared detection narrative, never personal to one subscriber.
    """
    candidates = (
        db.query(Event)
        .filter(Event.event_type == "trade_candidate_ready", Event.model == model, Event.user_id.is_(None))
        .all()
    )
    for e in candidates:
        if e.timestamp.date() != entry_date:
            continue
        details = e.details
        if details.get("direction") != direction:
            continue
        if abs(details.get("entry", float("inf")) - entry_price) < _PRICE_MATCH_TOLERANCE:
            return e
    return None


def reconcile_deals(
    db, bridge, deals: list[dict], magic: int, user_id: str, model: str, risk_pct: float, symbol: str,
) -> list[dict]:
    """
    Correlates a raw list of broker deals (BridgeClient.get_deals_history()'s
    output, unfiltered) against known Trade rows and Piece A's replayed
    narrative. Returns a list of event dicts for the caller to journal
    (write_event() + db.commit()) -- kept as a pure return value, same
    pattern check_for_orphaned_positions() already uses, so this
    function stays testable without needing a real DB commit inside it.

    For each closed position (has both an "in" and exactly one "out"
    deal) not already recorded:
    - matched to a Piece A candidate -> a full Trade row gets written
      (write_reconciled_historical_trade()), using the candidate's own
      real detected stop and a freshly-computed target (same method
      orphan_recovery.py already uses for a live catch) -- never
      fabricated numbers.
    - not matched -> journaled as a raw fact only (historical_trade_reconciled,
      matched=False), no Trade row -- Trade.stop_price/target_price are
      NOT NULL with no honest value to put there for an unmatched deal
      (see write_reconciled_historical_trade()'s own docstring).

    Explicitly NOT handled this pass, flagged rather than guessed at:
    - Partial-close sequences (more than one "out" deal for one
      position) -- skipped with a safety_check_failed event rather than
      picking one close deal arbitrarily.
    - A position with no matching "in" deal at all (shouldn't happen for
      a real position, but never crashes on it -- silently skipped).
    """
    collected_events = []
    by_position = _group_deals_by_position([d for d in deals if d["magic"] == magic])

    for position_id, position_deals in sorted(by_position.items()):
        in_deals = [d for d in position_deals if d["entry"] == "in"]
        out_deals = [d for d in position_deals if d["entry"] == "out"]
        if not in_deals or not out_deals:
            continue  # no open deal (shouldn't happen), or still open -- not this piece's job

        if len(out_deals) > 1:
            collected_events.append({
                "event_type": "safety_check_failed",
                "timestamp": out_deals[-1]["time_ny"],
                "check_name": "historical_reconciliation_partial_close_sequence_skipped",
                "position_id": position_id,
            })
            continue

        if trade_exists_for_ticket(db, user_id, model, position_id):
            continue  # already recorded -- a live catch, or a prior reconciliation run

        entry_deal = in_deals[0]
        close_deal = out_deals[0]
        direction = "long" if entry_deal["type"] == "buy" else "short"

        candidate_event = _find_matching_candidate_event(
            db, model, direction, entry_deal["price"], entry_deal["time_ny"].date(),
        )

        if candidate_event is None:
            collected_events.append({
                "event_type": "historical_trade_reconciled",
                "timestamp": close_deal["time_ny"],
                "ticket": position_id,
                "matched": False,
                "direction": direction,
                "entry_price": entry_deal["price"],
                "close_price": close_deal["price"],
                "profit": close_deal["profit"],
                "symbol": entry_deal["symbol"],
            })
            continue

        stop_price = candidate_event.details.get("stop")
        if stop_price is None:
            # A candidate event missing its own stop would be a real,
            # separate bug elsewhere (every trade_candidate_ready is
            # supposed to carry one) -- treat it the same honest way as
            # "no match" rather than fabricate a stop, but flag it
            # distinctly so it's traceable back to that root cause.
            collected_events.append({
                "event_type": "safety_check_failed",
                "timestamp": close_deal["time_ny"],
                "check_name": "historical_reconciliation_candidate_missing_stop",
                "position_id": position_id,
            })
            continue

        try:
            candles = bridge.get_candles_paginated(symbol, "M5", 9000)
            before_fill = [b for b in candles if b["time_utc"] < entry_deal["time_utc"]]
            target_price = compute_target(before_fill[-TARGET_LOOKBACK_BARS:], direction)
        except Exception as e:
            collected_events.append({
                "event_type": "safety_check_failed",
                "timestamp": close_deal["time_ny"],
                "check_name": "historical_reconciliation_target_compute_failed",
                "error": str(e),
                "position_id": position_id,
            })
            continue

        equity_before = get_current_equity(db, user_id, model, bridge_starting_equity=None)
        if equity_before is None:  # no prior trade at all -- seed from the real account's own balance
            equity_before = bridge.account_info()["balance"]

        trade = write_reconciled_historical_trade(
            db,
            direction=direction,
            # The SIMULATED entry, not the real deal's fill price --
            # app.core.trade_story._find_fill() matches Trade.entry_price
            # against the replayed order_filled event's own entry via a
            # tight 1e-9 tolerance (two numbers computed by the same
            # deterministic logic, expected to be identical). The real
            # fill legitimately differs by real slippage and goes in
            # real_fill_price below instead -- same dual-tracking split
            # write_trade() already uses for every normal trade.
            entry_price=candidate_event.details["entry"],
            stop_price=stop_price,
            target_price=target_price,
            entry_deal=entry_deal,
            close_deal=close_deal,
            user_id=user_id,
            model=model,
            risk_pct=risk_pct,
            equity_before=equity_before,
        )
        collected_events.append({
            "event_type": "historical_trade_reconciled",
            "timestamp": close_deal["time_ny"],
            "ticket": position_id,
            "matched": True,
            "trade_id": str(trade.trade_id),
        })

    return collected_events
