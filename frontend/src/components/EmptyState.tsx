import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  message: string;
  action?: ReactNode;
}

/** Generalizes what used to be Live.tsx's one-off NoBridgeConfigured
 * paragraph into a reusable "nothing here yet" block, used across
 * Overview/Live/Models/Trade History/Broker Connection. */
export function EmptyState({ title, message, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{message}</p>
      {action}
    </div>
  );
}
