"""
Tests for shadow_runner.order_manager.OrderManager -- specifically the
race-between-candidates and cancel-the-loser logic, since that's the
most error-prone part (easy to accidentally cancel the winner, or leave
a loser's order dangling).
"""
from shadow_runner.order_manager import (
    OrderManager,
    build_comment,
    compute_lot_size,
    compute_target,
)


class FakeBridge:
    """In-memory stand-in for shadow_runner.bridge_client.BridgeClient.
    Tracks placed/cancelled/modified orders so tests can assert on
    exactly what the manager did, without any real HTTP calls."""

    def __init__(self):
        self.placed = []       # list of dicts, as passed to place_pending_order
        self.cancelled = []    # list of tickets
        self.modified = []     # list of (ticket, take_profit)
        self.closed = []       # list of tickets, via close_position() -- sibling-race fix
        self._next_ticket = 1000
        self._pending_orders = {}   # ticket -> dict
        self._positions = {}        # ticket -> dict
        self._closed_history = {}   # ticket -> dict, see simulate_close()

        # Position-sizing test controls -- see test_compute_volume_* below.
        self.symbol_info_response = None
        self.symbol_info_should_fail = False
        self.symbol_info_call_count = 0
        self.balance_response = 1000.0
        self.balance_should_fail = False

        # Sibling-race fix test controls -- see
        # test_sibling_cancel_failure_* below. get_positions is called
        # TWICE within one check_for_fills() when a sibling-cancel fails
        # -- once by check_for_fills() itself (fill detection), once by
        # _handle_sibling_cancel_failure() (the duplicate check) -- so
        # "fail from call N onward" lets a test target just the second
        # one, distinct from failing fill-detection itself.
        self.get_positions_call_count = 0
        self.get_positions_should_fail_from_call = None
        self.close_position_should_fail = False

    def get_symbol_info(self, symbol):
        self.symbol_info_call_count += 1
        if self.symbol_info_should_fail:
            raise Exception("simulated bridge failure")
        return self.symbol_info_response

    def account_info(self):
        if self.balance_should_fail:
            raise Exception("simulated bridge failure")
        return {"balance": self.balance_response}

    def place_pending_order(self, symbol, direction, volume, entry_price, stop_loss, comment, magic):
        ticket = self._next_ticket
        self._next_ticket += 1
        order = {
            "order_ticket": ticket, "symbol": symbol, "direction": direction,
            "volume": volume, "entry_price": entry_price, "stop_loss": stop_loss,
            "take_profit": 0.0, "magic": magic,
        }
        self._pending_orders[ticket] = order
        self.placed.append(order)
        return order

    def get_pending_orders(self, magic):
        return [o for o in self._pending_orders.values() if o["magic"] == magic]

    def get_positions(self, magic):
        self.get_positions_call_count += 1
        if (
            self.get_positions_should_fail_from_call is not None
            and self.get_positions_call_count >= self.get_positions_should_fail_from_call
        ):
            raise Exception("simulated bridge failure")
        return [p for p in self._positions.values() if p["magic"] == magic]

    def cancel_pending_order(self, ticket):
        # Real broker behavior: cancelling a ticket that isn't a live
        # pending order anymore (already filled -- moved to
        # self._positions via simulate_fill -- or otherwise gone) fails.
        # Raising here (rather than the old .pop(ticket, None) no-op)
        # is what makes the sibling-race tests below meaningful.
        if ticket not in self._pending_orders:
            raise Exception(f"cancel failed: order {ticket} not found")
        self.cancelled.append(ticket)
        self._pending_orders.pop(ticket)
        return {"order_ticket": ticket, "retcode": 10009}

    def close_position(self, ticket):
        if self.close_position_should_fail:
            raise Exception("simulated bridge failure")
        self.closed.append(ticket)
        self._positions.pop(ticket, None)
        return {"ticket": ticket, "retcode": 10009}

    def modify_position(self, ticket, take_profit):
        self.modified.append((ticket, take_profit))
        if ticket in self._positions:
            self._positions[ticket]["take_profit"] = take_profit
        return {"ticket": ticket, "take_profit": take_profit, "retcode": 10009}

    def get_position_history(self, ticket):
        self.get_position_history_call_count = getattr(self, "get_position_history_call_count", 0) + 1
        if ticket in self._closed_history:
            return self._closed_history[ticket]
        return {"ticket": ticket, "is_closed": False}

    # ---- test helpers, not part of the real BridgeClient interface ----

    def simulate_fill(self, ticket, open_price):
        """Moves a ticket from pending -> positions, as if MT5 filled it."""
        order = self._pending_orders.pop(ticket)
        self._positions[ticket] = {
            "ticket": ticket, "symbol": order["symbol"], "direction": order["direction"],
            "volume": order["volume"], "open_price": open_price, "current_price": open_price,
            "stop_loss": order["stop_loss"], "take_profit": 0.0, "profit": 0.0, "magic": order["magic"],
            "time_utc": "2026-08-04T13:35:00+00:00", "time_ny": "2026-08-04T09:35:00-04:00",
        }

    def simulate_close(self, ticket, close_price, profit, close_reason="take_profit", history_ready=True):
        """Moves a ticket out of open positions, as if MT5 closed it, and
        (if history_ready) makes get_position_history() report it as
        closed -- set history_ready=False to simulate the broker-side
        history-cache-lag race check_for_close() has to handle."""
        self._positions.pop(ticket, None)
        if history_ready:
            self._closed_history[ticket] = {
                "ticket": ticket, "is_closed": True, "close_price": close_price,
                "close_time_utc": "2026-08-04T14:00:00+00:00", "close_time_ny": "2026-08-04T10:00:00-04:00",
                "profit": profit, "close_reason": close_reason,
            }



class FakeSettingsRow:
    def __init__(self, is_paused=False):
        self.is_paused = is_paused


class FakeSettingsQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class FakeSettingsDB:
    """Fake DB for OrderManager's is_paused check (get_user_paused_status).
    Defaults to not-paused, matching all pre-existing test expectations --
    only the dedicated pause tests construct one with is_paused=True."""

    def __init__(self, is_paused=False, row_exists=True):
        self._row = FakeSettingsRow(is_paused) if row_exists else None

    def query(self, model_cls):
        return FakeSettingsQuery(self._row)

    def close(self):
        pass


DEFAULT_SESSION_FACTORY = lambda: FakeSettingsDB(is_paused=False)  # noqa: E731


def make_model_config(status="active", magic=900001):
    return {"model_name": "fvg", "status": status, "risk_pct": 0.01, "magic_number": magic}


def make_candidate_event(direction="long", entry=1.1050, stop=1.1040, raid_bar=12, mss_bar=15):
    import datetime
    return {
        "event_type": "trade_candidate_ready", "timestamp": datetime.datetime(2026, 8, 4, 9, 0),
        "direction": direction, "entry": entry, "stop": stop, "raid_bar": raid_bar, "mss_bar": mss_bar,
    }


def test_shadow_model_never_places_real_orders():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="shadow"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == []


def test_disabled_model_never_places_real_orders():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="disabled"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == []


def test_active_model_places_pending_order_with_correct_comment():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12))

    assert len(bridge.placed) == 1
    placed = bridge.placed[0]
    assert placed["symbol"] == "EURUSDm"
    assert placed["direction"] == "long"
    assert placed["magic"] == 900001


def test_two_candidates_both_get_pending_orders():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))
    assert len(bridge.placed) == 2


def test_race_winner_cancels_the_loser():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="short", raid_bar=20, mss_bar=23, entry=1.1080, stop=1.1090))

    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    bridge.simulate_fill(ticket_a, open_price=1.1050)
    om.check_for_fills()

    assert bridge.cancelled == [ticket_b], "the loser (B) should be cancelled, not the winner (A)"
    assert om._winner_ticket == ticket_a
    assert om._winner_position_ticket == ticket_a


def test_sibling_race_both_fill_closes_the_duplicate_instead_of_orphaning_it():
    """The real bug, fixed 2026-09-02 (see PENDING_ITEMS.md's "Real bugs
    found 2026-09-02"): both siblings fill before the loser's cancel can
    run. The old code just logged the cancel failure and dropped the
    loser from tracking, leaving a real, untracked position with no
    take-profit to ride unmanaged until it eventually hit its stop.
    The fix must instead recognize the loser is now a real filled
    position and close it immediately."""
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=20, mss_bar=23, entry=1.1050, stop=1.1040))
    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    # Both fill -- the real race this bug came from.
    bridge.simulate_fill(ticket_a, open_price=1.1050)
    bridge.simulate_fill(ticket_b, open_price=1.1050)
    om.check_for_fills()

    assert bridge.cancelled == [], "cancel should have been attempted and failed, not succeeded"
    assert bridge.closed == [ticket_b], "the duplicate (loser) should be closed, not left running"
    assert om._winner_ticket == ticket_a, "the winner is still whichever check_for_fills saw first"

    dup_events = [e for e in received if e.get("event_type") == "duplicate_fill_closed"]
    assert len(dup_events) == 1
    assert dup_events[0]["order_ticket"] == ticket_b
    # symbol_info_response isn't configured in this test, so
    # compute_lot_size's own fallback-and-log-a-check-failure path fires
    # for unrelated reasons on every candidate here -- irrelevant noise.
    # What matters is that a successfully-handled duplicate does NOT
    # ALSO log it as a cancel/close failure.
    relevant_check_names = {
        e["check_name"] for e in received
        if e.get("event_type") == "safety_check_failed" and e.get("order_ticket") == ticket_b
    }
    assert relevant_check_names == set(), "a successfully-handled duplicate should not also emit a check failure"


def test_sibling_cancel_failure_without_a_real_fill_still_just_logs():
    """The cancel can fail for an ordinary reason too (order already
    expired/gone some other way) -- no real position exists, so this
    should behave exactly as before: a safety_check_failed event, no
    close_position call."""
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=20, mss_bar=23, entry=1.1050, stop=1.1040))
    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    bridge.simulate_fill(ticket_a, open_price=1.1050)
    bridge._pending_orders.pop(ticket_b)  # vanished some other way -- never became a position
    om.check_for_fills()

    assert bridge.closed == []
    # symbol_info_response isn't configured in this test, so
    # compute_lot_size's own fallback path also logs unrelated check
    # failures on every candidate here -- scope to ticket_b specifically.
    check_failures = [
        e for e in received if e.get("event_type") == "safety_check_failed" and e.get("order_ticket") == ticket_b
    ]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "cancel_sibling_order"


def test_sibling_race_falls_back_to_check_failure_if_positions_fetch_fails():
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=20, mss_bar=23, entry=1.1050, stop=1.1040))
    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    bridge.simulate_fill(ticket_a, open_price=1.1050)
    bridge.simulate_fill(ticket_b, open_price=1.1050)
    # Let check_for_fills()'s own fill-detection call through (call 1),
    # only fail the second get_positions() call -- the one inside
    # _handle_sibling_cancel_failure() checking for a duplicate.
    bridge.get_positions_should_fail_from_call = 2
    om.check_for_fills()

    assert bridge.closed == [], "can't even check -- must not guess, must fall back safely"
    check_failures = [
        e for e in received if e.get("event_type") == "safety_check_failed" and e.get("order_ticket") == ticket_b
    ]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "cancel_sibling_order"


def test_sibling_race_duplicate_close_failure_emits_distinct_check_name():
    """Worst case: the duplicate is correctly identified, but closing it
    also fails -- a real, known, unmanaged position a human needs to
    close by hand. Must be loud, and distinguishable from an ordinary
    cancel failure."""
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=20, mss_bar=23, entry=1.1050, stop=1.1040))
    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    bridge.simulate_fill(ticket_a, open_price=1.1050)
    bridge.simulate_fill(ticket_b, open_price=1.1050)
    bridge.close_position_should_fail = True
    om.check_for_fills()

    assert bridge.closed == []
    check_failures = [
        e for e in received if e.get("event_type") == "safety_check_failed" and e.get("order_ticket") == ticket_b
    ]
    assert len(check_failures) == 1
    assert check_failures[0]["check_name"] == "duplicate_fill_close_failed"


def test_no_third_order_placed_after_a_winner_is_decided():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    bridge.simulate_fill(bridge.placed[0]["order_ticket"], open_price=1.1050)
    om.check_for_fills()

    # A third candidate shows up later the same day -- should be ignored,
    # today's race is already over.
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=30, mss_bar=33))
    assert len(bridge.placed) == 1


def test_attach_target_computes_and_calls_modify():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    ticket = bridge.placed[0]["order_ticket"]
    bridge.simulate_fill(ticket, open_price=1.1050)
    om.check_for_fills()

    # 6 bars, oldest first, ending at the fill -- highest high is bar with high=1.1080
    bars = [
        {"high": 1.1055, "low": 1.1045},
        {"high": 1.1060, "low": 1.1048},
        {"high": 1.1080, "low": 1.1052},  # the extreme bar
        {"high": 1.1065, "low": 1.1050},
        {"high": 1.1058, "low": 1.1049},
        {"high": 1.1052, "low": 1.1047},
    ]
    om.attach_target(bars)

    assert len(bridge.modified) == 1
    modified_ticket, target = bridge.modified[0]
    assert modified_ticket == ticket
    assert target == (1.1080 + 1.1052) / 2


def test_cancel_all_at_day_end_skips_the_winner():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))
    ticket_a, ticket_b = bridge.placed[0]["order_ticket"], bridge.placed[1]["order_ticket"]

    # Neither fills today -- both still genuinely pending at day_end.
    om.cancel_all_at_day_end()
    assert set(bridge.cancelled) == {ticket_a, ticket_b}


def test_cancel_all_at_day_end_does_not_recancel_the_already_filled_winner():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    ticket = bridge.placed[0]["order_ticket"]
    bridge.simulate_fill(ticket, open_price=1.1050)
    om.check_for_fills()

    om.cancel_all_at_day_end()
    assert bridge.cancelled == [], "the winner is a real position now, not a pending order -- must not be cancelled"


def test_build_comment_format():
    assert build_comment("fvg", "long", 12) == "FVG:long-12"
    assert build_comment("ob", "short", 19) == "OB:short-19"


def realistic_symbol_info():
    """Plausible Exness EURUSDm contract spec (5-digit pricing) -- used
    only to exercise the real formula's math in tests; the actual
    values must be confirmed against a real /symbol_info call before
    trusting them for real position sizing (see
    PHASE4_BRIDGE_ORDERS.md)."""
    return {
        "contract_size": 100000.0,
        "tick_size": 0.00001,
        "tick_value": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
    }


def test_compute_lot_size_matches_hand_calculation():
    # 5-digit pricing -> 10 ticks/pip -> $10/pip/lot. Risk $10 over a
    # 10-pip stop -> exactly 0.1 lots.
    result = compute_lot_size(
        balance=1000, risk_pct=0.01, stop_distance_price=0.0010, symbol_info=realistic_symbol_info()
    )
    assert result == 0.1


def test_compute_lot_size_floors_to_real_volume_step_not_a_hardcoded_one():
    info = realistic_symbol_info()
    info["volume_step"] = 0.10  # a broker that only allows 0.1-lot increments
    # Risk $10 over 10 pips -> raw 0.1 lots exactly -> should land on the
    # 0.10 step cleanly, not silently assume 0.01 granularity.
    result = compute_lot_size(balance=1000, risk_pct=0.01, stop_distance_price=0.0010, symbol_info=info)
    assert result == 0.1

    # Risk that would raw-compute to 0.05 lots -> must floor DOWN to 0.0
    # under a 0.10 step (not up to 0.1) -- but volume_min still applies.
    info["volume_min"] = 0.10
    result2 = compute_lot_size(balance=500, risk_pct=0.01, stop_distance_price=0.0010, symbol_info=info)
    assert result2 == 0.10, "should floor to 0 lots of the 0.10 step, then floor UP to volume_min"


def test_compute_lot_size_caps_at_volume_max():
    info = realistic_symbol_info()
    info["volume_max"] = 5.0
    # Deliberately huge risk_amount that would compute to far more than 5 lots.
    result = compute_lot_size(balance=1_000_000, risk_pct=0.5, stop_distance_price=0.0005, symbol_info=info)
    assert result == 5.0


def test_compute_lot_size_rejects_zero_or_negative_stop_distance():
    try:
        compute_lot_size(balance=1000, risk_pct=0.01, stop_distance_price=0.0, symbol_info=realistic_symbol_info())
        assert False, "should have raised ValueError"
    except ValueError:
        pass


def test_compute_volume_uses_real_bridge_data_end_to_end():
    """_compute_volume() should fetch symbol_info + balance from the
    bridge and compute a REAL size, not the old hardcoded 0.01."""
    bridge = FakeBridge()
    bridge.symbol_info_response = realistic_symbol_info()
    bridge.balance_response = 1000.0
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")

    om.on_trade_candidate_ready(make_candidate_event(entry=1.1050, stop=1.1040))  # 10 pip stop

    assert len(bridge.placed) == 1
    assert bridge.placed[0]["volume"] == 0.1, "should be a real computed size, not the old 0.01 placeholder"


def test_compute_volume_falls_back_to_minimum_if_symbol_info_unavailable():
    bridge = FakeBridge()
    bridge.symbol_info_should_fail = True
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")

    om.on_trade_candidate_ready(make_candidate_event())

    assert len(bridge.placed) == 1
    assert bridge.placed[0]["volume"] == 0.01, "must fail SAFE (smallest size), never crash or place an unknown size"


def test_compute_volume_caches_symbol_info_across_multiple_candidates():
    bridge = FakeBridge()
    bridge.symbol_info_response = realistic_symbol_info()
    bridge.balance_response = 1000.0
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")

    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))

    assert bridge.symbol_info_call_count == 1, "should fetch symbol_info once and cache it, not once per candidate"


def test_compute_target_matches_trade_attempt_convention():
    bars = [
        {"high": 1.10, "low": 1.09},
        {"high": 1.12, "low": 1.10},  # extreme for a short (lowest low is elsewhere though)
        {"high": 1.08, "low": 1.05},  # lowest low
        {"high": 1.11, "low": 1.09},
        {"high": 1.10, "low": 1.08},
        {"high": 1.09, "low": 1.07},
    ]
    # long -> highest high (1.12), midpoint with its own low (1.10)
    assert compute_target(bars, "long") == (1.12 + 1.10) / 2
    # short -> lowest low (1.05), midpoint with its own high (1.08)
    assert compute_target(bars, "short") == (1.08 + 1.05) / 2


def _get_a_winner(bridge, om, entry=1.1050, stop=1.1040):
    """Shared setup for check_for_close tests: place, fill, confirm --
    returns the winning ticket."""
    om.on_trade_candidate_ready(make_candidate_event(entry=entry, stop=stop))
    ticket = bridge.placed[0]["order_ticket"]
    bridge.simulate_fill(ticket, open_price=entry)
    om.check_for_fills()
    return ticket


def test_check_for_close_returns_none_before_any_winner_exists():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    assert om.check_for_close() is None


def test_check_for_close_returns_none_while_still_open():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    _get_a_winner(bridge, om)
    assert om.check_for_close() is None  # still open in the fake bridge


def test_check_for_close_detects_a_real_close_and_emits_event():
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    ticket = _get_a_winner(bridge, om)

    bridge.simulate_close(ticket, close_price=1.1080, profit=30.0, close_reason="take_profit")
    result = om.check_for_close()

    assert result is not None
    assert result["close_price"] == 1.1080
    assert result["profit"] == 30.0
    assert result["close_reason"] == "take_profit"

    close_events = [e for e in received if e.get("event_type") == "real_trade_closed"]
    assert len(close_events) == 1
    assert close_events[0]["ticket"] == ticket


def test_check_for_close_only_reports_once():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    ticket = _get_a_winner(bridge, om)
    bridge.simulate_close(ticket, close_price=1.1080, profit=30.0)

    first = om.check_for_close()
    second = om.check_for_close()
    assert first is not None
    assert second is None, "should only report the close once, not every poll"


def test_check_for_close_handles_history_cache_lag_without_a_false_negative():
    """Position vanishes from open positions, but history hasn't caught
    up yet -- must NOT record anything, must retry on a later poll."""
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1", event_sink=received.append)
    ticket = _get_a_winner(bridge, om)

    bridge.simulate_close(ticket, close_price=1.1080, profit=30.0, history_ready=False)
    result = om.check_for_close()
    assert result is None, "must not record a close before history confirms it"
    assert not any(e.get("event_type") == "real_trade_closed" for e in received)

    # Now history catches up (simulating the next poll, after the lag resolves).
    bridge._closed_history[ticket] = {
        "ticket": ticket, "is_closed": True, "close_price": 1.1080,
        "close_time_utc": "x", "close_time_ny": "x", "profit": 30.0, "close_reason": "take_profit",
    }
    result2 = om.check_for_close()
    assert result2 is not None, "should successfully record the close once history catches up"


def test_check_for_close_fails_safe_on_bridge_error():
    class FailingBridge(FakeBridge):
        def get_positions(self, magic):
            raise Exception("simulated network failure")

    working_bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", working_bridge, DEFAULT_SESSION_FACTORY, "user1")
    _get_a_winner(working_bridge, om)  # place/fill against the working bridge

    om.bridge = FailingBridge()  # then swap in a failing one for the actual close check
    result = om.check_for_close()  # must not raise
    assert result is None


def test_get_real_outcome_returns_none_before_any_fill():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    assert om.get_real_outcome() is None


def test_get_real_outcome_has_fill_data_but_none_close_data_while_open():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    ticket = _get_a_winner(bridge, om, entry=1.1050, stop=1.1040)

    outcome = om.get_real_outcome()
    assert outcome["position_ticket"] == ticket
    assert outcome["fill_price"] == 1.1050
    assert outcome["fill_time_utc"] == "2026-08-04T13:35:00+00:00"
    assert outcome["close_price"] is None, "must not fabricate close data while still genuinely open"
    assert outcome["close_time_utc"] is None
    assert outcome["profit"] is None


def test_get_real_outcome_has_both_fill_and_close_data_once_closed():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge, DEFAULT_SESSION_FACTORY, "user1")
    ticket = _get_a_winner(bridge, om, entry=1.1050, stop=1.1040)
    bridge.simulate_close(ticket, close_price=1.1080, profit=30.0, close_reason="take_profit")
    om.check_for_close()

    outcome = om.get_real_outcome()
    assert outcome["fill_price"] == 1.1050  # fill data still present, unaffected by the close
    assert outcome["close_price"] == 1.1080
    assert outcome["profit"] == 30.0
    assert outcome["close_reason"] == "take_profit"


# ---------- Phase 4 step 4: is_paused safety rail ----------

def test_paused_user_never_places_a_real_order_even_when_active():
    bridge = FakeBridge()
    paused_factory = lambda: FakeSettingsDB(is_paused=True)  # noqa: E731
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, paused_factory, "user1")

    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == [], "must not place any real order while the account is paused"


def test_paused_check_emits_order_skipped_paused_event():
    bridge = FakeBridge()
    received = []
    paused_factory = lambda: FakeSettingsDB(is_paused=True)  # noqa: E731
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, paused_factory, "user1", event_sink=received.append)

    om.on_trade_candidate_ready(make_candidate_event())
    skip_events = [e for e in received if e.get("event_type") == "order_skipped_paused"]
    assert len(skip_events) == 1


def test_unpaused_user_places_orders_normally():
    bridge = FakeBridge()
    unpaused_factory = lambda: FakeSettingsDB(is_paused=False)  # noqa: E731
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, unpaused_factory, "user1")

    om.on_trade_candidate_ready(make_candidate_event())
    assert len(bridge.placed) == 1


def test_is_paused_is_checked_fresh_every_call_not_cached():
    """The whole point of is_paused is being able to stop trading
    immediately -- if OrderManager cached the value at construction
    time, flipping the flag mid-session would silently do nothing until
    a restart. This test proves it re-checks every time by flipping the
    underlying value BETWEEN two calls on the same OrderManager instance."""
    bridge = FakeBridge()
    state = {"paused": False}

    class TogglingFakeDB(FakeSettingsDB):
        def __init__(self):
            super().__init__(is_paused=state["paused"])

        def query(self, model_cls):
            self._row = FakeSettingsRow(state["paused"])  # re-read the live flag
            return FakeSettingsQuery(self._row)

    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: TogglingFakeDB(), "user1")

    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    assert len(bridge.placed) == 1, "should place normally while not paused"

    state["paused"] = True
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))
    assert len(bridge.placed) == 1, "should NOT place a second order once paused -- flip must take effect immediately"


def test_is_user_paused_fails_safe_not_paused_on_db_error():
    class FailingSettingsDB:
        def query(self, model_cls):
            raise Exception("simulated DB failure")
        def close(self):
            pass

    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: FailingSettingsDB(), "user1", event_sink=received.append)

    # Must not raise, and must fail toward NOT paused (proceeds to place normally) --
    # see _is_user_paused()'s own docstring for the reasoning.
    om.on_trade_candidate_ready(make_candidate_event())
    assert len(bridge.placed) == 1

    # Reliability fix: the failure itself must now be journaled, not just logged.
    # (Note: this fake bridge also has no symbol_info configured, so
    # _compute_volume's own fallback ALSO correctly fires its own
    # safety_check_failed event -- filter specifically for the one this
    # test actually cares about, is_paused_check.)
    paused_check_failures = [
        e for e in received
        if e.get("event_type") == "safety_check_failed" and e.get("check_name") == "is_paused_check"
    ]
    assert len(paused_check_failures) == 1
    assert len(bridge.placed) == 1


# ---------- per-model is_paused (decentralized pause, on top of the account-wide one) ----------

class ModelOnlyPausedDB:
    """Account-level UserSettings.is_paused is False; only the
    ModelConfig-level is_paused is True -- distinguishes the two checks,
    unlike FakeSettingsDB which answers both queries identically."""

    def query(self, model_cls):
        from app.models import ModelConfig, UserSettings
        if model_cls is UserSettings:
            return FakeSettingsQuery(FakeSettingsRow(is_paused=False))
        assert model_cls is ModelConfig
        return FakeSettingsQuery(FakeSettingsRow(is_paused=True))

    def close(self):
        pass


def test_model_paused_never_places_a_real_order_even_when_active():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: ModelOnlyPausedDB(), "user1")

    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == [], "must not place any real order while this model is paused"


def test_model_paused_check_emits_order_skipped_paused_event_with_reason():
    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: ModelOnlyPausedDB(), "user1", event_sink=received.append)

    om.on_trade_candidate_ready(make_candidate_event())
    skip_events = [e for e in received if e.get("event_type") == "order_skipped_paused"]
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "model_paused"


def test_is_model_paused_fails_safe_not_paused_on_db_error():
    class FailingSettingsDB:
        def query(self, model_cls):
            raise Exception("simulated DB failure")
        def close(self):
            pass

    bridge = FakeBridge()
    received = []
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, lambda: FailingSettingsDB(), "user1", event_sink=received.append)

    om.on_trade_candidate_ready(make_candidate_event())
    assert len(bridge.placed) == 1

    check_failures = [
        e for e in received
        if e.get("event_type") == "safety_check_failed" and e.get("check_name") == "model_is_paused_check"
    ]
    assert len(check_failures) == 1
    assert len(bridge.placed) == 1


def test_account_pause_short_circuits_before_the_model_level_check():
    """When both would be true, the account-wide check must win (it's
    checked first) -- only one skip event, tagged account_paused, not
    two."""
    bridge = FakeBridge()
    received = []
    paused_factory = lambda: FakeSettingsDB(is_paused=True)  # noqa: E731
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge, paused_factory, "user1", event_sink=received.append)

    om.on_trade_candidate_ready(make_candidate_event())
    skip_events = [e for e in received if e.get("event_type") == "order_skipped_paused"]
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "account_paused"