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
    if (trade.outcome) {
      closed += 1;
      if (trade.outcome === "win") wins += 1;
    } else {
      open += 1;
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
