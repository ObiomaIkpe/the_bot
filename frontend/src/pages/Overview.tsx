import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { AccountInfo, BridgeHealth, EventOut, ModelConfigOut, Position, TradeOut, UserSettingsOut } from "../api/types";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { ModelCard } from "../components/ModelCard";
import { PnlChart } from "../components/PnlChart";
import { StatTile } from "../components/StatTile";
import { buttonClasses } from "../lib/buttonStyles";
import { buildCumulativeSeries, summarizeTrades } from "../lib/pnl";

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

  // Distinguishes three states the old account-info-only inference
  // couldn't: not configured (503), bridge unreachable (502), and
  // reachable-but-MT5-disconnected (200, connected: false).
  const bridgeHealthQuery = useQuery({
    queryKey: ["bridge-health"],
    queryFn: () => apiClient.get<BridgeHealth>("/trading/health"),
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

  const notConfigured = bridgeHealthQuery.error instanceof ApiError && bridgeHealthQuery.error.status === 503;
  const bridgeUnreachable = bridgeHealthQuery.error instanceof ApiError && bridgeHealthQuery.error.status === 502;
  const mt5Disconnected = bridgeHealthQuery.data && !bridgeHealthQuery.data.connected;
  const summary = tradesQuery.data ? summarizeTrades(tradesQuery.data) : null;
  const series = tradesQuery.data ? buildCumulativeSeries(tradesQuery.data) : [];

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Overview</h1>
      </div>

      {settingsQuery.data?.is_paused && (
        <div className="px-4 py-3 rounded-lg mb-5 text-sm bg-warning/10 border border-warning/30 text-warning">
          Account-wide pause is active — no model will place new orders.
        </div>
      )}

      {notConfigured && (
        <EmptyState
          title="Connect a broker account"
          message="No active broker account is connected yet, so there's no live account data to show."
          action={
            <Link to="/broker-credentials" className={buttonClasses("primary")}>
              Connect MT5 account
            </Link>
          }
        />
      )}
      {bridgeUnreachable && (
        <div className="px-4 py-3 rounded-lg mb-5 text-sm bg-negative/10 border border-negative/30 text-negative">
          Broker bridge is unreachable right now — account data may be stale.
        </div>
      )}
      {mt5Disconnected && (
        <div className="px-4 py-3 rounded-lg mb-5 text-sm bg-warning/10 border border-warning/30 text-warning">
          The bridge is reachable, but its MT5 terminal isn't currently connected
          {bridgeHealthQuery.data?.detail ? ` — ${bridgeHealthQuery.data.detail}` : "."}
        </div>
      )}

      {accountInfoQuery.data && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-6">
          <StatTile label="Balance" value={fmtMoney(accountInfoQuery.data.balance, accountInfoQuery.data.currency)} />
          <StatTile label="Equity" value={fmtMoney(accountInfoQuery.data.equity)} />
          <StatTile label="Margin free" value={fmtMoney(accountInfoQuery.data.margin_free)} />
        </div>
      )}

      {summary && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-6">
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

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">P&L over time</h2>
      <div className="mb-8">
        <PnlChart data={series} />
      </div>

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Models</h2>
      {modelConfigsQuery.isLoading && <p>Loading...</p>}
      {modelConfigsQuery.data && modelConfigsQuery.data.length === 0 && (
        <EmptyState title="No models configured" message="Models are set up by the operator; check back once one is added." />
      )}
      {modelConfigsQuery.data && modelConfigsQuery.data.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4 mb-8">
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

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Recent activity</h2>
      {eventsQuery.isLoading && <p>Loading...</p>}
      {eventsQuery.data && eventsQuery.data.length === 0 && <p className="text-text-muted">No recent activity.</p>}
      {eventsQuery.data && eventsQuery.data.length > 0 && (
        <ul className="list-none p-0 m-0">
          {eventsQuery.data.map((e) => (
            <li key={e.event_id} className="flex gap-3 items-center py-2 border-b border-line text-sm">
              <span className="text-text-muted min-w-[150px]">{new Date(e.timestamp).toLocaleString()}</span>
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
