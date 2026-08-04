"""
Integration test for Phase 4 step 2c's wiring itself -- not just
OrderManager in isolation (test_order_manager.py already covers that
thoroughly), but proving a real trade_candidate_ready event emitted by
DayOrchestrator during actual runner._process_bar() flow genuinely
reaches a live OrderManager and results in a real (fake-bridge) pending
order call. This is the piece most likely to have a silent wiring bug
(e.g. combined_sink never actually calling order_manager, or
cd.order_manager being None when it shouldn't be).
"""
import datetime

from shadow_runner.runner import ShadowRunner
from tests.test_runner_orchestration import make_config, full_day_bars, establish_trend, FakeDB
from tests.test_trade_candidate_ready import FakeWatch, FakeFVGDetector
from tests.test_order_manager import FakeBridge as OrderFakeBridge


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


def test_trade_candidate_ready_reaches_a_real_order_manager_and_places_an_order():
    config = make_config()
    db = FakeDB([])
    bridge = CombinedFakeBridge()
    runner = ShadowRunner(config, bridge=bridge, session_factory=lambda: db)

    # Bypass the DB entirely for model_config -- this test is about the
    # runner-level WIRING, not persistence.get_model_config() (already
    # covered elsewhere). Directly set what _load_model_config() would
    # have set.
    runner.model_config = {
        "model_name": "fvg", "status": "active", "risk_pct": 0.01, "magic_number": 900001,
    }

    next_date = establish_trend(runner.gate)
    bars = full_day_bars(next_date)
    ten_am = datetime.time(10, 0)
    bars_before_10am = [b for b in bars if b["time_ny"].time() < ten_am]
    bars_from_10am_on = [b for b in bars if b["time_ny"].time() >= ten_am]

    for b in bars_before_10am:
        runner._process_bar(b)
    runner._process_bar(bars_from_10am_on[0])  # triggers _decide_day -> constructs OrderManager

    assert runner.current_day.order_manager is not None, "OrderManager should have been constructed"
    assert runner.current_day.order_manager.model_config["status"] == "active"

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

    fvg_e = {"event_type": "fvg_found", "timestamp": "t", "top": 1.1060, "bottom": 1.1050, "frame_idx": frame_idx}
    mss_e = {"event_type": "mss_confirmed", "timestamp": "t", "direction": "bull", "mss_bar_index": fire_bar - 1}
    orch._fvg_det = FakeFVGDetector(fvg_e)
    watch = FakeWatch(fire_on_bar_index=fire_bar, mss_e=mss_e)
    orch._candidates = [{"raid_bar": fire_bar - 3, "watch": watch}]

    # Feed one more bar to actually trigger the fake watch's on_new_bar
    # call, which -> emits trade_candidate_ready -> combined_sink ->
    # order_manager.on_trade_candidate_ready() -> bridge.place_pending_order()
    next_bar = bars_from_10am_on[1]
    runner._process_bar(next_bar)

    assert len(bridge.placed) == 1, "the real trade_candidate_ready event should have reached OrderManager and placed a real order"
    placed = bridge.placed[0]
    assert placed["magic"] == 900001
    assert placed["direction"] == "long"