import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../../api/client";
import type { AuditLogActorType, AuditLogOut } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";

const ACTOR_TYPES: AuditLogActorType[] = ["user", "machine", "credential", "unknown"];

// Mirrors app/models/audit_log.py's VALID_AUDIT_EVENT_TYPES -- kept in
// sync by hand, same convention as REAL_ACTION_EVENT_TYPES in
// AdminEventFeed.tsx.
const EVENT_TYPES = [
  "user_registered",
  "login_succeeded",
  "login_failed",
  "login_rejected_inactive",
  "password_changed",
  "broker_credential_created",
  "broker_credential_updated",
  "broker_credential_removed",
  "broker_credential_decommission_requested",
  "bridge_token_issued",
  "bridge_credentials_fetched",
  "bridge_credentials_fetch_denied",
  "provisioning_job_claimed",
  "provisioning_job_completed",
  "provisioning_job_failed",
  "decommission_job_claimed",
  "decommission_job_completed",
  "decommission_job_failed",
];

/** Admin-only equivalent of admin_dashboard/'s "Audit log" tab --
 * security/identity events (auth, broker credential lifecycle,
 * provisioning/decommission job transitions, the plaintext-credential
 * fetch), NOT the trading pipeline's own events (see AdminEventFeed for
 * those). A single-select event-type filter here, not Streamlit's
 * multiselect -- a reasonable simplification matching this frontend's
 * existing lightweight native-<select> idiom. */
export function AdminAuditLog() {
  const [actorTypeFilter, setActorTypeFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [hoursBack, setHoursBack] = useState(24 * 7);

  const auditQuery = useQuery({
    queryKey: ["admin-audit-log", actorTypeFilter, eventTypeFilter, hoursBack],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: "1000",
        since: new Date(Date.now() - hoursBack * 3600_000).toISOString(),
      });
      if (actorTypeFilter) params.set("actor_type", actorTypeFilter);
      if (eventTypeFilter) params.set("event_type", eventTypeFilter);
      return apiClient.get<AuditLogOut[]>(`/admin/audit-log?${params.toString()}`);
    },
  });

  const rows = auditQuery.data ?? [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Security / identity audit log</h1>
      </div>
      <p className="text-text-muted text-[13px] mb-5 max-w-[640px]">
        Auth, broker credential lifecycle, and provisioning/decommission job transitions -- not the trading pipeline's
        own events (see Live event feed for those).
      </p>

      <div className="flex gap-4 items-end mb-5 flex-wrap">
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Actor type</span>
          <select value={actorTypeFilter} onChange={(e) => setActorTypeFilter(e.target.value)}>
            <option value="">all</option>
            {ACTOR_TYPES.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Event type</span>
          <select value={eventTypeFilter} onChange={(e) => setEventTypeFilter(e.target.value)}>
            <option value="">all</option>
            {EVENT_TYPES.map((et) => (
              <option key={et} value={et}>
                {et}
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

      {auditQuery.isLoading && <p>Loading...</p>}
      {auditQuery.error && <p className="text-negative">Failed to load audit log: {String(auditQuery.error)}</p>}
      {!auditQuery.isLoading && rows.length === 0 && (
        <EmptyState title="No audit log rows" message="No audit log rows in this window." />
      )}
      {rows.length > 0 && (
        <>
          <p className="text-text-muted text-[13px] mb-3">{rows.length} rows shown</p>
          <Table>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Event type</th>
                <th>Actor type</th>
                <th>Actor</th>
                <th>Resource</th>
                <th>IP</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.audit_id}>
                  <td>{new Date(r.timestamp).toLocaleString()}</td>
                  <td>{r.event_type}</td>
                  <td>{r.actor_type}</td>
                  <td>{r.actor_label ?? "-"}</td>
                  <td>{r.resource_type ? `${r.resource_type}: ${r.resource_id}` : "-"}</td>
                  <td>{r.ip_address ?? "-"}</td>
                  <td>
                    <pre className="whitespace-pre-wrap break-all text-xs m-0 max-w-[360px]">
                      {JSON.stringify(r.details)}
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
