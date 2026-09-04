import itertools

import pytest

from app.main import app
from app.models.broker_credential import BrokerCredential
from app.models.model_config import ModelConfig
from app.models.trade import Trade
from app.routers import trading
from shadow_runner.bridge_client import BridgeError

_magic_counter = itertools.count(820001)


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


class FakeBridge:
    def __init__(self, positions=None, pending_orders=None, raise_on_action=None, raise_on_list=None):
        self._positions = positions or []
        self._pending_orders = pending_orders or []
        self._raise_on_action = raise_on_action
        # Separate from raise_on_action: close_position()/cancel_pending_order()
        # both look up the target via get_positions()/get_pending_orders()
        # FIRST (_find_owned_position/_find_owned_pending_order), before
        # ever reaching the actual close/cancel call -- raise_on_action
        # alone raising from get_positions() too would short-circuit that
        # lookup and never exercise the close/cancel path those tests want.
        self._raise_on_list = raise_on_list
        self.closed = []
        self.cancelled = []

    def get_positions(self, magic):
        if self._raise_on_list:
            raise self._raise_on_list
        return [p for p in self._positions if p["magic"] == magic]

    def get_pending_orders(self, magic):
        if self._raise_on_list:
            raise self._raise_on_list
        return [o for o in self._pending_orders if o["magic"] == magic]

    def close_position(self, ticket):
        if self._raise_on_action:
            raise self._raise_on_action
        self.closed.append(ticket)
        return {
            "ticket": ticket, "close_price": 1.1050, "volume_closed": 1.0,
            "time_utc": "2026-08-26T14:00:00+00:00", "time_ny": "2026-08-26T10:00:00-04:00",
            "retcode": 10009, "broker_comment": "closed by admin",
        }

    def account_info(self):
        return {
            "login": 1, "server": "s", "balance": 10000.0, "equity": 10050.0,
            "margin": 100.0, "margin_free": 9950.0, "margin_level": 10050.0,
            "leverage": 100, "currency": "USD",
        }

    def health(self):
        if self._raise_on_action:
            raise self._raise_on_action
        return {
            "status": "ok", "account_label": "demo-1", "login": 1,
            "connected": True, "trade_allowed": True, "detail": None,
        }

    def cancel_pending_order(self, order_ticket):
        if self._raise_on_action:
            raise self._raise_on_action
        self.cancelled.append(order_ticket)
        return {
            "order_ticket": order_ticket,
            "time_utc": "2026-08-26T14:00:00+00:00", "time_ny": "2026-08-26T10:00:00-04:00",
            "retcode": 10009, "broker_comment": "cancelled by admin",
        }


@pytest.fixture
def bridge_client(client):
    """Overrides get_bridge_client on top of the base `client` fixture's
    get_db override. Yields a setter so each test injects its own fake."""
    holder = {}

    def _fake_dependency():
        return holder["fake"]

    app.dependency_overrides[trading.get_bridge_client] = _fake_dependency

    def _set(fake):
        holder["fake"] = fake
        return fake

    yield _set
    app.dependency_overrides.pop(trading.get_bridge_client, None)


def _make_position(ticket, magic):
    return {
        "ticket": ticket, "symbol": "EURUSDm", "direction": "long", "volume": 1.0,
        "open_price": 1.1000, "current_price": 1.1010, "stop_loss": 1.0990, "take_profit": 1.1020,
        "profit": 10.0, "magic": magic,
        "time_utc": "2026-08-26T12:00:00+00:00", "time_ny": "2026-08-26T08:00:00-04:00",
    }


def _make_pending_order(order_ticket, magic):
    return {
        "order_ticket": order_ticket, "symbol": "EURUSDm", "direction": "long", "volume": 1.0,
        "entry_price": 1.1000, "stop_loss": 1.0990, "take_profit": 0.0, "magic": magic,
        "time_utc": "2026-08-26T12:00:00+00:00", "time_ny": "2026-08-26T08:00:00-04:00",
    }


def test_list_positions_filters_to_current_users_magics(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_a@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    other_magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    bridge_client(FakeBridge(positions=[_make_position(111, magic), _make_position(222, other_magic)]))

    resp = client.get("/trading/positions", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticket"] == 111


def test_list_positions_requires_auth(client):
    resp = client.get("/trading/positions")
    assert resp.status_code == 401


def test_list_positions_without_bridge_configured_is_503(client, db_session):
    """No broker_credentials row at all -- exercises the REAL
    get_bridge_client (not the bridge_client fixture's override)."""
    token = _register_and_login(client, "trad_b@example.com")
    resp = client.get("/trading/positions", headers=_auth_header(token))
    assert resp.status_code == 503


def test_positions_503_when_credential_exists_but_bridge_url_unset(client, db_session):
    """Credentials saved (self-service), but no bridge worker provisioned
    for it yet -- still 503, distinct from having no credential at all."""
    token = _register_and_login(client, "trad_i@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    cred = BrokerCredential(user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=True)
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()

    resp = client.get("/trading/positions", headers=_auth_header(token))
    assert resp.status_code == 503


def test_list_positions_maps_bridge_error_to_409(client, db_session, bridge_client):
    """The bridge's GET /positions is gated behind its own orders_enabled
    kill switch -- every freshly-provisioned bridge worker starts with
    this off. Must surface as 409, not a raw 502, so the frontend can
    show a specific, friendly message instead of a raw error string."""
    token = _register_and_login(client, "trad_j@example.com")
    bridge_client(FakeBridge(raise_on_list=BridgeError("GET /positions failed: 403 Client Error: Forbidden")))

    resp = client.get("/trading/positions", headers=_auth_header(token))
    assert resp.status_code == 409


def test_list_pending_orders_maps_bridge_error_to_409(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_k@example.com")
    bridge_client(FakeBridge(raise_on_list=BridgeError("GET /orders/pending failed: 403 Client Error: Forbidden")))

    resp = client.get("/trading/pending-orders", headers=_auth_header(token))
    assert resp.status_code == 409


def test_positions_503_when_only_credential_is_inactive(client, db_session):
    token = _register_and_login(client, "trad_j@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    cred = BrokerCredential(
        user_id=user_id, broker_name="b", server="s", account_type="demo", is_active=False,
        bridge_url="http://example.invalid:8001",
    )
    cred.account_login = "1"
    cred.account_password = "p"
    db_session.add(cred)
    db_session.commit()

    resp = client.get("/trading/positions", headers=_auth_header(token))
    assert resp.status_code == 503


def test_get_account_info(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_k@example.com")
    bridge_client(FakeBridge())

    resp = client.get("/trading/account-info", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["balance"] == 10000.0


def test_get_bridge_health(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_health_a@example.com")
    bridge_client(FakeBridge())

    resp = client.get("/trading/health", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "ok"


def test_get_bridge_health_without_bridge_configured_is_503(client, db_session):
    """No broker_credentials row at all -- exercises the REAL
    get_bridge_client (not the bridge_client fixture's override)."""
    token = _register_and_login(client, "trad_health_b@example.com")
    resp = client.get("/trading/health", headers=_auth_header(token))
    assert resp.status_code == 503


def test_get_bridge_health_maps_bridge_error_to_502(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_health_c@example.com")
    bridge_client(FakeBridge(raise_on_action=BridgeError("connection refused")))

    resp = client.get("/trading/health", headers=_auth_header(token))
    assert resp.status_code == 502


def test_close_position_succeeds_for_owned_ticket_and_journals(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_c@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    fake = bridge_client(FakeBridge(positions=[_make_position(333, magic)]))

    resp = client.post("/trading/positions/333/close", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["ticket"] == 333
    assert fake.closed == [333]

    events = client.get("/events", headers=_auth_header(token)).json()
    journaled = [e for e in events if e["event_type"] == "manual_close_requested"]
    assert len(journaled) == 1
    assert journaled[0]["is_shadow"] is False, "a real manual broker action must not be marked shadow"


def test_close_position_404_when_ticket_not_owned(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_d@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    other_magic = next(_magic_counter)
    fake = bridge_client(FakeBridge(positions=[_make_position(444, other_magic)]))

    resp = client.post("/trading/positions/444/close", headers=_auth_header(token))
    assert resp.status_code == 404
    assert fake.closed == [], "must never call the bridge to close a ticket that isn't the caller's"


def test_close_position_maps_bridge_error_to_409(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_e@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    bridge_client(FakeBridge(positions=[_make_position(555, magic)], raise_on_action=BridgeError("orders_enabled is false")))

    resp = client.post("/trading/positions/555/close", headers=_auth_header(token))
    assert resp.status_code == 409


def test_close_position_tags_matching_trade_real_close_reason(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_f@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    trade = Trade(
        user_id=user_id, model="fvg", is_shadow=False, direction="long",
        entry_price=1.1, stop_price=1.09, target_price=1.12,
        entry_time_utc=now, entry_time_ny=now, risk_pct_used=0.01, equity_before=10000.0,
        real_position_ticket=666,
    )
    db_session.add(trade)
    db_session.commit()

    bridge_client(FakeBridge(positions=[_make_position(666, magic)]))

    resp = client.post("/trading/positions/666/close", headers=_auth_header(token))
    assert resp.status_code == 200

    db_session.refresh(trade)
    assert trade.real_close_reason == "manual"


def test_cancel_pending_order_succeeds_for_owned_ticket_and_journals(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_g@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    fake = bridge_client(FakeBridge(pending_orders=[_make_pending_order(777, magic)]))

    resp = client.delete("/trading/pending-orders/777", headers=_auth_header(token))
    assert resp.status_code == 200
    assert fake.cancelled == [777]

    events = client.get("/events", headers=_auth_header(token)).json()
    journaled = [e for e in events if e["event_type"] == "manual_cancel_requested"]
    assert len(journaled) == 1


def test_cancel_pending_order_404_when_not_owned(client, db_session, bridge_client):
    token = _register_and_login(client, "trad_h@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]
    magic = next(_magic_counter)
    mc = db_session.query(ModelConfig).filter_by(user_id=user_id, model_name="fvg").one()
    mc.status = "active"
    mc.magic_number = magic
    db_session.commit()

    other_magic = next(_magic_counter)
    fake = bridge_client(FakeBridge(pending_orders=[_make_pending_order(888, other_magic)]))

    resp = client.delete("/trading/pending-orders/888", headers=_auth_header(token))
    assert resp.status_code == 404
    assert fake.cancelled == []
