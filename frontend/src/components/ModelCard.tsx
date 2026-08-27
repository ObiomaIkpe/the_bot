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
      <div className="flex items-center justify-between gap-2">
        <Link to={`/models/${modelConfig.model_name}`} className="font-semibold text-text no-underline hover:underline">
          {modelConfig.model_name}
        </Link>
        <div className="flex gap-1.5">
          {modelConfig.is_paused && <Badge variant="paused">paused</Badge>}
          <Badge variant={modelStatusBadgeVariant(modelConfig.status)}>{modelConfig.status}</Badge>
        </div>
      </div>
      <div className="mt-3 text-sm text-text-muted grid gap-1">
        <div>
          Today P&L:{" "}
          <span className={`font-mono ${summary.today >= 0 ? "text-positive" : "text-negative"}`}>
            {summary.today.toFixed(2)}
          </span>
        </div>
        <div>Win rate: {summary.winRate === null ? "—" : `${summary.winRate.toFixed(0)}%`}</div>
        <div>Open positions: {openPositionsCount}</div>
      </div>
    </Card>
  );
}
