import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { EventOut, TradeOut } from "../api/types";

const MODELS = ["fvg", "ob", "fvg_ob"];

const thStyle: React.CSSProperties = { textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "6px 10px", borderBottom: "1px solid #eee" };

export function Dashboard() {
  const [modelFilter, setModelFilter] = useState<string>("");
  const [hoursBack, setHoursBack] = useState(24);

  const eventsQuery = useQuery({
    queryKey: ["events", modelFilter, hoursBack],
    queryFn: () => {
      const since = new Date(Date.now() - hoursBack * 3600_000).toISOString();
      const params = new URLSearchParams({ since, limit: "200" });
      if (modelFilter) params.set("model", modelFilter);
      return apiClient.get<EventOut[]>(`/events?${params.toString()}`);
    },
    refetchInterval: 30_000, // replaces admin_dashboard's Streamlit meta-refresh with real polling
  });

  const tradesQuery = useQuery({
    queryKey: ["trades", modelFilter],
    queryFn: () => {
      const params = new URLSearchParams({ days_back: "30" });
      if (modelFilter) params.set("model", modelFilter);
      return apiClient.get<TradeOut[]>(`/trades?${params.toString()}`);
    },
  });

  return (
    <div>
      <h1>Dashboard</h1>

      <div style={{ marginBottom: 16, display: "flex", gap: 16, alignItems: "center" }}>
        <label>
          Model:{" "}
          <select value={modelFilter} onChange={(e) => setModelFilter(e.target.value)}>
            <option value="">all</option>
            {MODELS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label>
          Hours back:{" "}
          <input
            type="number"
            min={1}
            max={168}
            value={hoursBack}
            onChange={(e) => setHoursBack(Number(e.target.value))}
            style={{ width: 60 }}
          />
        </label>
      </div>

      <h2>Recent events</h2>
      {eventsQuery.isLoading && <p>Loading...</p>}
      {eventsQuery.error && <p style={{ color: "crimson" }}>Failed to load events: {String(eventsQuery.error)}</p>}
      {eventsQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%", marginBottom: 32 }}>
          <thead>
            <tr>
              <th style={thStyle}>Time</th>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Event</th>
              <th style={thStyle}>Shadow</th>
              <th style={thStyle}>Details</th>
            </tr>
          </thead>
          <tbody>
            {eventsQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={5}>
                  No events in this window.
                </td>
              </tr>
            )}
            {eventsQuery.data.map((e) => (
              <tr
                key={e.event_id}
                style={
                  e.event_type === "safety_check_failed"
                    ? { background: "#fde8e8" }
                    : !e.is_shadow
                      ? { background: "#e8f5e9" }
                      : undefined
                }
              >
                <td style={tdStyle}>{new Date(e.timestamp).toLocaleString()}</td>
                <td style={tdStyle}>{e.model}</td>
                <td style={tdStyle}>{e.event_type}</td>
                <td style={tdStyle}>{e.is_shadow ? "yes" : "no"}</td>
                <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 12 }}>
                  {JSON.stringify(e.details)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Trades (last 30 days)</h2>
      {tradesQuery.isLoading && <p>Loading...</p>}
      {tradesQuery.error && <p style={{ color: "crimson" }}>Failed to load trades: {String(tradesQuery.error)}</p>}
      {tradesQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th style={thStyle}>Entry (NY)</th>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>Shadow</th>
              <th style={thStyle}>Direction</th>
              <th style={thStyle}>Entry</th>
              <th style={thStyle}>Exit</th>
              <th style={thStyle}>Outcome</th>
              <th style={thStyle}>Real status</th>
              <th style={thStyle}>Real profit</th>
            </tr>
          </thead>
          <tbody>
            {tradesQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={9}>
                  No trades match these filters.
                </td>
              </tr>
            )}
            {tradesQuery.data.map((t) => (
              <tr key={t.trade_id}>
                <td style={tdStyle}>{new Date(t.entry_time_ny).toLocaleString()}</td>
                <td style={tdStyle}>{t.model}</td>
                <td style={tdStyle}>{t.is_shadow ? "yes" : "no"}</td>
                <td style={tdStyle}>{t.direction}</td>
                <td style={tdStyle}>{t.entry_price}</td>
                <td style={tdStyle}>{t.exit_price ?? "-"}</td>
                <td style={tdStyle}>{t.outcome ?? "open"}</td>
                <td style={tdStyle}>{t.real_status ?? "-"}</td>
                <td style={tdStyle}>{t.real_profit ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
