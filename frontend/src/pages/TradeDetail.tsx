import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { EventOut, TradeEventChainOut, TradeOut } from "../api/types";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

/** The trader-facing "why was this trade placed" story -- see
 * app.core.trade_story.build_trade_chain()'s module docstring on the
 * backend for how the chain is walked. There's no single GET
 * /trades/:id -- same as AdminTradeDetail.tsx, the trade itself is
 * found by filtering the already-fetched trade list client-side. */

const STAGE_LABELS: Record<string, string> = {
  raid_detected: "Liquidity raid",
  mss_confirmed: "Structure shift",
  fvg_found: "Fair value gap",
  fvg_rejected_min_stop: "Setup rejected",
  trade_candidate_ready: "Candidate ready",
  pending_order_placed: "Order placed",
  order_filled: "Filled",
  candidate_filled: "Filled",
  target_attached: "Target set",
  trade_closed: "Closed",
  real_trade_closed: "Closed (real)",
  partial_close_executed: "Partial close",
  order_placement_failed: "Order failed",
  order_skipped_paused: "Skipped (paused)",
  safety_check_failed: "Safety check failed",
  daily_loss_threshold_crossed: "Daily loss threshold",
};

function stageLabel(e: EventOut): string {
  return STAGE_LABELS[e.event_type] ?? e.event_type;
}

export function TradeDetail() {
  const { tradeId } = useParams<{ tradeId: string }>();

  const tradesQuery = useQuery({
    queryKey: ["trades", "all-for-detail"],
    queryFn: () => apiClient.get<TradeOut[]>("/trades?days_back=3650&limit=1000"),
  });

  const chainQuery = useQuery({
    queryKey: ["trade-event-chain", tradeId],
    queryFn: () => apiClient.get<TradeEventChainOut>(`/trades/${tradeId}/event-chain`),
    enabled: Boolean(tradeId),
  });

  const trade = tradesQuery.data?.find((t) => t.trade_id === tradeId);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <Link to="/trades" className="text-text-muted text-[13px] no-underline">
            &larr; Trade history
          </Link>
          <h1 className="mt-1 text-2xl">Trade story</h1>
        </div>
      </div>

      {tradesQuery.isLoading && <p>Loading...</p>}
      {!tradesQuery.isLoading && !trade && (
        <EmptyState title="Trade not found" message="This trade doesn't exist, or isn't yours." />
      )}

      {trade && (
        <>
          <div className="grid grid-cols-2 gap-4 mb-8">
            <Card>
              <h2 className="text-[13px] uppercase tracking-wide text-text-muted mt-0 mb-3">Outcome</h2>
              <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-text-muted">Model</dt>
                <dd className="m-0">{trade.model}</dd>
                <dt className="text-text-muted">Direction</dt>
                <dd className="m-0">{trade.direction}</dd>
                <dt className="text-text-muted">Entry</dt>
                <dd className="m-0 font-mono">{trade.entry_price}</dd>
                <dt className="text-text-muted">Stop</dt>
                <dd className="m-0 font-mono">{trade.stop_price}</dd>
                <dt className="text-text-muted">Target</dt>
                <dd className="m-0 font-mono">{trade.target_price}</dd>
                <dt className="text-text-muted">Exit</dt>
                <dd className="m-0 font-mono">{trade.exit_price ?? "-"}</dd>
                <dt className="text-text-muted">Outcome</dt>
                <dd className="m-0">{trade.outcome ?? "open"}</dd>
                <dt className="text-text-muted">Realized R</dt>
                <dd className="m-0 font-mono">{trade.realized_r ?? "-"}</dd>
              </dl>
            </Card>
            <Card>
              <h2 className="text-[13px] uppercase tracking-wide text-text-muted mt-0 mb-3">
                Real broker outcome <span className="normal-case text-text-muted">(if a real order was placed)</span>
              </h2>
              <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-text-muted">Status</dt>
                <dd className="m-0">{trade.real_status ?? "-"}</dd>
                <dt className="text-text-muted">Fill price</dt>
                <dd className="m-0 font-mono">{trade.real_fill_price ?? "-"}</dd>
                <dt className="text-text-muted">Close price</dt>
                <dd className="m-0 font-mono">{trade.real_close_price ?? "-"}</dd>
                <dt className="text-text-muted">Close reason</dt>
                <dd className="m-0">{trade.real_close_reason ?? "-"}</dd>
                <dt className="text-text-muted">Real profit</dt>
                <dd className={`m-0 font-mono ${(trade.real_profit ?? 0) >= 0 ? "text-positive" : "text-negative"}`}>
                  {trade.real_profit ?? "-"}
                </dd>
              </dl>
            </Card>
          </div>

          <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Why this trade happened</h2>

          {chainQuery.isLoading && <p>Loading...</p>}
          {chainQuery.data && chainQuery.data.chain.length === 0 && (
            <EmptyState
              title="Story not available"
              message="This trade's reasoning chain couldn't be reconstructed -- likely older data written before this page existed."
            />
          )}
          {chainQuery.data && chainQuery.data.chain.length > 0 && (
            <>
              {!chainQuery.data.fully_resolved && (
                <p className="text-text-muted text-[13px] mb-3">
                  Some earlier steps in this trade's reasoning couldn't be found -- showing what's available.
                </p>
              )}
              <Card>
                <ul className="list-none p-0 m-0">
                  {chainQuery.data.chain.map((e, i) => (
                    <li
                      key={e.event_id}
                      className={`flex gap-3 items-start py-3 text-sm ${i > 0 ? "border-t border-line" : ""}`}
                    >
                      <span className="text-text-muted min-w-[150px] shrink-0">
                        {new Date(e.timestamp).toLocaleString()}
                      </span>
                      <span className="font-semibold min-w-[130px] shrink-0">{stageLabel(e)}</span>
                      <span>{e.narrative}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  );
}
