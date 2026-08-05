"""
Tests for Phase 4 step 4 Part 2: daily loss VISIBILITY (confirmed
design -- no enforcement, no blocking, no force-closing; purely a
journaled signal). The most important test here is proving it's
genuinely non-enforcing: on_trade_candidate_ready() must still place a
real order even after the threshold has been crossed.
"""
import datetime

from shadow_runner.order_manager import OrderManager
from shadow_runner.persistence import get_realized_pnl_today
from tests.test_order_manager import FakeBridge, make_candidate_event, make_model_config


class FakeSettingsRow:
    def __init__(self, max_daily_loss_pct):
        self.max_daily_loss_pct = max_daily_loss_pct


class FakeTradeRow:
    def __init__(self, real_profit, real_close_time_ny):
        self.real_profit = real_profit
        self.real_close_time_ny = real_close_time_ny


class FakeQuery:
    def __init__(self, result_first=None, result_all=None):
        self._first = result_first
        self._all = result_all or []

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class FakeLossCheckDB:
    def __init__(self, max_daily_loss_pct=0.05, trade_rows=None, settings_row_exists=True):
        from app.models import UserSettings, Trade
        self._UserSettings = UserSettings
        self._Trade = Trade
        self._settings_row = FakeSettingsRow(max_daily_loss_pct) if settings_row_exists else None
        self._trade_rows = trade_rows or []

    def query(self, model_cls):
        if model_cls is self._UserSettings:
            return FakeQuery(result_first=self._settings_row)
        if model_cls is self._Trade:
            return FakeQuery(result_all=self._trade_rows)
        raise AssertionError(f"unexpected query for {model_cls}")

    def close(self):
        pass


class BalanceBridge(FakeBridge):
    def __init__(self, balance=1000.0):
        super().__init__()
        self._balance = balance

    def account_info(self):
        return {"balance": self._balance}


def today_ny():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.datetime.now(ZoneInfo("America/New_York")).date()


# ---------- get_realized_pnl_today ----------

def test_get_realized_pnl_today_nets_wins_and_losses():
    today = today_ny()
    yesterday = today - datetime.timedelta(days=1)
    rows = [
        FakeTradeRow(real_profit=-50.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0))),
        FakeTradeRow(real_profit=20.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(11, 0))),
        FakeTradeRow(real_profit=-1000.0, real_close_time_ny=datetime.datetime.combine(yesterday, datetime.time(9, 0))),  # different day -- must be excluded
    ]
    db = FakeLossCheckDB(trade_rows=rows)
    total = get_realized_pnl_today(db, "user1", "fvg", today)
    assert total == -30.0, "should net -50 + 20 = -30, excluding yesterday's -1000"


def test_get_realized_pnl_today_empty_when_nothing_closed_today():
    today = today_ny()
    db = FakeLossCheckDB(trade_rows=[])
    total = get_realized_pnl_today(db, "user1", "fvg", today)
    assert total == 0.0


# ---------- OrderManager.check_daily_loss_threshold ----------

def test_not_crossed_when_loss_below_threshold():
    today = today_ny()
    rows = [FakeTradeRow(real_profit=-10.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0)))]
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=rows)  # 5% of 1000 = $50; only lost $10
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", BalanceBridge(1000.0), lambda: db, "user1", event_sink=received.append)

    om.check_daily_loss_threshold()
    assert not any(e.get("event_type") == "daily_loss_threshold_crossed" for e in received)


def test_crossed_emits_event_with_correct_fields():
    today = today_ny()
    rows = [FakeTradeRow(real_profit=-80.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0)))]
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=rows)  # lost $80 of $1000 = 8%, over the 5% limit
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", BalanceBridge(1000.0), lambda: db, "user1", event_sink=received.append)

    om.check_daily_loss_threshold()
    events = [e for e in received if e.get("event_type") == "daily_loss_threshold_crossed"]
    assert len(events) == 1
    assert events[0]["realized_pnl"] == -80.0
    assert abs(events[0]["realized_loss_pct"] - 0.08) < 1e-9
    assert events[0]["max_daily_loss_pct"] == 0.05


def test_crossing_the_threshold_does_not_block_new_real_orders():
    """THE most important test in this file -- confirms this is
    genuinely visibility-only, per the confirmed design. A candidate
    arriving AFTER the threshold has already been crossed must still
    result in a real order being placed."""
    today = today_ny()
    rows = [FakeTradeRow(real_profit=-200.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0)))]
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=rows)  # way over the limit (20% loss)
    bridge = BalanceBridge(1000.0)
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: db, "user1")

    om.check_daily_loss_threshold()  # threshold crossed, event emitted internally
    om.on_trade_candidate_ready(make_candidate_event())  # a NEW candidate arrives afterward

    assert len(bridge.placed) == 1, "must still place a real order -- this check never blocks anything"


def test_only_emits_once_per_day():
    today = today_ny()
    rows = [FakeTradeRow(real_profit=-200.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0)))]
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=rows)
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", BalanceBridge(1000.0), lambda: db, "user1", event_sink=received.append)

    om.check_daily_loss_threshold()
    om.check_daily_loss_threshold()
    om.check_daily_loss_threshold()

    events = [e for e in received if e.get("event_type") == "daily_loss_threshold_crossed"]
    assert len(events) == 1, "must only emit once per day, even across many poll cycles"


def test_shadow_model_never_checked():
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=[])
    received = []
    om = OrderManager(make_model_config(status="shadow"), "EURUSDm", BalanceBridge(1000.0), lambda: db, "user1", event_sink=received.append)
    om.check_daily_loss_threshold()
    assert received == []


def test_returns_early_if_no_user_settings_row():
    db = FakeLossCheckDB(settings_row_exists=False)
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", BalanceBridge(1000.0), lambda: db, "user1", event_sink=received.append)
    om.check_daily_loss_threshold()  # must not raise
    assert received == []


def test_fails_safe_on_db_error():
    class FailingDB:
        def query(self, model_cls):
            raise Exception("simulated DB failure")
        def close(self):
            pass

    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", BalanceBridge(1000.0), lambda: FailingDB(), "user1", event_sink=received.append)
    om.check_daily_loss_threshold()  # must not raise

    # No daily_loss_threshold_crossed event (correct -- the check never
    # completed) -- but the failure itself IS now journaled (the
    # reliability fix this file's own conversation led to: a silent
    # DB error used to be invisible except in container logs).
    assert not any(e.get("event_type") == "daily_loss_threshold_crossed" for e in received)
    failure_events = [e for e in received if e.get("event_type") == "safety_check_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["check_name"] == "daily_loss_threshold_db"


def test_fails_safe_on_bridge_balance_error():
    class FailingBalanceBridge(BalanceBridge):
        def account_info(self):
            raise Exception("simulated bridge failure")

    today = today_ny()
    rows = [FakeTradeRow(real_profit=-200.0, real_close_time_ny=datetime.datetime.combine(today, datetime.time(9, 0)))]
    db = FakeLossCheckDB(max_daily_loss_pct=0.05, trade_rows=rows)
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", FailingBalanceBridge(), lambda: db, "user1", event_sink=received.append)
    om.check_daily_loss_threshold()  # must not raise

    assert not any(e.get("event_type") == "daily_loss_threshold_crossed" for e in received)
    failure_events = [e for e in received if e.get("event_type") == "safety_check_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["check_name"] == "daily_loss_threshold_balance_fetch"