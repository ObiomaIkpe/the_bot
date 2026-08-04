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
        self._next_ticket = 1000
        self._pending_orders = {}   # ticket -> dict
        self._positions = {}        # ticket -> dict

        # Position-sizing test controls -- see test_compute_volume_* below.
        self.symbol_info_response = None
        self.symbol_info_should_fail = False
        self.symbol_info_call_count = 0
        self.balance_response = 1000.0
        self.balance_should_fail = False

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
        return [p for p in self._positions.values() if p["magic"] == magic]

    def cancel_pending_order(self, ticket):
        self.cancelled.append(ticket)
        self._pending_orders.pop(ticket, None)
        return {"order_ticket": ticket, "retcode": 10009}

    def modify_position(self, ticket, take_profit):
        self.modified.append((ticket, take_profit))
        if ticket in self._positions:
            self._positions[ticket]["take_profit"] = take_profit
        return {"ticket": ticket, "take_profit": take_profit, "retcode": 10009}

    # ---- test helpers, not part of the real BridgeClient interface ----

    def simulate_fill(self, ticket, open_price):
        """Moves a ticket from pending -> positions, as if MT5 filled it."""
        order = self._pending_orders.pop(ticket)
        self._positions[ticket] = {
            "ticket": ticket, "symbol": order["symbol"], "direction": order["direction"],
            "volume": order["volume"], "open_price": open_price, "current_price": open_price,
            "stop_loss": order["stop_loss"], "take_profit": 0.0, "profit": 0.0, "magic": order["magic"],
        }


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
    om = OrderManager(make_model_config(status="shadow"), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == []


def test_disabled_model_never_places_real_orders():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="disabled"), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event())
    assert bridge.placed == []


def test_active_model_places_pending_order_with_correct_comment():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12))

    assert len(bridge.placed) == 1
    placed = bridge.placed[0]
    assert placed["symbol"] == "EURUSDm"
    assert placed["direction"] == "long"
    assert placed["magic"] == 900001


def test_two_candidates_both_get_pending_orders():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))
    assert len(bridge.placed) == 2


def test_race_winner_cancels_the_loser():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event(direction="long", raid_bar=12, mss_bar=15, entry=1.1050, stop=1.1040))
    om.on_trade_candidate_ready(make_candidate_event(direction="short", raid_bar=20, mss_bar=23, entry=1.1080, stop=1.1090))

    ticket_a = bridge.placed[0]["order_ticket"]
    ticket_b = bridge.placed[1]["order_ticket"]

    bridge.simulate_fill(ticket_a, open_price=1.1050)
    om.check_for_fills()

    assert bridge.cancelled == [ticket_b], "the loser (B) should be cancelled, not the winner (A)"
    assert om._winner_ticket == ticket_a
    assert om._winner_position_ticket == ticket_a


def test_no_third_order_placed_after_a_winner_is_decided():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    bridge.simulate_fill(bridge.placed[0]["order_ticket"], open_price=1.1050)
    om.check_for_fills()

    # A third candidate shows up later the same day -- should be ignored,
    # today's race is already over.
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=30, mss_bar=33))
    assert len(bridge.placed) == 1


def test_attach_target_computes_and_calls_modify():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
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
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=12, mss_bar=15))
    om.on_trade_candidate_ready(make_candidate_event(raid_bar=20, mss_bar=23))
    ticket_a, ticket_b = bridge.placed[0]["order_ticket"], bridge.placed[1]["order_ticket"]

    # Neither fills today -- both still genuinely pending at day_end.
    om.cancel_all_at_day_end()
    assert set(bridge.cancelled) == {ticket_a, ticket_b}


def test_cancel_all_at_day_end_does_not_recancel_the_already_filled_winner():
    bridge = FakeBridge()
    om = OrderManager(make_model_config(), "EURUSDm", bridge)
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
        "trade_contract_size": 100000.0,
        "trade_tick_size": 0.00001,
        "trade_tick_value": 1.0,
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
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge)

    om.on_trade_candidate_ready(make_candidate_event(entry=1.1050, stop=1.1040))  # 10 pip stop

    assert len(bridge.placed) == 1
    assert bridge.placed[0]["volume"] == 0.1, "should be a real computed size, not the old 0.01 placeholder"


def test_compute_volume_falls_back_to_minimum_if_symbol_info_unavailable():
    bridge = FakeBridge()
    bridge.symbol_info_should_fail = True
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge)

    om.on_trade_candidate_ready(make_candidate_event())

    assert len(bridge.placed) == 1
    assert bridge.placed[0]["volume"] == 0.01, "must fail SAFE (smallest size), never crash or place an unknown size"


def test_compute_volume_caches_symbol_info_across_multiple_candidates():
    bridge = FakeBridge()
    bridge.symbol_info_response = realistic_symbol_info()
    bridge.balance_response = 1000.0
    om = OrderManager(make_model_config(status="active"), "EURUSDm", bridge)

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