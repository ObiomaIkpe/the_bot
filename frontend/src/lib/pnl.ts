import type { TradeOut } from "../api/types";

/** Client-side trade aggregation -- there is no backend endpoint for
 * this (confirmed: GET /trades returns a flat, unaggregated list). The
 * dataset is small enough (per earlier diagnostics, roughly 1 trade per
 * 1.7 weeks) that computing this over an already-fetched trade list is
 * fine; this is not meant to scale to a high-frequency account. */
export interface PnlSummary {
  today: number;
  thisWeek: number;
  allTime: number;
  /** 0-100, or null if there are no closed (outcome-having) trades yet. */
  winRate: number | null;
  tradeCount: number;
  openCount: number;
}

function profitOf(trade: TradeOut): number {
  return trade.real_profit ?? 0;
}

/** `outcome` is the SIMULATED same-day verdict (win/loss/scratch),
 * fixed at day-finalize time by the detection pipeline -- see
 * shadow_runner/persistence.py's write_trade(). It is only ever null
 * for a trade that was genuinely still open when this ran, EXCEPT for
 * two classes discovered after the fact, which deliberately never go
 * through that simulated grading step at all and so leave `outcome`
 * null forever, even once fully resolved for real:
 *   - an orphaned position, found unmanaged and healed
 *     (write_orphan_trade())
 *   - a historical-reconciliation backfill row (write_reconciled_
 *     historical_trade(), 2026-09-05)
 * Both still carry a definitive REAL result the moment
 * `real_status === "closed"`, which this app never checked before --
 * this was reported live by a tester ("win rate doesn't make sense
 * given the trades we've seen") because both classes were showing a
 * real, closed profit/loss number next to an "open" label, and were
 * silently excluded from the win-rate math entirely. Prefer the real
 * result whenever the simulated one is missing, and only fall back to
 * "open" when there is truly no result of either kind yet. */
export type ResolvedOutcome = "win" | "loss" | "scratch" | "open";

export function resolveOutcome(trade: TradeOut): ResolvedOutcome {
  if (trade.outcome) return trade.outcome as ResolvedOutcome;
  if (trade.real_status === "closed") {
    const profit = trade.real_profit ?? 0;
    if (profit > 0) return "win";
    if (profit < 0) return "loss";
    return "scratch";
  }
  return "open";
}

export function summarizeTrades(trades: TradeOut[]): PnlSummary {
  const now = new Date();
  const todayKey = now.toDateString();
  const weekAgo = now.getTime() - 7 * 24 * 3600_000;

  let today = 0;
  let thisWeek = 0;
  let allTime = 0;
  let wins = 0;
  let closed = 0;
  let open = 0;

  for (const trade of trades) {
    // entry_time_ny is already NY wall-clock time from the backend, so
    // comparing local Date components against it avoids UTC-boundary
    // bugs around midnight.
    const entry = new Date(trade.entry_time_ny);
    const profit = profitOf(trade);
    allTime += profit;
    if (entry.toDateString() === todayKey) today += profit;
    if (entry.getTime() >= weekAgo) thisWeek += profit;
    const resolved = resolveOutcome(trade);
    if (resolved === "open") {
      open += 1;
    } else {
      closed += 1;
      if (resolved === "win") wins += 1;
    }
  }

  return {
    today,
    thisWeek,
    allTime,
    winRate: closed > 0 ? (wins / closed) * 100 : null,
    tradeCount: trades.length,
    openCount: open,
  };
}

export interface PnlPoint {
  date: string;
  cumulative: number;
}

/** Running-sum P&L over time, for PnlChart. Trades are sorted ascending
 * (the /trades API always returns them newest-first) before summing. */
export function buildCumulativeSeries(trades: TradeOut[]): PnlPoint[] {
  const sorted = [...trades].sort(
    (a, b) => new Date(a.entry_time_ny).getTime() - new Date(b.entry_time_ny).getTime(),
  );

  let running = 0;
  return sorted.map((trade) => {
    running += profitOf(trade);
    return { date: trade.entry_time_ny, cumulative: running };
  });
}
