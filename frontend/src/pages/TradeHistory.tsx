import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import type { TradeOut } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Table } from "../components/Table";
import { useModels } from "../lib/useModels";

const OUTCOMES = ["win", "loss", "scratch"];

type SortKey = "entry_time_ny" | "model" | "outcome" | "real_profit";
type SortDir = "asc" | "desc";

/** Split out of the old Dashboard.tsx: "what's happening right now"
 * (Overview) and "let me audit past trades" (here) are different tasks
 * with different UI needs, so they get separate pages instead of being
 * stacked on one. Backend sort is fixed (entry_time_ny DESC) and there's
 * no pagination beyond a hard limit -- client-side sort on the already
 * -fetched page is enough given the trade volume this account sees.
 *
 * daysBack default was 30 until 2026-09-04 -- widened to match
 * Overview.tsx's own days_back=3650: an orphan trade is written with
 * its REAL historical fill time as entry_time_ny, which can be well
 * outside any recent window by the time it's discovered (that's the
 * whole premise of an orphan). A 30-day default risked this page --
 * the one place a trader actually audits history -- silently hiding
 * exactly the kind of row this session's fixes exist to surface.
 * Still user-adjustable, just no longer hides anything by default. */
export function TradeHistory() {
  const modelsQuery = useModels();
  const [modelFilter, setModelFilter] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("");
  const [shadowFilter, setShadowFilter] = useState<"" | "true" | "false">("");
  const [daysBack, setDaysBack] = useState(3650);
  const [sortKey, setSortKey] = useState<SortKey>("entry_time_ny");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const tradesQuery = useQuery({
    queryKey: ["trades", modelFilter, outcomeFilter, shadowFilter, daysBack],
    queryFn: () => {
      const params = new URLSearchParams({ days_back: String(daysBack), limit: "1000" });
      if (modelFilter) params.set("model", modelFilter);
      if (outcomeFilter) params.set("outcome", outcomeFilter);
      if (shadowFilter) params.set("is_shadow", shadowFilter);
      return apiClient.get<TradeOut[]>(`/trades?${params.toString()}`);
    },
  });

  const sorted = useMemo(() => {
    if (!tradesQuery.data) return [];
    const rows = [...tradesQuery.data];
    rows.sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return rows;
  }, [tradesQuery.data, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function sortIndicator(key: SortKey) {
    if (sortKey !== key) return "";
    return sortDir === "asc" ? " ▲" : " ▼";
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Trade history</h1>
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
      {!tradesQuery.isLoading && sorted.length === 0 && (
        <EmptyState title="No trades" message="No trades match these filters." />
      )}
      {sorted.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th className="cursor-pointer" onClick={() => toggleSort("entry_time_ny")}>
                Entry (NY){sortIndicator("entry_time_ny")}
              </th>
              <th className="cursor-pointer" onClick={() => toggleSort("model")}>
                Model{sortIndicator("model")}
              </th>
              <th>Shadow</th>
              <th>Direction</th>
              <th>Entry</th>
              <th>Exit</th>
              <th className="cursor-pointer" onClick={() => toggleSort("outcome")}>
                Outcome{sortIndicator("outcome")}
              </th>
              <th>Real status</th>
              <th className="cursor-pointer" onClick={() => toggleSort("real_profit")}>
                Real profit{sortIndicator("real_profit")}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t) => (
              <tr key={t.trade_id}>
                <td>
                  <Link to={`/trades/${t.trade_id}`}>{new Date(t.entry_time_ny).toLocaleString()}</Link>
                </td>
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
