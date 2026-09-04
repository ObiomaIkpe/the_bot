"""
2026-09-04, historical backfill (Aug 10 -> Sept 4 window), Piece A of
the historical-reconciliation plan: get_candles() is capped at 5000
bars per call, always anchored at the most recent bar -- not enough to
reach a gap wider than ~17 trading days. get_candles_paginated() pages
backward via start_pos to reach further back. No HTTP-mocking library
is used anywhere else in this test suite -- following the existing
convention, BridgeClient.get_candles itself is monkeypatched (the real
BridgeClient class, its own pagination logic exercised for real) rather
than mocking at the requests/HTTP layer.
"""
import datetime

from shadow_runner.bridge_client import BridgeClient


def _bar(minutes_ago: int) -> dict:
    t = datetime.datetime(2026, 9, 4, 12, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return {
        "time_utc": t, "time_ny": t,
        "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
        "tick_volume": 1, "spread": 1, "real_volume": 0,
    }


def test_paginated_stops_once_enough_bars_collected(monkeypatch):
    calls = []

    def fake_get_candles(self, symbol, timeframe, count, start_pos=0):
        calls.append(start_pos)
        # Page 0 (most recent) has plenty on its own -- pagination
        # should stop after this one call, never request page 1.
        return [_bar(m) for m in range(count)]

    monkeypatch.setattr(BridgeClient, "get_candles", fake_get_candles)
    client = BridgeClient("http://fake")

    result = client.get_candles_paginated("EURUSDm", "M5", total_bars_needed=100)

    assert calls == [0], "should stop after the first page once enough bars are collected"
    assert len(result) == BridgeClient.MAX_CANDLES_PER_CALL


def test_paginated_stops_when_a_page_comes_back_short(monkeypatch):
    calls = []

    def fake_get_candles(self, symbol, timeframe, count, start_pos=0):
        calls.append(start_pos)
        if start_pos == 0:
            return [_bar(m) for m in range(BridgeClient.MAX_CANDLES_PER_CALL)]
        # Terminal history exhausted -- fewer bars than requested.
        return [_bar(m) for m in range(BridgeClient.MAX_CANDLES_PER_CALL, BridgeClient.MAX_CANDLES_PER_CALL + 50)]

    monkeypatch.setattr(BridgeClient, "get_candles", fake_get_candles)
    client = BridgeClient("http://fake")

    result = client.get_candles_paginated("EURUSDm", "M5", total_bars_needed=999999)

    assert calls == [0, BridgeClient.MAX_CANDLES_PER_CALL], "should fetch exactly two pages, then stop (short page)"
    assert len(result) == BridgeClient.MAX_CANDLES_PER_CALL + 50


def test_paginated_caps_at_max_pages_even_if_never_satisfied(monkeypatch):
    calls = []

    def fake_get_candles(self, symbol, timeframe, count, start_pos=0):
        calls.append(start_pos)
        # Every page comes back full -- never short, never enough on
        # its own -- would spin forever without the MAX_PAGES cap.
        return [_bar(start_pos + m) for m in range(BridgeClient.MAX_CANDLES_PER_CALL)]

    monkeypatch.setattr(BridgeClient, "get_candles", fake_get_candles)
    client = BridgeClient("http://fake")

    result = client.get_candles_paginated("EURUSDm", "M5", total_bars_needed=999999999)

    assert len(calls) == BridgeClient.MAX_PAGES, "must stop at the safety cap, not spin indefinitely"
    assert len(result) == BridgeClient.MAX_PAGES * BridgeClient.MAX_CANDLES_PER_CALL


def test_paginated_dedupes_overlapping_bars_across_pages(monkeypatch):
    def fake_get_candles(self, symbol, timeframe, count, start_pos=0):
        if start_pos == 0:
            # Bars 0-4999 (most recent).
            return [_bar(m) for m in range(5000)]
        # Deliberately overlaps the previous page by 10 bars (4990-4999)
        # to prove de-duplication, then continues further back, short
        # (terminal history exhausted) to stop after this page.
        return [_bar(m) for m in range(4990, 5040)]

    monkeypatch.setattr(BridgeClient, "get_candles", fake_get_candles)
    client = BridgeClient("http://fake")

    result = client.get_candles_paginated("EURUSDm", "M5", total_bars_needed=999999)

    times = [b["time_utc"] for b in result]
    assert len(times) == len(set(times)), "no duplicate bars across overlapping pages"
    assert len(result) == 5040, "overlap must be collapsed, not double-counted"


def test_paginated_result_is_sorted_oldest_to_newest(monkeypatch):
    def fake_get_candles(self, symbol, timeframe, count, start_pos=0):
        if start_pos == 0:
            return [_bar(m) for m in range(5000)]
        return [_bar(m) for m in range(5000, 5100)]  # short page -> stop here

    monkeypatch.setattr(BridgeClient, "get_candles", fake_get_candles)
    client = BridgeClient("http://fake")

    result = client.get_candles_paginated("EURUSDm", "M5", total_bars_needed=999999)

    times = [b["time_utc"] for b in result]
    assert times == sorted(times), "result must be chronological oldest-first regardless of page fetch order"
