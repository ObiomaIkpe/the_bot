import { describe, expect, it } from "vitest";
import type { TradeOut } from "../api/types";
import { buildCumulativeSeries, resolveOutcome, summarizeTrades } from "./pnl";

/** Regression coverage for the bug a tester reported live
 * ("win rate doesn't make sense given the trades we've seen"): a real,
 * closed trade discovered after the fact (orphan recovery, or the
 * 2026-09-05 historical-reconciliation backfill) never gets the
 * SIMULATED `outcome` field set -- write_orphan_trade() and
 * write_reconciled_historical_trade() both leave it null forever, on
 * purpose, since neither ever went through the same-day simulated
 * grading step. Before this fix, summarizeTrades() treated a null
 * `outcome` as "still open" unconditionally, so these trades showed a
 * real, closed profit/loss number in the table while being counted as
 * open and excluded from the win-rate math entirely. */

function trade(overrides: Partial<TradeOut>): TradeOut {
  return {
    trade_id: "t1",
    model: "fvg",
    is_shadow: false,
    direction: "long",
    entry_price: 1.105,
    stop_price: 1.103,
    target_price: 1.109,
    exit_price: null,
    outcome: null,
    realized_r: null,
    risk_pct_used: 0.01,
    entry_time_utc: "2026-08-27T12:00:00Z",
    entry_time_ny: "2026-08-27T08:00:00-04:00",
    exit_time_utc: null,
    real_status: null,
    real_fill_price: null,
    real_close_price: null,
    real_close_reason: null,
    real_profit: null,
    ...overrides,
  };
}

describe("resolveOutcome", () => {
  it("uses the simulated outcome when it's set", () => {
    expect(resolveOutcome(trade({ outcome: "win", real_status: "closed", real_profit: -5 }))).toBe("win");
  });

  it("falls back to the real result when the simulated outcome is null but the real position is closed", () => {
    expect(resolveOutcome(trade({ outcome: null, real_status: "closed", real_profit: 42.3 }))).toBe("win");
    expect(resolveOutcome(trade({ outcome: null, real_status: "closed", real_profit: -42.3 }))).toBe("loss");
    expect(resolveOutcome(trade({ outcome: null, real_status: "closed", real_profit: 0 }))).toBe("scratch");
  });

  it("is genuinely open when neither the simulated nor the real result exists yet", () => {
    expect(resolveOutcome(trade({ outcome: null, real_status: null, real_profit: null }))).toBe("open");
  });

  it("is genuinely open when the real position is still open or partially closed", () => {
    expect(resolveOutcome(trade({ outcome: null, real_status: "open", real_profit: null }))).toBe("open");
    expect(resolveOutcome(trade({ outcome: null, real_status: "partial_closed", real_profit: 10 }))).toBe("open");
  });
});

describe("summarizeTrades", () => {
  it("counts an orphan-recovered / historically-reconciled trade as closed, not open", () => {
    const summary = summarizeTrades([
      trade({ outcome: null, real_status: "closed", real_profit: 100 }),
      trade({ outcome: null, real_status: "closed", real_profit: -50 }),
    ]);
    expect(summary.openCount).toBe(0);
    expect(summary.winRate).toBe(50);
  });

  it("matches this session's exact reported case: real closed trades with no simulated outcome were previously excluded from win rate entirely", () => {
    const summary = summarizeTrades([
      trade({ outcome: "win", real_status: "closed", real_profit: 30 }), // normal live trade
      trade({ outcome: null, real_status: "closed", real_profit: 75 }), // reconciled backfill trade, a win
      trade({ outcome: null, real_status: "closed", real_profit: -10 }), // reconciled backfill trade, a loss
    ]);
    // Before the fix: winRate was 100 (1/1) and openCount was 2 (the two
    // reconciled trades wrongly excluded). After the fix: all three
    // count, 2 wins out of 3.
    expect(summary.openCount).toBe(0);
    expect(summary.tradeCount).toBe(3);
    expect(summary.winRate).toBeCloseTo((2 / 3) * 100);
  });

  it("still treats a genuinely-still-open real trade as open, not a scratch", () => {
    const summary = summarizeTrades([trade({ outcome: null, real_status: "open", real_profit: null })]);
    expect(summary.openCount).toBe(1);
    expect(summary.winRate).toBeNull();
  });
});

describe("buildCumulativeSeries", () => {
  it("gives every trade a unique, sequential tradeNumber even when two trades share the exact same entry_time_ny", () => {
    // Exactly the 27/08/2026 sibling-order incident this was built for:
    // two real trades from one detected candidate, same simulated
    // entry timestamp, different real outcomes. A date-keyed x-axis
    // collapses or crowds these; tradeNumber never does.
    const series = buildCumulativeSeries([
      trade({ entry_time_ny: "2026-08-27T15:59:22-04:00", real_profit: 498.3 }),
      trade({ entry_time_ny: "2026-08-27T15:59:22-04:00", real_profit: -490.75 }),
    ]);
    expect(series.map((p) => p.tradeNumber)).toEqual([1, 2]);
    expect(series[0].date).toBe(series[1].date);
  });

  it("carries this trade's own profit separately from the running cumulative total", () => {
    const series = buildCumulativeSeries([
      trade({ entry_time_ny: "2026-08-01T00:00:00Z", real_profit: 100 }),
      trade({ entry_time_ny: "2026-08-02T00:00:00Z", real_profit: -30 }),
    ]);
    expect(series[0]).toMatchObject({ profit: 100, cumulative: 100 });
    expect(series[1]).toMatchObject({ profit: -30, cumulative: 70 });
  });
});
