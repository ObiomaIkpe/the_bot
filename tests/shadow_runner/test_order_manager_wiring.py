"""
Integration test for Phase 4 step 2c's wiring itself -- not just
OrderManager in isolation (test_order_manager.py already covers that
thoroughly), but proving a real trade_candidate_ready event emitted by
DayOrchestrator during actual runner._process_bar() flow genuinely
reaches a live OrderManager and results in a real (fake-bridge) pending
order call. This is the piece most likely to have a silent wiring bug
(e.g. combined_sink never actually calling an order manager, or
cd.order_managers being empty when it shouldn't be).

Multi-user fan-out, piece 2: rewritten to seed a real subscriber via
db_session (User + BrokerCredential + ModelConfig) instead of hand-
setting runner.model_config directly -- _decide_day() now discovers
subscribers via get_active_subscribers(), which needs real rows to
join across, not a fake DB that can't replicate that join. bridge_factory
is injected to hand back the SAME fake bridge regardless of URL, so the
subscriber's own bridge_url string never needs to resolve to anything
real.
"""
import datetime

from app.models import BrokerCredential, ModelConfig, User
from shadow_runner.runner import ShadowRunner
from tests.shadow_runner.test_runner_orchestration import make_config, full_day_bars, establish_trend
from tests.streaming.test_trade_candidate_ready import FakeWatch, FakeFVGDetector
from tests.shadow_runner.test_order_manager import FakeBridge as OrderFakeBridge


class CombinedFakeBridge(OrderFakeBridge):
    """test_order_manager's FakeBridge already tracks placed/cancelled/
    modified orders -- extend it with the read-only methods runner.py
    also needs (get_candles for the bootstrap/replay paths, though not
    exercised directly by this test since we drive _process_bar by
    hand)."""

    def get_candles(self, symbol, timeframe, count):
        return []  # not exercised in this test -- see module docstring

    def account_info(self):
        return {"balance": 50000.0}


def _make_subscriber(db_session, email, magic_number=900001, risk_pct=0.01, bridge_url="http://sub-bridge:9001"):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    mc = ModelConfig(
        user_id=user.user_id, model_name="fvg", status="active",
        risk_pct=risk_pct, magic_number=magic_number,
    )
    bc = BrokerCredential(
        user_id=user.user_id, broker_name="forex.com", server="FOREXcom-Demo",
        account_type="demo", is_active=True, bridge_url=bridge_url,
    )
    bc.account_login = "12345"
    bc.account_password = "secret"
    db_session.add_all([mc, bc])
    db_session.commit()
    return user


def test_trade_candidate_ready_reaches_a_real_order_manager_and_places_an_order(db_session):
    config = make_config()
    subscriber = _make_subscriber(db_session, "wiring_sub@example.com")
    # Captured now, as a plain value -- _process_bar() below triggers
    # multiple db.close() cycles on this same shared session (once per
    # _decide_day()/_write_events_now() call), which detaches `subscriber`
    # and would raise DetachedInstanceError on a later .user_id access.
    subscriber_id = subscriber.user_id
    bridge = CombinedFakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,  # same fake regardless of the subscriber's own bridge_url
    )

    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)
    bars_before_10am = [b for b in bars if b["time_ny"].time() < ten_am]
    bars_from_10am_on = [b for b in bars if b["time_ny"].time() >= ten_am]

    for b in bars_before_10am:
        runner._process_bar(b)
    runner._process_bar(bars_from_10am_on[0])  # triggers _decide_day -> constructs OrderManagers

    assert set(runner.current_day.order_managers.keys()) == {subscriber_id}
    om = runner.current_day.order_managers[subscriber_id]
    assert om is not None, "OrderManager should have been constructed for the seeded subscriber"
    assert om.model_config["status"] == "active"
    assert om.model_config["magic_number"] == 900001

    # Inject a fake candidate directly into the real, live DayOrchestrator
    # -- same technique as test_trade_candidate_ready.py, reused here at
    # the runner level to trigger a REAL trade_candidate_ready event
    # through the runner's actual combined_sink wiring, not a hand-built one.
    orch = runner.current_day.orchestrator
    # fire_bar must be the index the NEXT bar fed will receive (i.e.
    # len(cd.bars) right now) -- bars_from_10am_on[0] was already
    # processed above (that's what triggered _decide_day), so its index
    # is already in the past; the fake watch needs to fire on the bar
    # we're about to feed next.
    fire_bar = len(runner.current_day.bars)
    frame_idx = fire_bar - 2

    # Multi-user fan-out, piece 2: these now flow through a REAL
    # db_session (not the old FakeDB), so `timestamp` must be a real
    # datetime -- the placeholder string "t" this test used to use never
    # got validated by an in-memory fake, but a real Postgres
    # `timestamp with time zone` column rejects it outright.
    fire_ts = bars_from_10am_on[1]["time_ny"]
    fvg_e = {"event_type": "fvg_found", "timestamp": fire_ts, "top": 1.1060, "bottom": 1.1050, "frame_idx": frame_idx}
    mss_e = {"event_type": "mss_confirmed", "timestamp": fire_ts, "direction": "bull", "mss_bar_index": fire_bar - 1}
    orch._fvg_det = FakeFVGDetector(fvg_e)
    watch = FakeWatch(fire_on_bar_index=fire_bar, mss_e=mss_e)
    orch._candidates = [{"raid_bar": fire_bar - 3, "watch": watch}]

    # Feed one more bar to actually trigger the fake watch's on_new_bar
    # call, which -> emits trade_candidate_ready -> combined_sink ->
    # order_manager.on_trade_candidate_ready() -> bridge.place_pending_order().
    next_bar = bars_from_10am_on[1]
    runner._process_bar(next_bar)

    assert len(bridge.placed) == 1, "the real trade_candidate_ready event should have reached OrderManager and placed a real order"
    placed = bridge.placed[0]
    assert placed["magic"] == 900001
    assert placed["direction"] == "long"


def test_a_second_user_with_a_disabled_model_never_gets_an_order_manager(db_session):
    """The other half of the wiring proof: someone with the model NOT
    active must never end up in cd.order_managers at all -- proving
    get_active_subscribers()'s status filter is actually respected by
    _decide_day(), not just by the query in isolation (already covered
    by tests/shadow_runner/test_get_active_subscribers.py)."""
    config = make_config()
    active_subscriber = _make_subscriber(db_session, "wiring_active@example.com", magic_number=900101)
    active_subscriber_id = active_subscriber.user_id  # captured now -- see the other test's comment
    disabled_user = User(email="wiring_disabled@example.com", password_hash="x")
    db_session.add(disabled_user)
    db_session.commit()
    db_session.refresh(disabled_user)
    mc = ModelConfig(
        user_id=disabled_user.user_id, model_name="fvg", status="disabled",
        risk_pct=0.01, magic_number=900102,
    )
    bc = BrokerCredential(
        user_id=disabled_user.user_id, broker_name="forex.com", server="FOREXcom-Demo",
        account_type="demo", is_active=True, bridge_url="http://disabled-sub-bridge:9001",
    )
    bc.account_login = "99999"
    bc.account_password = "secret"
    db_session.add_all([mc, bc])
    db_session.commit()

    bridge = CombinedFakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,
    )

    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)
    bars_before_10am = [b for b in bars if b["time_ny"].time() < ten_am]
    bars_from_10am_on = [b for b in bars if b["time_ny"].time() >= ten_am]

    for b in bars_before_10am:
        runner._process_bar(b)
    runner._process_bar(bars_from_10am_on[0])

    assert set(runner.current_day.order_managers.keys()) == {active_subscriber_id}
