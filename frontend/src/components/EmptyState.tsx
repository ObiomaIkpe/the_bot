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
    <div className="text-center py-10 px-5 text-text-muted border border-dashed border-line rounded-lg">
      <h3 className="text-text m-0 mb-2 text-base font-semibold">{title}</h3>
      <p className="m-0 mb-4">{message}</p>
      {action}
    </div>
  );
}
