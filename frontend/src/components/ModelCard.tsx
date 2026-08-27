import { Link } from "react-router-dom";
import type { ModelConfigOut, TradeOut } from "../api/types";
import { modelStatusBadgeVariant } from "../lib/modelStatus";
import { summarizeTrades } from "../lib/pnl";
import { Badge } from "./Badge";
import { Card } from "./Card";

interface ModelCardProps {
  modelConfig: ModelConfigOut;
  /** All trades across all models -- this component filters by
   * model_name itself, so Overview and Models can both fetch trades
   * once and pass the same list down to every card. */
  trades: TradeOut[];
  openPositionsCount: number;
}

/** Shared by Overview's mini-cards and the full Models list -- same
 * card, same data, just a different grid around it. */
export function ModelCard({ modelConfig, trades, openPositionsCount }: ModelCardProps) {
  const modelTrades = trades.filter((t) => t.model === modelConfig.model_name);
  const summary = summarizeTrades(modelTrades);

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <Link
          to={`/models/${modelConfig.model_name}`}
          style={{ fontWeight: 600, color: "inherit", textDecoration: "none" }}
        >
          {modelConfig.model_name}
        </Link>
        <div style={{ display: "flex", gap: 6 }}>
          {modelConfig.is_paused && <Badge variant="paused">paused</Badge>}
          <Badge variant={modelStatusBadgeVariant(modelConfig.status)}>{modelConfig.status}</Badge>
        </div>
      </div>
      <div style={{ marginTop: 12, fontSize: 14, color: "var(--text-muted)", display: "grid", gap: 4 }}>
        <div>
          Today P&L:{" "}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              color: summary.today >= 0 ? "var(--positive)" : "var(--negative)",
            }}
          >
            {summary.today.toFixed(2)}
          </span>
        </div>
        <div>Win rate: {summary.winRate === null ? "—" : `${summary.winRate.toFixed(0)}%`}</div>
        <div>Open positions: {openPositionsCount}</div>
      </div>
    </Card>
  );
}
