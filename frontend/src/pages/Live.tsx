import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { AccountInfo, BridgeHealth, PendingOrder, Position } from "../api/types";
import { Button } from "../components/Button";
import { ConfirmModal } from "../components/ConfirmModal";
import { EmptyState } from "../components/EmptyState";
import { StatTile } from "../components/StatTile";
import { Table } from "../components/Table";

type PendingAction = { kind: "close"; ticket: number } | { kind: "cancel"; orderTicket: number };

function NoBridgeConfigured() {
  return (
    <EmptyState
      title="No broker account connected"
      message="Connect a broker account under Broker Connection first, then it will show up here."
    />
  );
}

function OrdersDisabled() {
  return (
    <EmptyState
      title="Order placement is disabled for this account"
      message="This bridge worker has order placement turned off, so positions and pending orders can't be listed. This is expected for an account not meant to trade for real."
    />
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

  const bridgeHealthQuery = useQuery({
    queryKey: ["bridge-health"],
    queryFn: () => apiClient.get<BridgeHealth>("/trading/health"),
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
  const ordersDisabled = (err: unknown) => err instanceof ApiError && err.status === 409;

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Live</h1>
      </div>

      {bridgeHealthQuery.data && !bridgeHealthQuery.data.connected && (
        <div className="px-4 py-3 rounded-lg mb-5 text-sm bg-warning/10 border border-warning/30 text-warning">
          The bridge is reachable, but its MT5 terminal isn't currently connected
          {bridgeHealthQuery.data.detail ? ` — ${bridgeHealthQuery.data.detail}` : "."}
        </div>
      )}

      {accountInfoQuery.isLoading && <p>Loading...</p>}
      {accountInfoQuery.error && noBridge(accountInfoQuery.error) && <NoBridgeConfigured />}
      {accountInfoQuery.error && !noBridge(accountInfoQuery.error) && (
        <p className="text-negative">Failed to load account info: {String(accountInfoQuery.error)}</p>
      )}
      {accountInfoQuery.data && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-6">
          <StatTile label="Balance" value={`${accountInfoQuery.data.balance.toFixed(2)} ${accountInfoQuery.data.currency}`} />
          <StatTile label="Equity" value={accountInfoQuery.data.equity.toFixed(2)} />
          <StatTile label="Margin free" value={accountInfoQuery.data.margin_free.toFixed(2)} />
        </div>
      )}

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Open positions</h2>
      {positionsQuery.error && noBridge(positionsQuery.error) && <NoBridgeConfigured />}
      {positionsQuery.error && ordersDisabled(positionsQuery.error) && <OrdersDisabled />}
      {positionsQuery.error && !noBridge(positionsQuery.error) && !ordersDisabled(positionsQuery.error) && (
        <p className="text-negative">Failed to load positions: {String(positionsQuery.error)}</p>
      )}
      {positionsQuery.data && positionsQuery.data.length === 0 && (
        <EmptyState title="No open positions" message="Nothing is currently open." />
      )}
      {positionsQuery.data && positionsQuery.data.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Symbol</th>
              <th>Direction</th>
              <th>Volume</th>
              <th>Open</th>
              <th>Current</th>
              <th>Profit</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {positionsQuery.data.map((p) => (
              <tr key={p.ticket}>
                <td>{p.ticket}</td>
                <td>{p.symbol}</td>
                <td>{p.direction}</td>
                <td>{p.volume}</td>
                <td className="font-mono">{p.open_price}</td>
                <td className="font-mono">{p.current_price}</td>
                <td className={`font-mono ${p.profit >= 0 ? "text-positive" : "text-negative"}`}>{p.profit.toFixed(2)}</td>
                <td>
                  <Button variant="destructive" onClick={() => setPendingAction({ kind: "close", ticket: p.ticket })}>
                    Close
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3 mt-8">Pending orders</h2>
      {pendingOrdersQuery.error && noBridge(pendingOrdersQuery.error) && <NoBridgeConfigured />}
      {pendingOrdersQuery.error && ordersDisabled(pendingOrdersQuery.error) && <OrdersDisabled />}
      {pendingOrdersQuery.error && !noBridge(pendingOrdersQuery.error) && !ordersDisabled(pendingOrdersQuery.error) && (
        <p className="text-negative">Failed to load pending orders: {String(pendingOrdersQuery.error)}</p>
      )}
      {pendingOrdersQuery.data && pendingOrdersQuery.data.length === 0 && (
        <EmptyState title="No pending orders" message="Nothing is currently pending." />
      )}
      {pendingOrdersQuery.data && pendingOrdersQuery.data.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Symbol</th>
              <th>Direction</th>
              <th>Volume</th>
              <th>Entry</th>
              <th>Stop</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {pendingOrdersQuery.data.map((o) => (
              <tr key={o.order_ticket}>
                <td>{o.order_ticket}</td>
                <td>{o.symbol}</td>
                <td>{o.direction}</td>
                <td>{o.volume}</td>
                <td className="font-mono">{o.entry_price}</td>
                <td className="font-mono">{o.stop_loss}</td>
                <td>
                  <Button
                    variant="destructive"
                    onClick={() => setPendingAction({ kind: "cancel", orderTicket: o.order_ticket })}
                  >
                    Cancel
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
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
