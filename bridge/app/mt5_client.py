"""
Thin wrapper around the MetaTrader5 package.

Hard constraints this file exists to enforce:
  - One mt5.initialize() per OS process. This module holds the ONE connection
    for this worker. Never call mt5.initialize() a second time in-process.
  - MT5's python package has thread affinity: calls made from a different OS
    thread than the one that ran initialize() hang indefinitely rather than
    erroring (observed directly on this VPS -- request arrived, handler never
    returned, no exception, no log line). So EVERY mt5.* call in this file,
    including initialize() itself, is routed through a single dedicated
    background thread (_EXECUTOR, max_workers=1) via _run(). Never call
    mt5.* directly from a FastAPI request-handler thread.
  - No hardcoded UTC/NY offset constants -- DST shifts the NY offset across
    the year. Always convert via zoneinfo("America/New_York") at request
    time, never store or assume a fixed hour delta. (rulebook Section 33.3)

PHASE 4 CHANGE -- THIS FILE IS NO LONGER READ-ONLY
-----------------------------------------------------
Phase 2/3 kept this file strictly read-only by design. Phase 4 adds real
order placement: place_market_order(), get_positions(), close_position().
This is the single most consequential change in the whole project --
these functions place REAL orders against whatever MT5 account this
worker is configured for. Nothing in this file knows or cares whether
that account is a demo or a real-money account; that distinction lives
entirely in which credentials are in config.json. Treat config.json
(and which account it points at) as the actual safety boundary, not
this code.

Two safety layers exist ABOVE the raw mt5.order_send() call:
  1. BridgeConfig.orders_enabled -- checked in main.py before these
     functions are even reachable via HTTP. Defaults to False.
  2. Every order this bridge places carries BridgeConfig.magic_number,
     so it's always identifiable (in /positions, and in the MT5
     terminal's own history) as something THIS system placed, not a
     manual trade or another tool's.
Neither of those layers second-guesses an individual order's size,
direction, or price -- that judgment belongs entirely to the caller
(the live-trading runner, not the bridge). This file's job is narrow
and mechanical: place exactly what it's told, once per call, and
report back exactly what actually happened.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5

from app.config import BridgeConfig

log = logging.getLogger("bridge.mt5_client")

NY_TZ = ZoneInfo("America/New_York")

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# Single dedicated thread -- every mt5.* call in this process runs here.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-worker")
_initialized = False
_config: BridgeConfig | None = None


def _run(fn, *args, timeout: float = 15.0):
    """Submit fn(*args) to the dedicated MT5 thread and block for the result.
    timeout guards against a repeat of the cross-thread hang turning into a
    silent forever-hang if something is still wrong -- callers get a clear
    TimeoutError instead of nothing."""
    future = _EXECUTOR.submit(fn, *args)
    return future.result(timeout=timeout)


class MT5Error(Exception):
    """Raised on any mt5 call failure; carries mt5.last_error() detail."""


def _fail(context: str) -> None:
    code, desc = mt5.last_error()
    raise MT5Error(f"{context} failed: mt5.last_error() = ({code}, {desc!r})")


def _do_initialize(config: BridgeConfig) -> None:
    """Runs ON the dedicated thread."""
    global _initialized
    ok = mt5.initialize(
        path=config.mt5_terminal_path,
        login=config.login,
        password=config.password,
        server=config.server,
        portable=True,
    )
    if not ok:
        _fail("mt5.initialize")
    _initialized = True


def startup(config: BridgeConfig) -> None:
    """Call once at FastAPI startup. Establishes the single connection --
    on the dedicated thread, so every later call is same-thread as init."""
    global _config
    _config = config
    _run(_do_initialize, config)
    log.info(
        "MT5 connection established: account=%s server=%s label=%s",
        config.login, config.server, config.account_label,
    )


def _do_shutdown() -> None:
    global _initialized
    if _initialized:
        mt5.shutdown()
        _initialized = False


def shutdown() -> None:
    _run(_do_shutdown)
    _EXECUTOR.shutdown(wait=True)
    log.info("MT5 connection closed")


def _ensure_connected() -> None:
    """Runs ON the dedicated thread (called only from within other _do_*
    functions below -- never call this directly from a request-handler thread)."""
    global _initialized
    if _initialized and mt5.terminal_info() is not None:
        return
    log.warning("MT5 connection appears down, attempting reconnect")
    if _config is None:
        raise MT5Error("mt5 client used before startup() was called")
    ok = mt5.initialize(
        path=_config.mt5_terminal_path,
        login=_config.login,
        password=_config.password,
        server=_config.server,
        portable=True,
    )
    if not ok:
        _initialized = False
        _fail("mt5.initialize (reconnect)")
    _initialized = True
    log.info("MT5 reconnect succeeded")


def _to_ny(ts_epoch: int) -> tuple[str, str]:
    """Convert an MT5 epoch-seconds timestamp (server time, GMT+0) to
    (utc_iso, ny_iso) -- both tz-aware. Server is fixed GMT+0, so the epoch
    value equals a UTC instant directly; NY offset is derived fresh from
    zoneinfo, never hardcoded, so it tracks DST automatically."""
    utc_dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
    ny_dt = utc_dt.astimezone(NY_TZ)
    return utc_dt.isoformat(), ny_dt.isoformat()


def _do_health() -> dict:
    try:
        _ensure_connected()
    except MT5Error as e:
        return {"connected": False, "trade_allowed": None, "detail": str(e)}
    term = mt5.terminal_info()
    acct = mt5.account_info()
    if term is None or acct is None:
        return {"connected": False, "trade_allowed": None, "detail": "terminal_info/account_info returned None"}
    return {
        "connected": bool(term.connected),
        "trade_allowed": bool(acct.trade_allowed),
        "detail": None,
    }


def health() -> dict:
    return _run(_do_health)


def _do_account_info() -> dict:
    _ensure_connected()
    acct = mt5.account_info()
    if acct is None:
        _fail("mt5.account_info")
    return {
        "login": acct.login,
        "server": acct.server,
        "balance": acct.balance,
        "equity": acct.equity,
        "margin": acct.margin,
        "margin_free": acct.margin_free,
        "margin_level": acct.margin_level if acct.margin > 0 else None,
        "leverage": acct.leverage,
        "currency": acct.currency,
    }


def account_info() -> dict:
    return _run(_do_account_info)


def _do_tick(symbol: str) -> dict:
    _ensure_connected()
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        _fail(f"mt5.symbol_info_tick({symbol!r})")
    time_utc, time_ny = _to_ny(t.time)
    return {
        "symbol": symbol,
        "bid": t.bid,
        "ask": t.ask,
        "last": t.last,
        "volume": t.volume,
        "time_utc": time_utc,
        "time_ny": time_ny,
    }


def tick(symbol: str) -> dict:
    return _run(_do_tick, symbol)


def _do_candles(symbol: str, timeframe: str, count: int) -> list[dict]:
    if timeframe not in TIMEFRAME_MAP:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Choose from {sorted(TIMEFRAME_MAP)}")
    _ensure_connected()
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_MAP[timeframe], 0, count)
    if rates is None:
        _fail(f"mt5.copy_rates_from_pos({symbol!r}, {timeframe!r}, count={count})")
    out = []
    for r in rates:
        time_utc, time_ny = _to_ny(int(r["time"]))
        out.append({
            "time_utc": time_utc,
            "time_ny": time_ny,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
            "spread": int(r["spread"]),
            "real_volume": int(r["real_volume"]),
        })
    return out


def candles(symbol: str, timeframe: str, count: int) -> list[dict]:
    return _run(_do_candles, symbol, timeframe, count)


def _do_symbol_info(symbol: str) -> dict:
    _ensure_connected()
    info = mt5.symbol_info(symbol)
    if info is None:
        _fail(f"mt5.symbol_info({symbol!r})")
    return {
        "symbol": symbol,
        "digits": info.digits,
        "trade_contract_size": info.trade_contract_size,
        "trade_tick_size": info.trade_tick_size,
        "trade_tick_value": info.trade_tick_value,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
    }


def symbol_info(symbol: str) -> dict:
    """
    Read-only -- the REAL contract specification for this symbol,
    straight from MT5/the broker, not an assumed figure. Added
    specifically to replace the unverified "$10/pip per standard lot"
    default that shadow_runner/order_manager.py's compute_lot_size()
    used to rely on -- see that module's docstring for the full story.
    No orders_enabled gate needed (matches the other read-only
    endpoints from Phase 2) -- this is pure information, no order
    placement involved.
    """
    return _run(_do_symbol_info, symbol)


# ---------------------------------------------------------------------------
# Phase 4: order placement. See this file's module docstring above for the
# safety layers that sit ABOVE these functions -- they are deliberately not
# repeated here.
# ---------------------------------------------------------------------------


def _do_place_market_order(
    symbol: str, direction: str, volume: float,
    stop_loss: float, take_profit: float,
    comment: str, magic: int,
) -> dict:
    _ensure_connected()

    t = mt5.symbol_info_tick(symbol)
    if t is None:
        _fail(f"mt5.symbol_info_tick({symbol!r}) before order placement")

    if direction == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        requested_price = t.ask
    elif direction == "sell":
        order_type = mt5.ORDER_TYPE_SELL
        requested_price = t.bid
    else:
        raise ValueError(f"direction must be 'buy' or 'sell', got {direction!r}")

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": requested_price,
        "sl": stop_loss,
        "tp": take_profit,
        # Allowed slippage in points between the price above and the
        # actual fill. Not yet tuned against real Exness fill behavior --
        # verify this is sensible once live orders have actually run;
        # see PHASE4_BRIDGE_ORDERS.md.
        "deviation": 20,
        "magic": magic,
        "comment": comment[:31],  # MT5 caps comment length at 31 chars
        "type_time": mt5.ORDER_TIME_GTC,
        # IMPORTANT, UNVERIFIED: MT5 filling-mode support is broker- and
        # symbol-specific. IOC is a common default but has NOT been
        # confirmed against Exness/EURUSDm specifically. If order_send
        # fails with a filling-mode-related retcode, this is the first
        # thing to check -- see PHASE4_BRIDGE_ORDERS.md's manual test
        # checklist, which must pass before this is ever wired into the
        # live runner.
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        _fail("mt5.order_send returned None")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(
            f"order_send did not complete: retcode={result.retcode}, "
            f"comment={result.comment!r}"
        )

    # Look up the resulting position for an AUTHORITATIVE broker-side fill
    # time/price, rather than trusting our own clock or the request echo --
    # this is exactly the data Option B's slippage measurement needs
    # (requested_price above vs. fill_price below).
    position_ticket = result.order
    positions = mt5.positions_get(ticket=position_ticket)
    if positions:
        fill_time_epoch = positions[0].time
        fill_price = positions[0].price_open
    else:
        # Shouldn't normally happen right after TRADE_RETCODE_DONE, but
        # don't fail the whole call over a lookup miss -- fall back to
        # the order_send result itself, flagged clearly in the response.
        log.warning(
            "positions_get(ticket=%s) returned nothing right after a "
            "successful order_send -- using order_send's own price/now() "
            "as a fallback, flagged in the response as fill_time_is_estimate",
            position_ticket,
        )
        fill_time_epoch = int(datetime.now(timezone.utc).timestamp())
        fill_price = result.price

    time_utc, time_ny = _to_ny(fill_time_epoch)
    return {
        "position_ticket": position_ticket,
        "order_ticket": result.order,
        "deal_ticket": result.deal,
        "symbol": symbol,
        "direction": direction,
        "volume": result.volume,
        "requested_price": requested_price,
        "fill_price": fill_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "magic": magic,
        "time_utc": time_utc,
        "time_ny": time_ny,
        "retcode": result.retcode,
        "broker_comment": result.comment,
        "fill_time_is_estimate": not bool(positions),
    }


def place_market_order(
    symbol: str, direction: str, volume: float,
    stop_loss: float, take_profit: float,
    comment: str, magic: int,
) -> dict:
    return _run(_do_place_market_order, symbol, direction, volume, stop_loss, take_profit, comment, magic)


def _do_get_positions(magic: int | None) -> list[dict]:
    _ensure_connected()
    positions = mt5.positions_get()
    if positions is None:
        _fail("mt5.positions_get")
    out = []
    for p in positions:
        if magic is not None and p.magic != magic:
            continue
        time_utc, time_ny = _to_ny(p.time)
        out.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "direction": "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
            "volume": p.volume,
            "open_price": p.price_open,
            "current_price": p.price_current,
            "stop_loss": p.sl,
            "take_profit": p.tp,
            "profit": p.profit,
            "magic": p.magic,
            "time_utc": time_utc,
            "time_ny": time_ny,
        })
    return out


def get_positions(magic: int | None = None) -> list[dict]:
    return _run(_do_get_positions, magic)


def _do_close_position(ticket: int) -> dict:
    _ensure_connected()
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise MT5Error(f"No open position found with ticket {ticket}")
    pos = positions[0]

    t = mt5.symbol_info_tick(pos.symbol)
    if t is None:
        _fail(f"mt5.symbol_info_tick({pos.symbol!r}) before closing position")

    # Closing a BUY position means placing a SELL, and vice versa.
    if pos.type == mt5.ORDER_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = t.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = t.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "position": ticket,  # ties this order to the specific position being closed
        "price": price,
        "deviation": 20,
        "magic": pos.magic,
        "comment": "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        _fail("mt5.order_send (close) returned None")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(
            f"close did not complete: retcode={result.retcode}, comment={result.comment!r}"
        )

    time_utc, time_ny = _to_ny(int(datetime.now(timezone.utc).timestamp()))
    return {
        "ticket": ticket,
        "close_price": result.price,
        "volume_closed": result.volume,
        "time_utc": time_utc,
        "time_ny": time_ny,
        "retcode": result.retcode,
        "broker_comment": result.comment,
    }


def close_position(ticket: int) -> dict:
    return _run(_do_close_position, ticket)


# ---------------------------------------------------------------------------
# Phase 4 step 2a: pending limit orders + position modify.
#
# WHY THIS EXISTS SEPARATELY FROM place_market_order(): the strategy's
# take-profit target is computed from the 6 bars immediately BEFORE the
# fill -- it literally cannot be known before the fill happens (see this
# phase's design discussion). A market order fills instantly with no
# window to compute anything first. A PENDING limit order, by contrast,
# sits at the strategy's exact computed entry price and waits for the
# market to reach it -- faithfully matching how TradeAttempt already
# simulates fills in the batch/streaming model, and giving the caller a
# real gap between "order placed" and "order filled" in which to compute
# the target and then attach it via modify_position().
#
# Direction -> MT5 order type is fixed, not caller-configurable: this
# strategy's FVG entries are always a retracement INTO a recent price
# level, never a breakout through one. Long always waits for price to
# dip down to entry (BUY_LIMIT); short always waits for price to rise up
# to entry (SELL_LIMIT). There is no scenario in this model where a stop
# order (buy above market / sell below market) is correct.
# ---------------------------------------------------------------------------


def _do_place_pending_limit_order(
    symbol: str, direction: str, volume: float,
    entry_price: float, stop_loss: float,
    comment: str, magic: int,
) -> dict:
    _ensure_connected()

    if direction == "long":
        order_type = mt5.ORDER_TYPE_BUY_LIMIT
    elif direction == "short":
        order_type = mt5.ORDER_TYPE_SELL_LIMIT
    else:
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry_price,
        "sl": stop_loss,
        "tp": 0.0,  # deliberately not set -- unknown until fill, see module note above
        "type_time": mt5.ORDER_TIME_GTC,
        # Expiry is managed by the CALLER (live-order-manager), not MT5's
        # own day-order semantics -- the strategy's day_end (5pm NY) is
        # the authoritative boundary, and MT5's server-side day rollover
        # doesn't necessarily line up with it. Cancel explicitly via
        # cancel_pending_order() instead of relying on this expiring on
        # its own.
        "magic": magic,
        "comment": comment[:31],
    }

    result = mt5.order_send(request)
    if result is None:
        _fail("mt5.order_send (pending) returned None")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(
            f"pending order_send did not complete: retcode={result.retcode}, "
            f"comment={result.comment!r}"
        )

    time_utc, time_ny = _to_ny(int(datetime.now(timezone.utc).timestamp()))
    return {
        "order_ticket": result.order,
        "symbol": symbol,
        "direction": direction,
        "volume": volume,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "magic": magic,
        "time_utc": time_utc,
        "time_ny": time_ny,
        "retcode": result.retcode,
        "broker_comment": result.comment,
    }


def place_pending_limit_order(
    symbol: str, direction: str, volume: float,
    entry_price: float, stop_loss: float,
    comment: str, magic: int,
) -> dict:
    return _run(
        _do_place_pending_limit_order, symbol, direction, volume, entry_price, stop_loss, comment, magic
    )


def _do_get_pending_orders(magic: int | None) -> list[dict]:
    _ensure_connected()
    orders = mt5.orders_get()
    if orders is None:
        _fail("mt5.orders_get")
    out = []
    for o in orders:
        if magic is not None and o.magic != magic:
            continue
        if o.type not in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT):
            continue  # this bridge only ever places limit orders; ignore anything else on the account
        time_utc, time_ny = _to_ny(o.time_setup)
        out.append({
            "order_ticket": o.ticket,
            "symbol": o.symbol,
            "direction": "long" if o.type == mt5.ORDER_TYPE_BUY_LIMIT else "short",
            "volume": o.volume_current,
            "entry_price": o.price_open,
            "stop_loss": o.sl,
            "take_profit": o.tp,  # 0.0 until modify_position() sets it post-fill
            "magic": o.magic,
            "time_utc": time_utc,
            "time_ny": time_ny,
        })
    return out


def get_pending_orders(magic: int | None = None) -> list[dict]:
    return _run(_do_get_pending_orders, magic)


def _do_cancel_pending_order(ticket: int) -> dict:
    _ensure_connected()
    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }
    result = mt5.order_send(request)
    if result is None:
        _fail("mt5.order_send (cancel) returned None")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(
            f"cancel did not complete: retcode={result.retcode}, comment={result.comment!r}"
        )
    time_utc, time_ny = _to_ny(int(datetime.now(timezone.utc).timestamp()))
    return {
        "order_ticket": ticket,
        "time_utc": time_utc,
        "time_ny": time_ny,
        "retcode": result.retcode,
        "broker_comment": result.comment,
    }


def cancel_pending_order(ticket: int) -> dict:
    return _run(_do_cancel_pending_order, ticket)


def _do_modify_position(ticket: int, stop_loss: float | None, take_profit: float | None) -> dict:
    _ensure_connected()
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise MT5Error(f"No open position found with ticket {ticket}")
    pos = positions[0]

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": pos.symbol,
        "sl": stop_loss if stop_loss is not None else pos.sl,
        "tp": take_profit if take_profit is not None else pos.tp,
    }
    result = mt5.order_send(request)
    if result is None:
        _fail("mt5.order_send (modify) returned None")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise MT5Error(
            f"modify did not complete: retcode={result.retcode}, comment={result.comment!r}"
        )
    time_utc, time_ny = _to_ny(int(datetime.now(timezone.utc).timestamp()))
    return {
        "ticket": ticket,
        "stop_loss": request["sl"],
        "take_profit": request["tp"],
        "time_utc": time_utc,
        "time_ny": time_ny,
        "retcode": result.retcode,
        "broker_comment": result.comment,
    }


def modify_position(ticket: int, stop_loss: float | None = None, take_profit: float | None = None) -> dict:
    return _run(_do_modify_position, ticket, stop_loss, take_profit)