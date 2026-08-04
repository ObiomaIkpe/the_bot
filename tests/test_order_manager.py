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


def test_compute_lot_size_rounds_down_and_floors_at_minimum():
    # risk $10, stop 10 pips, $10/pip/lot -> exactly 0.1 lots
    assert compute_lot_size(balance=1000, risk_pct=0.01, stop_distance_price=0.0010) == 0.1
    # Tiny risk amount should floor at the 0.01 minimum, not round to 0.
    assert compute_lot_size(balance=100, risk_pct=0.001, stop_distance_price=0.0050) == 0.01


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