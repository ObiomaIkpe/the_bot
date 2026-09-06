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

/** Same exact gap as resolveOutcome() above, reported live by a tester
 * ("missing exit prices"): the Exit column only ever read `exit_price`
 * (the SIMULATED close), which is null for the same two classes of
 * trade discovered after the fact -- an orphan-recovered position, or
 * a historical-reconciliation backfill row. Both carry a real,
 * definitive close price in `real_close_price` the moment
 * `real_status === "closed"`; prefer it the same way. */
export function resolveExitPrice(trade: TradeOut): number | null {
  if (trade.exit_price != null) return trade.exit_price;
  if (trade.real_status === "closed" && trade.real_close_price != null) return trade.real_close_price;
  return null;
}

/** Mirrors resolveExitPrice() -- `exit_time_utc` is the SIMULATED
 * close time (set once, at day-finalize, for a trade that went
 * through the normal detection pipeline); `real_close_time_ny` is the
 * real broker close time, only ever populated for a trade with an
 * actual real order. Reported live: no column showed when a trade
 * actually closed at all. Prefer the real time when it exists --
 * it's the more meaningful of the two for a trade the user actually
 * held real money in -- falling back to the simulated time only for a
 * trade that never had a real component (e.g. a pure shadow trade). */
export function resolveCloseTime(trade: TradeOut): string | null {
  return trade.real_close_time_ny ?? trade.exit_time_utc ?? null;
}

/** Reported live: "real status of very first trade isn't visible."
 * That trade predates the real account (06/08/2026, cutover was
 * 08/28), so a blank real_status is technically correct: it never had
 * a real order to have a status. The bug is a bare "-" not saying
 * that -- indistinguishable from "this should have a real status and
 * doesn't."
 *
 * First attempt at this fix checked `is_shadow`, which turned out to
 * be wrong: verified live against this exact trade, `is_shadow` is
 * False for it -- it only reflects whether the model's config was
 * 'active' at decision time (write_trade()'s own is_shadow param
 * doc), NOT whether a broker actually existed to place a real order.
 * The model was already configured active before the real account
 * existed, so is_shadow=False here despite no real order ever being
 * possible. Base the label on actual real-order data instead, which
 * can't be fooled by that config/infrastructure gap. */
export function resolveRealStatusLabel(trade: TradeOut): string {
  if (trade.real_status) return trade.real_status;
  if (trade.real_fill_price == null && trade.real_close_price == null) return "no real order";
  return "-";
}

/** Same gap as the other resolve* helpers above, found live on the
 * trade-story detail page: `realized_r` is the SIMULATED R-multiple
 * (compute_realized_r() in shadow_runner/persistence.py, needs a
 * simulated exit_price), so it's null forever for an orphan-recovered
 * or historically-reconciled trade -- same two classes as everywhere
 * else in this file. Unlike exit price/close time, there's no
 * `real_realized_r` column to fall back to; it's derived instead from
 * fields that DO exist on every real, closed trade:
 *   R = real_profit / (equity_before * risk_pct_used)
 * -- the same relationship write_trade() uses in reverse to compute
 * equity_after from a simulated realized_r. equity_before is always
 * set (NOT NULL on Trade), so this only needs real_status/real_profit
 * to be present to produce a number. */
export function resolveRealizedR(trade: TradeOut): number | null {
  if (trade.realized_r != null) return trade.realized_r;
  if (trade.real_status === "closed" && trade.real_profit != null) {
    const riskAmount = trade.equity_before * trade.risk_pct_used;
    if (riskAmount === 0) return null;
    return trade.real_profit / riskAmount;
  }
  return null;
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
  /** 1-based position in chronological order -- the chart's x-axis
   * category. Two trades can share the exact same `date` (the same
   * detected candidate, fanned out or reconciled into more than one
   * real row -- see the 27/08/2026 sibling-order incident this was
   * built to display clearly) and a raw-date axis collapses or
   * visually crowds them with no way to tell them apart. A plain
   * sequence number is always unique regardless of how many trades
   * land on one calendar day; the real date/time lives in the
   * tooltip instead. */
  tradeNumber: number;
  /** This one trade's own real profit/loss -- distinct from
   * `cumulative` below. Needed so the chart can show each trade's own
   * win/loss, not just the blended running total (a losing trade is
   * still present in the cumulative line, but only as a dip -- easy
   * to read as "it went down for some other reason" rather than "this
   * specific trade lost"). */
  profit: number;
  cumulative: number;
  direction: string;
  outcome: ResolvedOutcome;
}

/** Running-sum P&L over time, for PnlChart. Trades are sorted ascending
 * (the /trades API always returns them newest-first) before summing. */
export function buildCumulativeSeries(trades: TradeOut[]): PnlPoint[] {
  const sorted = [...trades].sort(
    (a, b) => new Date(a.entry_time_ny).getTime() - new Date(b.entry_time_ny).getTime(),
  );

  let running = 0;
  return sorted.map((trade, index) => {
    const profit = profitOf(trade);
    running += profit;
    return {
      date: trade.entry_time_ny,
      tradeNumber: index + 1,
      profit,
      cumulative: running,
      direction: trade.direction,
      outcome: resolveOutcome(trade),
    };
  });
}
