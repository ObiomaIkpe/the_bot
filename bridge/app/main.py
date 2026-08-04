"""
MT5 bridge worker.

Phase 2/3: read-only. Phase 4: order placement added, gated behind
BridgeConfig.orders_enabled -- see mt5_client.py's module docstring for
the full safety reasoning. One process = one account = one port. Run
with a SINGLE uvicorn worker (no --workers > 1, no gunicorn
multi-worker) — mt5.initialize() holds one connection per OS process, so
a second worker process would either fail to connect or silently fight
the first for the same terminal.

Run (from C:\\bridge, with venv active):
    uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1

Config path defaults to C:\\bridge\\config.json; override with the
BRIDGE_CONFIG_PATH env var to run a second worker for a second account
against a different config file and port.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app import mt5_client
from app.config import get_config
from app.models import (
    AccountInfoResponse,
    CancelResult,
    CandlesResponse,
    CloseResult,
    HealthResponse,
    ModifyPositionRequest,
    ModifyResult,
    OrderResult,
    PendingOrder,
    PendingOrdersResponse,
    PendingOrderResult,
    PlaceOrderRequest,
    PlacePendingOrderRequest,
    Position,
    PositionsResponse,
    TickResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bridge.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    mt5_client.startup(config)
    yield
    mt5_client.shutdown()


app = FastAPI(
    title="MT5 Bridge Worker",
    description="MT5 data + order bridge. Order endpoints gated behind orders_enabled config.",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
def get_health():
    config = get_config()
    h = mt5_client.health()
    status = "ok" if h["connected"] else "down"
    return HealthResponse(
        status=status,
        account_label=config.account_label,
        login=config.login,
        connected=h["connected"],
        trade_allowed=h["trade_allowed"],
        detail=h["detail"],
    )


@app.get("/account_info", response_model=AccountInfoResponse)
def get_account_info():
    try:
        data = mt5_client.account_info()
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return AccountInfoResponse(**data)


@app.get("/tick", response_model=TickResponse)
def get_tick(symbol: str = Query(default=None, description="Defaults to config's default_symbol, e.g. EURUSDm")):
    config = get_config()
    sym = symbol or config.default_symbol
    try:
        data = mt5_client.tick(sym)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return TickResponse(**data)


@app.get("/candles", response_model=CandlesResponse)
def get_candles(
    symbol: str = Query(default=None, description="Defaults to config's default_symbol, e.g. EURUSDm"),
    timeframe: str = Query(default="M5", description="M1, M5, M15, M30, H1, H4, D1"),
    count: int = Query(default=100, ge=1, le=5000, description="Number of most recent closed/forming bars"),
):
    config = get_config()
    sym = symbol or config.default_symbol
    try:
        rows = mt5_client.candles(sym, timeframe, count)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return CandlesResponse(symbol=sym, timeframe=timeframe, count=len(rows), candles=rows)


# ---------------------------------------------------------------------------
# Phase 4: order placement / positions. Every endpoint below checks
# orders_enabled FIRST, before touching mt5_client at all -- see
# BridgeConfig.orders_enabled's docstring and mt5_client.py's module
# docstring for the full safety reasoning. This is a 403, not a 404: the
# endpoint exists, it's just deliberately turned off.
# ---------------------------------------------------------------------------


def _require_orders_enabled(config) -> None:
    if not config.orders_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Order placement is disabled for this bridge worker "
                "(orders_enabled=false in config.json). This is a "
                "deliberate safety default, not an error -- set "
                "orders_enabled: true in config.json and restart the "
                "bridge to enable it."
            ),
        )


@app.post("/orders", response_model=OrderResult)
def place_order(order: PlaceOrderRequest):
    config = get_config()
    _require_orders_enabled(config)
    try:
        data = mt5_client.place_market_order(
            symbol=order.symbol,
            direction=order.direction,
            volume=order.volume,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            comment=order.comment,
            magic=order.magic if order.magic is not None else config.magic_number,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return OrderResult(**data)


@app.get("/positions", response_model=PositionsResponse)
def get_positions(
    only_ours: bool = Query(
        default=True,
        description=(
            "If true (default), only return positions tagged with this "
            "worker's magic number -- excludes anything placed manually "
            "or by another tool on the same account. Set false to see "
            "every open position regardless of origin."
        ),
    ),
):
    config = get_config()
    _require_orders_enabled(config)
    magic = config.magic_number if only_ours else None
    try:
        rows = mt5_client.get_positions(magic)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return PositionsResponse(positions=[Position(**r) for r in rows])


@app.post("/positions/{ticket}/close", response_model=CloseResult)
def close_position(ticket: int):
    config = get_config()
    _require_orders_enabled(config)
    try:
        data = mt5_client.close_position(ticket)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return CloseResult(**data)


# ---------------------------------------------------------------------------
# Phase 4 step 2a: pending limit orders + position modify. Same
# orders_enabled gate as everything else above.
# ---------------------------------------------------------------------------


@app.post("/orders/pending", response_model=PendingOrderResult)
def place_pending_order(order: PlacePendingOrderRequest):
    config = get_config()
    _require_orders_enabled(config)
    try:
        data = mt5_client.place_pending_limit_order(
            symbol=order.symbol,
            direction=order.direction,
            volume=order.volume,
            entry_price=order.entry_price,
            stop_loss=order.stop_loss,
            comment=order.comment,
            magic=order.magic if order.magic is not None else config.magic_number,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return PendingOrderResult(**data)


@app.get("/orders/pending", response_model=PendingOrdersResponse)
def get_pending_orders(
    only_ours: bool = Query(
        default=True,
        description="If true (default), only this worker's own pending orders (by magic number).",
    ),
):
    config = get_config()
    _require_orders_enabled(config)
    magic = config.magic_number if only_ours else None
    try:
        rows = mt5_client.get_pending_orders(magic)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return PendingOrdersResponse(orders=[PendingOrder(**r) for r in rows])


@app.delete("/orders/pending/{ticket}", response_model=CancelResult)
def cancel_pending_order(ticket: int):
    config = get_config()
    _require_orders_enabled(config)
    try:
        data = mt5_client.cancel_pending_order(ticket)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return CancelResult(**data)


@app.post("/positions/{ticket}/modify", response_model=ModifyResult)
def modify_position(ticket: int, modification: ModifyPositionRequest):
    config = get_config()
    _require_orders_enabled(config)
    if modification.stop_loss is None and modification.take_profit is None:
        raise HTTPException(status_code=400, detail="Provide at least one of stop_loss or take_profit")
    try:
        data = mt5_client.modify_position(ticket, modification.stop_loss, modification.take_profit)
    except mt5_client.MT5Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    return ModifyResult(**data)