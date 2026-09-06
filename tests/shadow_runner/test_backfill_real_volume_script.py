"""
Tests for shadow_runner/scripts/backfill_real_volume_2026_09_06.py.

Following this project's established convention for one-off scripts
(see test_backfill_narrative_script.py's own docstring): main() itself
(real SessionLocal, real BridgeClient) is not tested directly --
apply_volume_backfill(), the actual matching logic, is extracted and
tested here with plain fixtures instead.
"""
import datetime
import uuid

from app.models import Trade
from shadow_runner.scripts.backfill_real_volume_2026_09_06 import apply_volume_backfill


def _trade(ticket, real_volume=None):
    return Trade(
        trade_id=uuid.uuid4(),
        model="fvg",
        is_shadow=False,
        direction="long",
        entry_price=1.1000,
        stop_price=1.0990,
        target_price=1.1050,
        entry_time_utc=datetime.datetime(2026, 8, 12, 9, 5, tzinfo=datetime.timezone.utc),
        entry_time_ny=datetime.datetime(2026, 8, 12, 5, 5, tzinfo=datetime.timezone.utc),
        risk_pct_used=0.01,
        equity_before=10000.0,
        setup_context={},
        real_position_ticket=ticket,
        real_volume=real_volume,
    )


def _deal(position_id, entry, volume=0.07):
    return {
        "ticket": 999, "position_id": position_id, "symbol": "EURUSDm", "magic": 900001,
        "entry": entry, "type": "buy", "volume": volume, "price": 1.1000, "profit": 0.0,
        "time_utc": datetime.datetime(2026, 8, 12, 9, 5, tzinfo=datetime.timezone.utc),
        "time_ny": datetime.datetime(2026, 8, 12, 5, 5, tzinfo=datetime.timezone.utc),
        "reason": "stop_loss",
    }


def test_matched_ticket_gets_its_entry_deals_volume():
    t = _trade(ticket=555)
    deals = [_deal(555, "in", volume=0.07), _deal(555, "out", volume=0.07)]

    updated, unmatched = apply_volume_backfill([t], deals)

    assert updated == 1
    assert unmatched == []
    assert t.real_volume == 0.07


def test_still_open_position_matches_too_even_with_no_out_deal():
    """Deliberately narrower than reconcile_deals() -- unlike that
    function, this only ever needs the entry deal, so a trade whose
    real_status is still 'open' (no close yet) still gets backfilled."""
    t = _trade(ticket=556)
    deals = [_deal(556, "in", volume=0.12)]  # no matching "out" deal at all

    updated, unmatched = apply_volume_backfill([t], deals)

    assert updated == 1
    assert t.real_volume == 0.12


def test_ticket_with_no_deals_in_the_fetched_window_is_left_untouched_not_guessed_at():
    t = _trade(ticket=557)
    deals = [_deal(999, "in")]  # some other position entirely

    updated, unmatched = apply_volume_backfill([t], deals)

    assert updated == 0
    assert unmatched == [t.trade_id]
    assert t.real_volume is None


def test_position_with_only_an_out_deal_and_no_in_deal_is_left_untouched():
    """Shouldn't happen for a real position, but must never crash or
    fabricate a volume from the wrong deal."""
    t = _trade(ticket=558)
    deals = [_deal(558, "out")]

    updated, unmatched = apply_volume_backfill([t], deals)

    assert updated == 0
    assert unmatched == [t.trade_id]


def test_multiple_trades_each_matched_independently():
    t1 = _trade(ticket=601)
    t2 = _trade(ticket=602)
    t3 = _trade(ticket=603)  # will stay unmatched
    deals = [_deal(601, "in", volume=0.05), _deal(602, "in", volume=0.5)]

    updated, unmatched = apply_volume_backfill([t1, t2, t3], deals)

    assert updated == 2
    assert unmatched == [t3.trade_id]
    assert t1.real_volume == 0.05
    assert t2.real_volume == 0.5
    assert t3.real_volume is None


def test_already_populated_real_volume_is_simply_overwritten_by_the_matched_deal():
    """apply_volume_backfill() itself doesn't need to guard against
    re-running on an already-populated row -- the caller (main()) is
    what filters to real_volume IS NULL only, so this never actually
    happens in practice, but the function must not behave surprisingly
    if it's ever called on an already-populated trade."""
    t = _trade(ticket=555, real_volume=0.01)
    deals = [_deal(555, "in", volume=0.07)]

    updated, _ = apply_volume_backfill([t], deals)

    assert updated == 1
    assert t.real_volume == 0.07
