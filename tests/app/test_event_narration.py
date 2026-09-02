"""
Tests for app.core.event_narration.narrate_event() -- one per template
that appears in a trade's chain, plus the fallback path. Field shapes
here are copied from the real emitting code (see
event_narration.py's own module docstring for exactly which files),
not invented -- if these ever drift from reality, this test file is
the place to fix both.
"""
from app.core.event_narration import narrate_event


def test_raid_detected_bull():
    text = narrate_event(
        "raid_detected",
        {"direction": "bull", "raid_level": 1.10500, "raid_bar_low": 1.10480, "bar_index": 12, "mss_reference_level": 1.10700},
    )
    assert "1.10500" in text
    assert "1.10480" in text
    assert "long" in text


def test_raid_detected_bear():
    text = narrate_event(
        "raid_detected",
        {"direction": "bear", "raid_level": 1.10700, "raid_bar_high": 1.10720, "bar_index": 12, "mss_reference_level": 1.10500},
    )
    assert "short" in text


def test_mss_confirmed():
    text = narrate_event(
        "mss_confirmed",
        {"direction": "bull", "level": 1.10700, "close": 1.10720, "raid_bar_index": 12, "mss_bar_index": 20},
    )
    assert "bullish" in text
    assert "1.10720" in text


def test_fvg_found():
    text = narrate_event(
        "fvg_found",
        {"direction": "bull", "top": 1.10750, "bottom": 1.10730, "frame_idx": 18, "mss_bar_index": 20},
    )
    assert "1.10730" in text
    assert "1.10750" in text
    assert "long" in text


def test_fvg_rejected_min_stop():
    text = narrate_event("fvg_rejected_min_stop", {"direction": "bull", "risk_pips": 2.0})
    assert "rejected" in text
    assert "2.00000" in text


def test_trade_candidate_ready():
    text = narrate_event(
        "trade_candidate_ready",
        {"direction": "bull", "entry": 1.10740, "stop": 1.10500, "raid_bar": 12, "mss_bar": 20},
    )
    assert "long" in text
    assert "1.10740" in text
    assert "1.10500" in text


def test_order_filled():
    text = narrate_event(
        "order_filled",
        {"direction": "long", "entry": 1.10740, "stop": 1.10500, "target": 1.11000, "fill_bar_index": 22},
    )
    assert "1.10740" in text


def test_candidate_filled():
    text = narrate_event(
        "candidate_filled",
        {"order_ticket": 123, "direction": "long", "fill_price": 1.10745},
    )
    assert "1.10745" in text


def test_target_attached():
    text = narrate_event("target_attached", {"ticket": 123, "target": 1.11000})
    assert "1.11000" in text


def test_trade_closed_win():
    text = narrate_event("trade_closed", {"direction": "long", "outcome": "win", "exit_price": 1.11000})
    assert "win" in text
    assert "1.11000" in text


def test_real_trade_closed():
    text = narrate_event(
        "real_trade_closed",
        {"ticket": 123, "close_price": 1.11000, "profit": 45.0, "close_reason": "take_profit"},
    )
    assert "take_profit" in text
    assert "+45.00000" in text


def test_partial_close_executed():
    text = narrate_event(
        "partial_close_executed",
        {"ticket": 123, "closed_volume": 0.5, "close_price": 1.10900, "remaining_volume": 0.5},
    )
    assert "0.50000" in text


def test_order_placement_failed():
    text = narrate_event("order_placement_failed", {"candidate_key": "x", "error": "bridge returned 503"})
    assert "bridge returned 503" in text


def test_order_skipped_paused_account():
    text = narrate_event("order_skipped_paused", {"direction": "long", "entry": 1.1, "reason": "account_paused"})
    assert "account is paused" in text


def test_order_skipped_paused_model():
    text = narrate_event("order_skipped_paused", {"direction": "long", "entry": 1.1, "reason": "model_paused"})
    assert "model is paused" in text


def test_safety_check_failed():
    text = narrate_event("safety_check_failed", {"check_name": "max_daily_loss", "error": "limit exceeded"})
    assert "max_daily_loss" in text
    assert "limit exceeded" in text


def test_duplicate_fill_closed():
    text = narrate_event("duplicate_fill_closed", {"order_ticket": 3147397683, "reason": "sibling_race_both_filled"})
    assert "duplicate" in text.lower()
    assert "closed" in text.lower()


def test_orphan_position_recovered():
    text = narrate_event(
        "orphan_position_recovered",
        {"ticket": 3147397683, "direction": "long", "target": 1.16526, "fill_price": 1.16460},
    )
    assert "3147397683" in text
    assert "1.16526" in text


def test_daily_loss_threshold_crossed():
    text = narrate_event(
        "daily_loss_threshold_crossed",
        {"realized_pnl": -500.0, "realized_loss_pct": 5.0, "max_daily_loss_pct": 3.0},
    )
    assert "5.00000" in text
    assert "3.00000" in text


def test_unknown_event_type_falls_back_gracefully():
    text = narrate_event("some_future_event_type", {"whatever": "value"})
    assert text == "Some future event type."


def test_missing_required_field_falls_back_gracefully_instead_of_raising():
    # raid_detected normally needs raid_level -- missing it must not raise.
    text = narrate_event("raid_detected", {"direction": "bull"})
    assert text == "Raid detected."
