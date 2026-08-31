import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { AdminEventOut } from "../../api/types";
import { Badge } from "../../components/Badge";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";
import { useModels } from "../../lib/useModels";

// Mirrors app/models/event.py's REAL_ACTION_EVENT_TYPES -- kept in sync
// by hand, same convention api/types.ts's own top comment already
// documents for every other backend-defined shape this frontend copies.
const REAL_ACTION_EVENT_TYPES = new Set([
  "pending_order_placed",
  "pending_order_cancelled",
  "candidate_filled",
  "target_attached",
  "order_placement_failed",
  "order_skipped_paused",
  "real_trade_closed",
  "partial_close_executed",
  "daily_loss_threshold_crossed",
  "safety_check_failed",
  "manual_close_requested",
  "manual_cancel_requested",
]);

/** Admin-only, all-users equivalent of TradeHistory.tsx's event
 * counterpart -- replaces admin_dashboard/'s "Live Event Feed" tab. */
export function AdminEventFeed() {
  const modelsQuery = useModels();
  const [modelFilter, setModelFilter] = useState("");
  const [hoursBack, setHoursBack] = useState(12);

  const eventsQuery = useQuery({
    queryKey: ["admin-events", modelFilter, hoursBack],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: "1000",
        since: new Date(Date.now() - hoursBack * 3600_000).toISOString(),
      });
      if (modelFilter) params.set("model", modelFilter);
      return apiClient.get<AdminEventOut[]>(`/admin/events?${params.toString()}`);
    },
  });

  const events = eventsQuery.data ?? [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Live event feed</h1>
      </div>

      <div className="flex gap-4 items-end mb-5 flex-wrap">
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Model</span>
          <select value={modelFilter} onChange={(e) => setModelFilter(e.target.value)}>
            <option value="">all</option>
            {(modelsQuery.data ?? []).map((m) => (
              <option key={m.model_name} value={m.model_name}>
                {m.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Hours back</span>
          <input
            type="number"
            min={1}
            max={24 * 30}
            value={hoursBack}
            onChange={(e) => setHoursBack(Number(e.target.value))}
            className="w-20"
          />
        </label>
      </div>

      {eventsQuery.isLoading && <p>Loading...</p>}
      {eventsQuery.error && <p className="text-negative">Failed to load events: {String(eventsQuery.error)}</p>}
      {!eventsQuery.isLoading && events.length === 0 && (
        <EmptyState title="No events" message="No events in this window." />
      )}
      {events.length > 0 && (
        <>
          <p className="text-text-muted text-[13px] mb-3">{events.length} events shown</p>
          <Table>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>User</th>
                <th>Model</th>
                <th>Event type</th>
                <th>Shadow</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event_id}>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td>{e.user_email}</td>
                  <td>{e.model}</td>
                  <td>
                    {e.event_type === "safety_check_failed" ? (
                      <Badge variant="error">{e.event_type}</Badge>
                    ) : REAL_ACTION_EVENT_TYPES.has(e.event_type) ? (
                      <Badge variant="active">{e.event_type}</Badge>
                    ) : (
                      e.event_type
                    )}
                  </td>
                  <td>{e.is_shadow ? "yes" : "no"}</td>
                  <td>
                    <pre className="whitespace-pre-wrap break-all text-xs m-0 max-w-[420px]">
                      {JSON.stringify(e.details)}
                    </pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </div>
  );
}
