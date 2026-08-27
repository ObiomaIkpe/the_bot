import type { ReactNode } from "react";

export type BadgeVariant = "neutral" | "active" | "shadow" | "disabled" | "paused" | "connected" | "error";

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: "bg-bg-elevated-2 text-text-muted border-line",
  disabled: "bg-bg-elevated-2 text-text-muted border-line",
  active: "bg-positive/10 text-positive border-positive/30",
  connected: "bg-positive/10 text-positive border-positive/30",
  shadow: "bg-accent/10 text-accent border-accent/30",
  paused: "bg-paused/15 text-paused border-paused/35",
  error: "bg-negative/10 text-negative border-negative/30",
};

export function Badge({ variant = "neutral", children }: { variant?: BadgeVariant; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide border ${VARIANT_CLASSES[variant]}`}
    >
      {children}
    </span>
  );
}
