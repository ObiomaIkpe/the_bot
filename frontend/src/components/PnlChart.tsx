import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PnlPoint } from "../lib/pnl";
import { EmptyState } from "./EmptyState";

/** Colored via CSS custom properties (var(--color-accent) etc.) instead
 * of hardcoded hex, so it re-themes automatically with the light/dark
 * toggle -- no chart-specific theme branching needed. */
export function PnlChart({ data }: { data: PnlPoint[] }) {
  if (data.length === 0) {
    return <EmptyState title="No trade history yet" message="A P&L chart will appear here once there are trades." />;
  }

  return (
    <div style={{ width: "100%", height: 220 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-line)" strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickFormatter={(value: string) => new Date(value).toLocaleDateString()}
            stroke="var(--color-text-muted)"
            fontSize={12}
            minTickGap={40}
          />
          <YAxis stroke="var(--color-text-muted)" fontSize={12} width={60} />
          <Tooltip
            labelFormatter={(value) => (typeof value === "string" ? new Date(value).toLocaleString() : value)}
            formatter={(value) => (typeof value === "number" ? value.toFixed(2) : String(value))}
            contentStyle={{
              background: "var(--color-bg-elevated)",
              border: "1px solid var(--color-line)",
              borderRadius: 8,
              color: "var(--color-text)",
              fontSize: 13,
            }}
          />
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
  );
}
