from typing import Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded" | "down"
    account_label: str
    login: int
    connected: bool
    trade_allowed: Optional[bool] = None
    detail: Optional[str] = None


class AccountInfoResponse(BaseModel):
    login: int
    server: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: Optional[float] = None
    leverage: int
    currency: str


class TickResponse(BaseModel):
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    time_utc: str          # ISO 8601, tz-aware, UTC
    time_ny: str            # ISO 8601, tz-aware, America/New_York


class Candle(BaseModel):
    time_utc: str
    time_ny: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


class CandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    count: int
    candles: list[Candle]


# ---------------------------------------------------------------------------
# Phase 4: order placement / positions
# ---------------------------------------------------------------------------


class PlaceOrderRequest(BaseModel):
    symbol: str
    direction: str  # "buy" | "sell"
    volume: float   # lot size, e.g. 0.01
    stop_loss: float
    take_profit: float
    comment: str = ""  # truncated to 31 chars by MT5 itself; magic number
                         # (from config, not the caller) is the real
                         # identifying tag, this is just for human context


class OrderResult(BaseModel):
    position_ticket: int
    order_ticket: int
    deal_ticket: int
    symbol: str
    direction: str
    volume: float
    requested_price: float   # tick price at the moment this bridge decided to place the order
    fill_price: float        # authoritative broker fill price (see mt5_client.py)
    stop_loss: float
    take_profit: float
    magic: int
    time_utc: str
    time_ny: str
    retcode: int
    broker_comment: str
    fill_time_is_estimate: bool  # True only in the rare fallback case documented in mt5_client.py


class Position(BaseModel):
    ticket: int
    symbol: str
    direction: str
    volume: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    profit: float
    magic: int
    time_utc: str
    time_ny: str


class PositionsResponse(BaseModel):
    positions: list[Position]


class CloseResult(BaseModel):
    ticket: int
    close_price: float
    volume_closed: float
    time_utc: str
    time_ny: str
    retcode: int
    broker_comment: str


# ---------------------------------------------------------------------------
# Phase 4 step 2a: pending limit orders + position modify
# ---------------------------------------------------------------------------


class PlacePendingOrderRequest(BaseModel):
    symbol: str
    direction: str  # "long" | "short" -- maps to BUY_LIMIT / SELL_LIMIT, see mt5_client.py
    volume: float
    entry_price: float
    stop_loss: float
    comment: str = ""
    # No take_profit field, deliberately -- unknown at placement time.
    # Set it afterward via POST /positions/{ticket}/modify once the
    # position has actually filled and the target's been computed.


class PendingOrderResult(BaseModel):
    order_ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    stop_loss: float
    magic: int
    time_utc: str
    time_ny: str
    retcode: int
    broker_comment: str


class PendingOrder(BaseModel):
    order_ticket: int
    symbol: str
    direction: str
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float  # 0.0 until modified post-fill
    magic: int
    time_utc: str
    time_ny: str


class PendingOrdersResponse(BaseModel):
    orders: list[PendingOrder]


class CancelResult(BaseModel):
    order_ticket: int
    time_utc: str
    time_ny: str
    retcode: int
    broker_comment: str


class ModifyPositionRequest(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


class ModifyResult(BaseModel):
    ticket: int
    stop_loss: float
    take_profit: float
    time_utc: str
    time_ny: str
    retcode: int
    broker_comment: str