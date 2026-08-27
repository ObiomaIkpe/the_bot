interface StatTileProps {
  label: string;
  value: string;
  tone?: "neutral" | "positive" | "negative";
}

const TONE_CLASSES: Record<NonNullable<StatTileProps["tone"]>, string> = {
  neutral: "text-text",
  positive: "text-positive",
  negative: "text-negative",
};

export function StatTile({ label, value, tone = "neutral" }: StatTileProps) {
  return (
    <div className="bg-bg-elevated border border-line rounded-lg px-5 py-4">
      <div className="text-xs text-text-muted uppercase tracking-wide mb-1.5">{label}</div>
      <div className={`font-mono text-2xl font-semibold ${TONE_CLASSES[tone]}`}>{value}</div>
    </div>
  );
}
