import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { ModelConfigOut, ModelStatus, TradeOut } from "../api/types";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { PnlChart } from "../components/PnlChart";
import { StatTile } from "../components/StatTile";
import { Table } from "../components/Table";
import { modelStatusBadgeVariant } from "../lib/modelStatus";
import { buildCumulativeSeries, summarizeTrades } from "../lib/pnl";

const STATUSES: ModelStatus[] = ["disabled", "shadow", "active"];

export function ModelDetail() {
  const { modelName } = useParams<{ modelName: string }>();
  const queryClient = useQueryClient();

  const modelConfigsQuery = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => apiClient.get<ModelConfigOut[]>("/model-configs"),
  });

  const tradesQuery = useQuery({
    queryKey: ["trades", modelName],
    queryFn: () => apiClient.get<TradeOut[]>(`/trades?model=${modelName}&days_back=3650&limit=1000`),
    enabled: Boolean(modelName),
  });

  const patchModelConfig = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<Pick<ModelConfigOut, "status" | "is_paused">> }) =>
      apiClient.patch(`/model-configs/${id}`, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["model-configs"] }),
  });

  const modelConfig = modelConfigsQuery.data?.find((mc) => mc.model_name === modelName);
  const summary = tradesQuery.data ? summarizeTrades(tradesQuery.data) : null;
  const series = tradesQuery.data ? buildCumulativeSeries(tradesQuery.data) : [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <Link to="/models" className="text-text-muted text-[13px] no-underline">
            &larr; Models
          </Link>
          <h1 className="mt-1 text-2xl">{modelName}</h1>
        </div>
        {modelConfig && <Badge variant={modelStatusBadgeVariant(modelConfig.status)}>{modelConfig.status}</Badge>}
      </div>

      {modelConfig && (
        <div className="bg-bg-elevated border border-line rounded-lg p-5 mb-6 flex gap-6 items-center flex-wrap">
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-text-muted">Status</span>
            <select
              value={modelConfig.status}
              disabled={patchModelConfig.isPending}
              onChange={(e) =>
                patchModelConfig.mutate({ id: modelConfig.config_id, changes: { status: e.target.value as ModelStatus } })
              }
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={modelConfig.is_paused}
              disabled={patchModelConfig.isPending}
              onChange={(e) =>
                patchModelConfig.mutate({ id: modelConfig.config_id, changes: { is_paused: e.target.checked } })
              }
            />
            Paused
          </label>
          <span className="text-text-muted text-sm">
            Risk {(modelConfig.risk_pct * 100).toFixed(1)}% · Magic {modelConfig.magic_number}
          </span>
        </div>
      )}

      {summary && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-6">
          <StatTile
            label="Total P&L"
            value={summary.allTime.toFixed(2)}
            tone={summary.allTime >= 0 ? "positive" : "negative"}
          />
          <StatTile label="Win rate" value={summary.winRate === null ? "—" : `${summary.winRate.toFixed(0)}%`} />
          <StatTile label="Trades" value={String(summary.tradeCount)} />
          <StatTile label="Open" value={String(summary.openCount)} />
        </div>
      )}

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">P&L over time</h2>
      <div className="mb-8">
        <PnlChart data={series} />
      </div>

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Trade history</h2>
      {tradesQuery.isLoading && <p>Loading...</p>}
      {tradesQuery.data && tradesQuery.data.length === 0 && (
        <EmptyState title="No trades yet" message="This model hasn't logged any trades yet." />
      )}
      {tradesQuery.data && tradesQuery.data.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Entry (NY)</th>
              <th>Direction</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Outcome</th>
              <th>Real profit</th>
            </tr>
          </thead>
          <tbody>
            {tradesQuery.data.map((t) => (
              <tr key={t.trade_id}>
                <td>{new Date(t.entry_time_ny).toLocaleString()}</td>
                <td>{t.direction}</td>
                <td className="font-mono">{t.entry_price}</td>
                <td className="font-mono">{t.exit_price ?? "-"}</td>
                <td>{t.outcome ?? "open"}</td>
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
