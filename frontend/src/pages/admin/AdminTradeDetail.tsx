import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { AdminEventChainOut, AdminTradeOut } from "../../api/types";
import { Card } from "../../components/Card";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";

/** Ports admin_dashboard/'s Trades tab drill-down. There's no single
 * GET /admin/trades/:id -- the trade itself is found by filtering the
 * all-trades list client-side, since the backend only exposes the list
 * + a dedicated event-chain endpoint (see app/routers/admin.py). */
export function AdminTradeDetail() {
  const { tradeId } = useParams<{ tradeId: string }>();

  const tradesQuery = useQuery({
    queryKey: ["admin-trades", "all-for-detail"],
    queryFn: () => apiClient.get<AdminTradeOut[]>("/admin/trades?days_back=3650&limit=1000"),
  });

  const chainQuery = useQuery({
    queryKey: ["admin-trade-event-chain", tradeId],
    queryFn: () => apiClient.get<AdminEventChainOut>(`/admin/trades/${tradeId}/event-chain`),
    enabled: Boolean(tradeId),
  });

  const trade = tradesQuery.data?.find((t) => t.trade_id === tradeId);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <Link to="/admin/trades" className="text-text-muted text-[13px] no-underline">
            &larr; Trades
          </Link>
          <h1 className="mt-1 text-2xl">Trade detail</h1>
        </div>
      </div>

      {tradesQuery.isLoading && <p>Loading...</p>}
      {!tradesQuery.isLoading && !trade && (
        <EmptyState title="Trade not found" message="This trade doesn't exist, or has been filtered out." />
      )}

      {trade && (
        <>
          <div className="grid grid-cols-2 gap-4 mb-8">
            <Card>
              <h2 className="text-[13px] uppercase tracking-wide text-text-muted mt-0 mb-3">Simulated outcome</h2>
              <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-text-muted">User</dt>
                <dd className="m-0">{trade.user_email}</dd>
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
                Real broker outcome <span className="normal-case text-text-muted">(null if shadow / never filled)</span>
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

          <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">
            Full event chain for {new Date(trade.entry_time_ny).toLocaleDateString()} ({trade.model})
          </h2>
          <p className="text-text-muted text-[13px] mb-3">
            Rows marked below are the specific fill/close events matched to this trade (by direction + price -- there's
            no direct foreign key).
          </p>

          {chainQuery.isLoading && <p>Loading event chain...</p>}
          {chainQuery.data && chainQuery.data.day_events.length === 0 && (
            <EmptyState title="No events" message="No events found for this trade's day." />
          )}
          {chainQuery.data && chainQuery.data.day_events.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Event type</th>
                  <th>Match</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {chainQuery.data.day_events.map((e) => {
                  const isFill = e.event_id === chainQuery.data?.matched_fill_event_id;
                  const isClose = e.event_id === chainQuery.data?.matched_close_event_id;
                  return (
                    <tr key={e.event_id} className={isFill || isClose ? "bg-accent/5" : undefined}>
                      <td>{new Date(e.timestamp).toLocaleString()}</td>
                      <td>{e.event_type}</td>
                      <td>{isFill ? "➡️ this trade's fill" : isClose ? "➡️ this trade's close" : ""}</td>
                      <td>
                        <pre className="whitespace-pre-wrap break-all text-xs m-0 max-w-[420px]">
                          {JSON.stringify(e.details)}
                        </pre>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </>
      )}
    </div>
  );
}
