import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PnlPoint } from "../lib/pnl";
import { EmptyState } from "./EmptyState";

/** Colored via CSS custom properties (var(--color-accent) etc.) instead
 * of hardcoded hex, so it re-themes automatically with the light/dark
 * toggle -- no chart-specific theme branching needed. */

const tooltipStyle = {
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-line)",
  borderRadius: 8,
  color: "var(--color-text)",
  fontSize: 13,
};

/** Shared by both charts below -- every point already carries everything
 * either one needs, so one tooltip content works for both instead of
 * two near-duplicate formatters. Shows the real date/time (the x-axis
 * itself only shows a trade sequence number -- see PnlPoint.tradeNumber's
 * own comment for why two trades can land on the exact same date) plus
 * enough context (direction, outcome, this trade's own profit, and the
 * running total after it) to read one point fully without guessing. */
function PnlTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: PnlPoint }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0].payload;
  return (
    <div style={tooltipStyle} className="px-3 py-2">
      <div className="text-text-muted mb-1">{new Date(point.date).toLocaleString()}</div>
      <div>
        {point.direction} · {point.outcome}
      </div>
      <div className={point.profit >= 0 ? "text-positive" : "text-negative"}>
        This trade: {point.profit.toFixed(2)}
      </div>
      <div className="text-text-muted">Running total: {point.cumulative.toFixed(2)}</div>
    </div>
  );
}

/** Per-trade profit/loss, one bar per trade -- a diverging bar chart
 * (above/below the zero baseline), not a magnitude scale. The bar's
 * position relative to the baseline is the real signal (win = above,
 * loss = below); color is reinforcement on top of that, not the only
 * way to tell them apart -- readable even without distinguishing
 * green from red. Kept as its own chart rather than combined with the
 * cumulative line below: the two measure different things at very
 * different scales as trade count grows (one trade's own P&L vs a
 * growing running total), and a shared x-axis across two small charts
 * reads cleanly without resorting to a second y-axis on one chart. */
function PerTradeChart({ data }: { data: PnlPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={90}>
      <BarChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
        <XAxis dataKey="tradeNumber" hide />
        <YAxis hide />
        <ReferenceLine y={0} stroke="var(--color-line)" />
        <Tooltip content={<PnlTooltip />} />
        <Bar dataKey="profit" maxBarSize={20} radius={[3, 3, 3, 3]}>
          {data.map((point) => (
            <Cell
              key={point.tradeNumber}
              fill={point.profit >= 0 ? "var(--color-positive)" : "var(--color-negative)"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function PnlChart({ data }: { data: PnlPoint[] }) {
  if (data.length === 0) {
    return <EmptyState title="No trade history yet" message="A P&L chart will appear here once there are trades." />;
  }

  return (
    <div>
      <div className="text-[13px] text-text-muted mb-1">Per-trade result</div>
      <PerTradeChart data={data} />
      <div className="text-[13px] text-text-muted mt-3 mb-1">Running total</div>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="3 3" />
            <XAxis
              dataKey="tradeNumber"
              tickFormatter={(value: number) => `#${value}`}
              stroke="var(--color-text-muted)"
              fontSize={12}
              minTickGap={40}
            />
            <YAxis stroke="var(--color-text-muted)" fontSize={12} width={60} />
            <Tooltip content={<PnlTooltip />} />
            <Line
              type="monotone"
              dataKey="cumulative"
              stroke="var(--color-accent)"
              strokeWidth={2}
              dot={data.length < 20}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
