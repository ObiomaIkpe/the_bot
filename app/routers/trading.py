"""
Live bridge actions for the admin API: list/close positions, list/cancel
pending orders. Every route proxies through this Hetzner-side service to
the Windows bridge -- the browser never talks to the bridge directly (it
has no auth of its own; see ADMIN_FRONTEND_PLAN.md's "non-negotiable
architecture rule").

Ownership: bridge/app/models.py's Position and PendingOrder both carry a
`magic` field, and model_configs.magic_number is globally unique across
the whole system (see app/models/model_config.py). So a ticket belongs to
the current user if and only if its magic matches one of THEIR
model_configs' magic numbers -- fetched fresh per request, never assumed.

Which bridge: resolved per-user from their own active BrokerCredential
row (broker_credentials.bridge_url -- see migration 0008), not a single
global setting. Each MT5 account has its own bridge worker/port; a
global BRIDGE_URL only ever worked for exactly one account.
"""
import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.broker_credential import BrokerCredential
from app.models.model_config import ModelConfig
from app.models.trade import Trade
from app.models.user import User
from bridge.app.models import AccountInfoResponse, CancelResult, CloseResult, HealthResponse, PendingOrder, Position
from shadow_runner.bridge_client import BridgeClient, BridgeError
from shadow_runner.persistence import write_event

router = APIRouter(prefix="/trading", tags=["trading"])

_NY_TZ = ZoneInfo("America/New_York")


def get_bridge_client(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BridgeClient:
    cred = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.user_id == current_user.user_id, BrokerCredential.is_active.is_(True))
        .first()
    )
    if cred is None or not cred.bridge_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active, bridge-connected broker credential configured for this account",
        )
    return BridgeClient(cred.bridge_url)


def _user_model_configs(db: Session, user_id) -> list[ModelConfig]:
    return db.query(ModelConfig).filter(ModelConfig.user_id == user_id).all()


def _find_owned_position(db: Session, bridge: BridgeClient, user_id, ticket: int):
    """Returns (position dict, model_name) if `ticket` belongs to one of
    this user's own magic numbers, else None."""
    for mc in _user_model_configs(db, user_id):
        for p in bridge.get_positions(mc.magic_number):
            if p["ticket"] == ticket:
                return p, mc.model_name
    return None


def _find_owned_pending_order(db: Session, bridge: BridgeClient, user_id, order_ticket: int):
    for mc in _user_model_configs(db, user_id):
        for o in bridge.get_pending_orders(mc.magic_number):
            if o["order_ticket"] == order_ticket:
                return o, mc.model_name
    return None


@router.get("/account-info", response_model=AccountInfoResponse)
def get_account_info(bridge: BridgeClient = Depends(get_bridge_client)):
    try:
        return bridge.account_info()
    except BridgeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/health", response_model=HealthResponse)
def get_bridge_health(bridge: BridgeClient = Depends(get_bridge_client)):
    """Distinct from account-info's 503/502: this can also report 200
    with connected=False, meaning the bridge process itself is reachable
    but its MT5 terminal isn't -- a third state account-info alone can't
    tell apart from "bridge unreachable" (502)."""
    try:
        return bridge.health()
    except BridgeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/positions", response_model=list[Position])
def list_positions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bridge: BridgeClient = Depends(get_bridge_client),
):
    try:
        positions = []
        for mc in _user_model_configs(db, current_user.user_id):
            positions.extend(bridge.get_positions(mc.magic_number))
        return positions
    except BridgeError as e:
        # Same reasoning as close_position()'s mapping below: the
        # bridge's GET /positions is ALSO gated behind its own
        # orders_enabled kill switch (not just the write endpoints) --
        # every freshly-provisioned bridge worker starts with this off,
        # so this is the single most common cause here, not a genuine
        # gateway failure. Surface as a conflict so the frontend can show
        # a specific, friendly message instead of a raw error string.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/pending-orders", response_model=list[PendingOrder])
def list_pending_orders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bridge: BridgeClient = Depends(get_bridge_client),
):
    try:
        orders = []
        for mc in _user_model_configs(db, current_user.user_id):
            orders.extend(bridge.get_pending_orders(mc.magic_number))
        return orders
    except BridgeError as e:
        # Same reasoning as list_positions() above.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/positions/{ticket}/close", response_model=CloseResult)
def close_position(
    ticket: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bridge: BridgeClient = Depends(get_bridge_client),
):
    try:
        found = _find_owned_position(db, bridge, current_user.user_id, ticket)
    except BridgeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")
    _, model_name = found

    try:
        result = bridge.close_position(ticket)
    except BridgeError as e:
        # Most commonly the bridge's own orders_enabled kill switch is
        # off -- surface as a conflict, not a generic 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    write_event(
        db,
        {
            "event_type": "manual_close_requested",
            "timestamp": datetime.datetime.now(_NY_TZ).replace(tzinfo=None),
            "ticket": ticket,
            "result": result,
        },
        current_user.user_id,
        model_name,
    )

    # Best-effort: tag the matching Trade row's real_close_reason, if one
    # exists and hasn't already been set (e.g. by the background
    # reconciliation flow in position_tracker.py). Never blocks the
    # close itself -- the action already succeeded against the broker
    # by this point regardless of what happens here.
    trade = (
        db.query(Trade)
        .filter(Trade.user_id == current_user.user_id, Trade.real_position_ticket == ticket)
        .first()
    )
    if trade is not None and trade.real_close_reason is None:
        trade.real_close_reason = "manual"

    db.commit()
    return result


@router.delete("/pending-orders/{order_ticket}", response_model=CancelResult)
def cancel_pending_order(
    order_ticket: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    bridge: BridgeClient = Depends(get_bridge_client),
):
    try:
        found = _find_owned_pending_order(db, bridge, current_user.user_id, order_ticket)
    except BridgeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending order not found")
    _, model_name = found

    try:
        result = bridge.cancel_pending_order(order_ticket)
    except BridgeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    write_event(
        db,
        {
            "event_type": "manual_cancel_requested",
            "timestamp": datetime.datetime.now(_NY_TZ).replace(tzinfo=None),
            "order_ticket": order_ticket,
            "result": result,
        },
        current_user.user_id,
        model_name,
    )
    db.commit()
    return result
