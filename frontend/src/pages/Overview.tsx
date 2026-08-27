import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { AccountInfo, EventOut, ModelConfigOut, Position, TradeOut, UserSettingsOut } from "../api/types";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { ModelCard } from "../components/ModelCard";
import { StatTile } from "../components/StatTile";
import { summarizeTrades } from "../lib/pnl";

function fmtMoney(n: number, currency?: string) {
  return `${n.toFixed(2)}${currency ? ` ${currency}` : ""}`;
}

export function Overview() {
  const accountInfoQuery = useQuery({
    queryKey: ["account-info"],
    queryFn: () => apiClient.get<AccountInfo>("/trading/account-info"),
    retry: false,
    refetchInterval: 30_000,
  });

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiClient.get<UserSettingsOut>("/settings"),
    retry: false,
  });

  const modelConfigsQuery = useQuery({
    queryKey: ["model-configs"],
    queryFn: () => apiClient.get<ModelConfigOut[]>("/model-configs"),
  });

  const positionsQuery = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.get<Position[]>("/trading/positions"),
    retry: false,
    refetchInterval: 15_000,
  });

  // No per-model P&L endpoint exists server-side -- Trade.model and
  // ModelConfig.model_name already share the same string values, so
  // fetching once here and filtering client-side (in ModelCard) covers
  // it with zero backend changes. days_back is a generous window
  // standing in for "all-time" given the small trade volume.
  const tradesQuery = useQuery({
    queryKey: ["trades-all"],
    queryFn: () => apiClient.get<TradeOut[]>("/trades?days_back=3650&limit=1000"),
  });

  const eventsQuery = useQuery({
    queryKey: ["events", "", 24, 10],
    queryFn: () => {
      const since = new Date(Date.now() - 24 * 3600_000).toISOString();
      return apiClient.get<EventOut[]>(`/events?since=${encodeURIComponent(since)}&limit=10`);
    },
    refetchInterval: 30_000,
  });

  const noBridge = accountInfoQuery.error instanceof ApiError && accountInfoQuery.error.status === 503;
  const bridgeError = accountInfoQuery.error instanceof ApiError && accountInfoQuery.error.status === 502;
  const summary = tradesQuery.data ? summarizeTrades(tradesQuery.data) : null;

  return (
    <div>
      <div className="page-header">
        <h1>Overview</h1>
      </div>

      {settingsQuery.data?.is_paused && (
        <div className="banner banner-warning">
          Account-wide pause is active — no model will place new orders.
        </div>
      )}

      {noBridge && (
        <EmptyState
          title="Connect a broker account"
          message="No active broker account is connected yet, so there's no live account data to show."
          action={
            <Link to="/broker-credentials" className="btn btn-primary">
              Connect MT5 account
            </Link>
          }
        />
      )}
      {bridgeError && (
        <div className="banner banner-error">Broker bridge is unreachable right now — account data may be stale.</div>
      )}

      {accountInfoQuery.data && (
        <div className="stat-grid">
          <StatTile label="Balance" value={fmtMoney(accountInfoQuery.data.balance, accountInfoQuery.data.currency)} />
          <StatTile label="Equity" value={fmtMoney(accountInfoQuery.data.equity)} />
          <StatTile label="Margin free" value={fmtMoney(accountInfoQuery.data.margin_free)} />
        </div>
      )}

      {summary && (
        <div className="stat-grid">
          <StatTile label="P&L today" value={fmtMoney(summary.today)} tone={summary.today >= 0 ? "positive" : "negative"} />
          <StatTile
            label="P&L this week"
            value={fmtMoney(summary.thisWeek)}
            tone={summary.thisWeek >= 0 ? "positive" : "negative"}
          />
          <StatTile
            label="P&L all-time"
            value={fmtMoney(summary.allTime)}
            tone={summary.allTime >= 0 ? "positive" : "negative"}
          />
          <StatTile label="Win rate" value={summary.winRate === null ? "—" : `${summary.winRate.toFixed(0)}%`} />
        </div>
      )}

      <h2 className="section-title">Models</h2>
      {modelConfigsQuery.isLoading && <p>Loading...</p>}
      {modelConfigsQuery.data && modelConfigsQuery.data.length === 0 && (
        <EmptyState title="No models configured" message="Models are set up by the operator; check back once one is added." />
      )}
      {modelConfigsQuery.data && modelConfigsQuery.data.length > 0 && (
        <div className="card-grid" style={{ marginBottom: 32 }}>
          {modelConfigsQuery.data.map((mc) => (
            <ModelCard
              key={mc.config_id}
              modelConfig={mc}
              trades={tradesQuery.data ?? []}
              openPositionsCount={positionsQuery.data?.filter((p) => p.magic === mc.magic_number).length ?? 0}
            />
          ))}
        </div>
      )}

      <h2 className="section-title">Recent activity</h2>
      {eventsQuery.isLoading && <p>Loading...</p>}
      {eventsQuery.data && eventsQuery.data.length === 0 && (
        <p style={{ color: "var(--text-muted)" }}>No recent activity.</p>
      )}
      {eventsQuery.data && eventsQuery.data.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {eventsQuery.data.map((e) => (
            <li
              key={e.event_id}
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
                padding: "8px 0",
                borderBottom: "1px solid var(--border)",
                fontSize: 14,
              }}
            >
              <span style={{ color: "var(--text-muted)", minWidth: 150 }}>
                {new Date(e.timestamp).toLocaleString()}
              </span>
              <Badge variant={e.event_type === "safety_check_failed" ? "error" : e.is_shadow ? "neutral" : "active"}>
                {e.model}
              </Badge>
              <span>{e.event_type}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
