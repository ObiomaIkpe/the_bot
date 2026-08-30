import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "../../api/client";
import type { AdminTradeOut } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { Table } from "../../components/Table";

const MODELS = ["fvg", "ob", "fvg_ob"];
const OUTCOMES = ["win", "loss", "scratch"];

/** Admin-only, all-users equivalent of TradeHistory.tsx -- replaces
 * admin_dashboard/'s "Trades" tab. No user filter (admin sees
 * everyone); each row links to the event-chain drill-down. */
export function AdminTrades() {
  const [modelFilter, setModelFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("");
  const [shadowFilter, setShadowFilter] = useState<"" | "true" | "false">("");
  const [daysBack, setDaysBack] = useState(30);

  const tradesQuery = useQuery({
    queryKey: ["admin-trades", modelFilter, outcomeFilter, shadowFilter, daysBack],
    queryFn: () => {
      const params = new URLSearchParams({ days_back: String(daysBack), limit: "1000" });
      if (modelFilter) params.set("model", modelFilter);
      if (outcomeFilter) params.set("outcome", outcomeFilter);
      if (shadowFilter) params.set("is_shadow", shadowFilter);
      return apiClient.get<AdminTradeOut[]>(`/admin/trades?${params.toString()}`);
    },
  });

  const trades = tradesQuery.data ?? [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Trades (all users)</h1>
      </div>

      <div className="flex gap-4 items-end mb-5 flex-wrap">
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Model</span>
          <select value={modelFilter} onChange={(e) => setModelFilter(e.target.value)}>
            <option value="">all</option>
            {MODELS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Outcome</span>
          <select value={outcomeFilter} onChange={(e) => setOutcomeFilter(e.target.value)}>
            <option value="">all</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Shadow</span>
          <select value={shadowFilter} onChange={(e) => setShadowFilter(e.target.value as "" | "true" | "false")}>
            <option value="">all</option>
            <option value="true">shadow only</option>
            <option value="false">real only</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[13px] text-text-muted">Days back</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={daysBack}
            onChange={(e) => setDaysBack(Number(e.target.value))}
            className="w-20"
          />
        </label>
      </div>

      {tradesQuery.isLoading && <p>Loading...</p>}
      {tradesQuery.error && <p className="text-negative">Failed to load trades: {String(tradesQuery.error)}</p>}
      {!tradesQuery.isLoading && trades.length === 0 && (
        <EmptyState title="No trades" message="No trades match these filters." />
      )}
      {trades.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Entry (NY)</th>
              <th>User</th>
              <th>Model</th>
              <th>Shadow</th>
              <th>Direction</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Outcome</th>
              <th>Real status</th>
              <th>Real profit</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.trade_id}>
                <td>
                  <Link to={`/admin/trades/${t.trade_id}`} className="text-accent no-underline hover:underline">
                    {new Date(t.entry_time_ny).toLocaleString()}
                  </Link>
                </td>
                <td>{t.user_email}</td>
                <td>{t.model}</td>
                <td>{t.is_shadow ? "yes" : "no"}</td>
                <td>{t.direction}</td>
                <td className="font-mono">{t.entry_price}</td>
                <td className="font-mono">{t.exit_price ?? "-"}</td>
                <td>{t.outcome ?? "open"}</td>
                <td>{t.real_status ?? "-"}</td>
                <td className={`font-mono ${(t.real_profit ?? 0) >= 0 ? "text-positive" : "text-negative"}`}>
                  {t.real_profit ?? "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
