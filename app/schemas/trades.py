import datetime
import uuid

from pydantic import BaseModel

from app.schemas.events import EventOut


class TradeOut(BaseModel):
    trade_id: uuid.UUID
    model: str
    is_shadow: bool

    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float | None
    outcome: str | None
    realized_r: float | None
    # % of equity actually risked on THIS trade -- distinct from
    # ModelConfigOut.risk_pct, which is the model's current configured
    # setting and can drift from what a past trade actually used.
    risk_pct_used: float
    # The equity snapshot this trade's risk was sized against. Was never
    # exposed here despite always being set on Trade (NOT NULL) -- added
    # so the frontend can derive a real R-multiple (real_profit /
    # (equity_before * risk_pct_used)) for a trade whose `realized_r` is
    # null because it never went through simulated grading (orphan-
    # recovered / historically-reconciled), the same gap already fixed
    # for exit price and close time.
    equity_before: float
    # The equity snapshot right after this trade closed -- null until it
    # does. Exposed alongside equity_before for the same reason: it
    # existed on Trade the whole time and was simply never returned.
    equity_after: float | None
    # In practice only ever {trend, risk_pips} -- see Trade's own column
    # comment. Exposed as-is (a small dict) rather than picking specific
    # keys out of it, so this stays correct even if the shape changes.
    setup_context: dict

    entry_time_utc: datetime.datetime
    entry_time_ny: datetime.datetime
    exit_time_utc: datetime.datetime | None

    real_status: str | None
    # The actual broker order ticket -- lets a user cross-reference a
    # trade against their own MT5/Exness terminal directly, which was
    # previously only possible by asking for a raw DB query. Never
    # exposed before despite always being captured once a real fill
    # happens (migration 0022 widened this to BigInteger).
    real_position_ticket: int | None
    real_fill_price: float | None
    # When the real order actually filled at the broker -- distinct
    # from entry_time_ny (the bot's own simulated decision time), which
    # can differ by seconds to minutes. Never exposed before; only the
    # close-side real_close_time_ny got added earlier today.
    real_fill_time_ny: datetime.datetime | None
    real_close_price: float | None
    real_close_reason: str | None
    real_profit: float | None
    # The real broker close time -- was never exposed here even though
    # the column has always existed on Trade. Reported live: the trade
    # history page had no way to show when a trade actually closed at
    # all, real or simulated.
    real_close_time_ny: datetime.datetime | None

    class Config:
        from_attributes = True


class TradeEventChainOut(BaseModel):
    """The trader-facing "why was this trade placed" story --
    app.core.trade_story.build_trade_chain()'s result, narrated. Unlike
    admin's AdminEventChainOut (whole day's events + a best-effort
    match), `chain` here is scoped to exactly this trade's own
    raid -> mss -> fvg -> candidate -> fill -> close, in order --
    no other same-day candidates mixed in."""
    chain: list[EventOut]
    fully_resolved: bool
