"""
Multi-user fan-out, piece 2 -- the gap found and folded in mid-build
(see MULTI_USER_FANOUT_PLAN.md's build notes): PositionTracker widened
from a single process-wide instance to one per subscriber
(self.position_trackers), built at startup (so a restart doesn't lose
track of an already-open multi-day position) and topped up at every
_decide_day() (so a brand-new subscriber gets overnight risk coverage
without needing a full runner restart).
"""
from app.models import BrokerCredential, ModelConfig, User
from shadow_runner.position_tracker import PositionTracker
from shadow_runner.runner import ShadowRunner
from tests.shadow_runner.test_order_manager import FakeBridge
from tests.shadow_runner.test_order_manager_wiring import CombinedFakeBridge, make_config


def _make_subscriber(db_session, email, magic_number, bridge_url="http://sub-bridge:9001"):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    mc = ModelConfig(
        user_id=user.user_id, model_name="fvg", status="active",
        risk_pct=0.01, magic_number=magic_number,
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


def test_load_initial_position_trackers_builds_one_per_subscriber(db_session):
    config = make_config()
    sub1 = _make_subscriber(db_session, "pt_load_1@example.com", 900301)
    sub2 = _make_subscriber(db_session, "pt_load_2@example.com", 900302)
    sub1_id, sub2_id = sub1.user_id, sub2.user_id

    bridge = FakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,
    )

    runner._load_initial_position_trackers()

    assert set(runner.position_trackers.keys()) == {sub1_id, sub2_id}
    for pt in runner.position_trackers.values():
        assert isinstance(pt, PositionTracker)
    assert runner.position_trackers[sub1_id].model_config["magic_number"] == 900301
    assert runner.position_trackers[sub2_id].model_config["magic_number"] == 900302


def test_ensure_position_tracker_never_replaces_an_existing_entry(db_session):
    """Proves in-flight multi-day tracking state survives being "topped
    up" again -- _ensure_position_tracker() must be a true no-op for a
    subscriber who's already tracked, not a silent reconstruction."""
    config = make_config()
    sub = _make_subscriber(db_session, "pt_noop@example.com", 900303)
    sub_id = sub.user_id

    bridge = FakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,
    )
    runner._ensure_position_tracker({
        "user_id": sub_id, "bridge_url": "http://sub-bridge:9001",
        "magic_number": 900303, "risk_pct": 0.01,
    })
    first_instance = runner.position_trackers[sub_id]
    first_instance._tracked[12345] = {"trade_id": "fake", "entry_time_ny": None, "partial_closed": False}

    runner._ensure_position_tracker({
        "user_id": sub_id, "bridge_url": "http://sub-bridge:9001",
        "magic_number": 900303, "risk_pct": 0.01,
    })

    assert runner.position_trackers[sub_id] is first_instance
    assert 12345 in runner.position_trackers[sub_id]._tracked


def test_decide_day_tops_up_position_trackers_for_a_newly_added_subscriber(db_session):
    """A subscriber who didn't exist at startup still gets overnight risk
    coverage the moment they're picked up by a day's decide-point --
    no full runner restart required."""
    import datetime
    config = make_config()
    bridge = CombinedFakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,
    )
    assert runner.position_trackers == {}  # nobody subscribed at startup

    sub = _make_subscriber(db_session, "pt_topup@example.com", 900304)
    sub_id = sub.user_id

    from tests.shadow_runner.test_runner_orchestration import establish_trend, full_day_bars
    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)
    for b in [b for b in bars if b["time_ny"].time() < ten_am]:
        runner._process_bar(b)
    runner._process_bar([b for b in bars if b["time_ny"].time() >= ten_am][0])  # triggers _decide_day

    assert sub_id in runner.position_trackers


def test_poll_once_isolates_one_subscribers_check_positions_failure(db_session, monkeypatch):
    """One subscriber's PositionTracker blowing up must never stop
    check_positions() from running for anyone else this poll."""
    config = make_config()
    bridge = FakeBridge()
    runner = ShadowRunner(
        config, bridge=bridge, session_factory=lambda: db_session,
        bridge_factory=lambda url: bridge,
    )

    class ExplodingPositionTracker:
        def check_positions(self):
            raise RuntimeError("simulated bridge outage")

    calls = []

    class RecordingPositionTracker:
        def check_positions(self):
            calls.append("checked")

    runner.position_trackers["broken-user"] = ExplodingPositionTracker()
    runner.position_trackers["healthy-user"] = RecordingPositionTracker()

    # Isolate poll_once() from the rest of its own body (bar-fetching,
    # order-manager checks) -- this test is specifically about the
    # position-tracker loop's isolation, not the whole poll cycle.
    monkeypatch.setattr(runner, "_filter_new_closed_bars", lambda candles, now_ny: [])
    monkeypatch.setattr(runner, "_check_order_manager_fills", lambda: None)
    monkeypatch.setattr(runner, "_check_order_manager_close", lambda: None)
    monkeypatch.setattr(runner, "_check_daily_loss_threshold", lambda: None)
    monkeypatch.setattr(bridge, "get_candles", lambda *a, **k: [], raising=False)

    runner.poll_once()  # must not raise, despite the exploding tracker

    assert calls == ["checked"]
