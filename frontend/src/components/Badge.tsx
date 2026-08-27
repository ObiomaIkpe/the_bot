import type { ReactNode } from "react";

export type BadgeVariant = "neutral" | "active" | "shadow" | "disabled" | "paused" | "connected" | "error";

export function Badge({ variant = "neutral", children }: { variant?: BadgeVariant; children: ReactNode }) {
  return <span className={`badge badge-${variant}`}>{children}</span>;
}
