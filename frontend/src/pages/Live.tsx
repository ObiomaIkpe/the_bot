import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { AccountInfo, PendingOrder, Position } from "../api/types";
import { ConfirmModal } from "../components/ConfirmModal";

const thStyle: React.CSSProperties = { textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "6px 10px", borderBottom: "1px solid #eee" };

type PendingAction = { kind: "close"; ticket: number } | { kind: "cancel"; orderTicket: number };

function NoBridgeConfigured() {
  return (
    <p style={{ color: "#666" }}>
      No active, bridge-connected broker account configured for this user yet. Connect one under
      Settings first (see broker-credentials setup).
    </p>
  );
}

export function Live() {
  const queryClient = useQueryClient();
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);

  const accountInfoQuery = useQuery({
    queryKey: ["account-info"],
    queryFn: () => apiClient.get<AccountInfo>("/trading/account-info"),
    retry: false,
    refetchInterval: 30_000,
  });

  const positionsQuery = useQuery({
    queryKey: ["positions"],
    queryFn: () => apiClient.get<Position[]>("/trading/positions"),
    retry: false,
    refetchInterval: 15_000,
  });

  const pendingOrdersQuery = useQuery({
    queryKey: ["pending-orders"],
    queryFn: () => apiClient.get<PendingOrder[]>("/trading/pending-orders"),
    retry: false,
    refetchInterval: 15_000,
  });

  const closePosition = useMutation({
    mutationFn: (ticket: number) => apiClient.post(`/trading/positions/${ticket}/close`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      setPendingAction(null);
    },
  });

  const cancelPendingOrder = useMutation({
    mutationFn: (orderTicket: number) => apiClient.delete(`/trading/pending-orders/${orderTicket}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pending-orders"] });
      setPendingAction(null);
    },
  });

  const noBridge = (err: unknown) => err instanceof ApiError && err.status === 503;

  return (
    <div>
      <h1>Live</h1>

      <h2>Account</h2>
      {accountInfoQuery.isLoading && <p>Loading...</p>}
      {accountInfoQuery.error && noBridge(accountInfoQuery.error) && <NoBridgeConfigured />}
      {accountInfoQuery.error && !noBridge(accountInfoQuery.error) && (
        <p style={{ color: "crimson" }}>Failed to load account info: {String(accountInfoQuery.error)}</p>
      )}
      {accountInfoQuery.data && (
        <p>
          Balance: <strong>{accountInfoQuery.data.balance.toFixed(2)}</strong> {accountInfoQuery.data.currency} ·
          Equity: {accountInfoQuery.data.equity.toFixed(2)} · Margin free: {accountInfoQuery.data.margin_free.toFixed(2)}
        </p>
      )}

      <h2>Open positions</h2>
      {positionsQuery.error && noBridge(positionsQuery.error) && <NoBridgeConfigured />}
      {positionsQuery.error && !noBridge(positionsQuery.error) && (
        <p style={{ color: "crimson" }}>Failed to load positions: {String(positionsQuery.error)}</p>
      )}
      {positionsQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%", marginBottom: 32 }}>
          <thead>
            <tr>
              <th style={thStyle}>Ticket</th>
              <th style={thStyle}>Symbol</th>
              <th style={thStyle}>Direction</th>
              <th style={thStyle}>Volume</th>
              <th style={thStyle}>Open</th>
              <th style={thStyle}>Current</th>
              <th style={thStyle}>Profit</th>
              <th style={thStyle}></th>
            </tr>
          </thead>
          <tbody>
            {positionsQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={8}>
                  No open positions.
                </td>
              </tr>
            )}
            {positionsQuery.data.map((p) => (
              <tr key={p.ticket}>
                <td style={tdStyle}>{p.ticket}</td>
                <td style={tdStyle}>{p.symbol}</td>
                <td style={tdStyle}>{p.direction}</td>
                <td style={tdStyle}>{p.volume}</td>
                <td style={tdStyle}>{p.open_price}</td>
                <td style={tdStyle}>{p.current_price}</td>
                <td style={{ ...tdStyle, color: p.profit >= 0 ? "#2e7d32" : "#c0392b" }}>{p.profit.toFixed(2)}</td>
                <td style={tdStyle}>
                  <button type="button" onClick={() => setPendingAction({ kind: "close", ticket: p.ticket })}>
                    Close
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Pending orders</h2>
      {pendingOrdersQuery.error && noBridge(pendingOrdersQuery.error) && <NoBridgeConfigured />}
      {pendingOrdersQuery.error && !noBridge(pendingOrdersQuery.error) && (
        <p style={{ color: "crimson" }}>Failed to load pending orders: {String(pendingOrdersQuery.error)}</p>
      )}
      {pendingOrdersQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th style={thStyle}>Ticket</th>
              <th style={thStyle}>Symbol</th>
              <th style={thStyle}>Direction</th>
              <th style={thStyle}>Volume</th>
              <th style={thStyle}>Entry</th>
              <th style={thStyle}>Stop</th>
              <th style={thStyle}></th>
            </tr>
          </thead>
          <tbody>
            {pendingOrdersQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={7}>
                  No pending orders.
                </td>
              </tr>
            )}
            {pendingOrdersQuery.data.map((o) => (
              <tr key={o.order_ticket}>
                <td style={tdStyle}>{o.order_ticket}</td>
                <td style={tdStyle}>{o.symbol}</td>
                <td style={tdStyle}>{o.direction}</td>
                <td style={tdStyle}>{o.volume}</td>
                <td style={tdStyle}>{o.entry_price}</td>
                <td style={tdStyle}>{o.stop_loss}</td>
                <td style={tdStyle}>
                  <button
                    type="button"
                    onClick={() => setPendingAction({ kind: "cancel", orderTicket: o.order_ticket })}
                  >
                    Cancel
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {pendingAction?.kind === "close" && (
        <ConfirmModal
          title="Close position"
          message={`Close position #${pendingAction.ticket} at market, right now? This cannot be undone.`}
          confirmLabel="Close position"
          busy={closePosition.isPending}
          onCancel={() => setPendingAction(null)}
          onConfirm={() => closePosition.mutate(pendingAction.ticket)}
        />
      )}
      {pendingAction?.kind === "cancel" && (
        <ConfirmModal
          title="Cancel pending order"
          message={`Cancel pending order #${pendingAction.orderTicket}? This cannot be undone.`}
          confirmLabel="Cancel order"
          busy={cancelPendingOrder.isPending}
          onCancel={() => setPendingAction(null)}
          onConfirm={() => cancelPendingOrder.mutate(pendingAction.orderTicket)}
        />
      )}
    </div>
  );
}
