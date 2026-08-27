import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { ModelConfigOut, ModelStatus, TradeOut } from "../api/types";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { StatTile } from "../components/StatTile";
import { Table } from "../components/Table";
import { modelStatusBadgeVariant } from "../lib/modelStatus";
import { summarizeTrades } from "../lib/pnl";

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

  return (
    <div>
      <div className="page-header">
        <div>
          <Link to="/models" style={{ color: "var(--text-muted)", fontSize: 13, textDecoration: "none" }}>
            &larr; Models
          </Link>
          <h1 style={{ marginTop: 4 }}>{modelName}</h1>
        </div>
        {modelConfig && <Badge variant={modelStatusBadgeVariant(modelConfig.status)}>{modelConfig.status}</Badge>}
      </div>

      {modelConfig && (
        <div className="card" style={{ marginBottom: 24, display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
          <label className="field" style={{ margin: 0 }}>
            <span>Status</span>
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
          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
          <span style={{ color: "var(--text-muted)", fontSize: 14 }}>
            Risk {(modelConfig.risk_pct * 100).toFixed(1)}% · Magic {modelConfig.magic_number}
          </span>
        </div>
      )}

      {summary && (
        <div className="stat-grid">
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

      <h2 className="section-title">Trade history</h2>
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
                <td style={{ fontFamily: "var(--font-mono)" }}>{t.entry_price}</td>
                <td style={{ fontFamily: "var(--font-mono)" }}>{t.exit_price ?? "-"}</td>
                <td>{t.outcome ?? "open"}</td>
                <td
                  style={{
                    fontFamily: "var(--font-mono)",
                    color: (t.real_profit ?? 0) >= 0 ? "var(--positive)" : "var(--negative)",
                  }}
                >
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
