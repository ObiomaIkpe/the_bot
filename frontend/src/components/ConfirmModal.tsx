import { Button, type ButtonVariant } from "./Button";

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  /** Most callers confirm a destructive broker action (close/cancel),
   * hence the default -- pass "primary" for a non-destructive confirm
   * (e.g. "I've copied the token"). */
  variant?: Extract<ButtonVariant, "destructive" | "primary">;
}

/** Real, irreversible broker actions (close a position, cancel a pending
 * order) go through this -- no bare-click destructive buttons, per
 * ADMIN_FRONTEND_PLAN.md's explicit requirement for the Live page. */
export function ConfirmModal({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
  variant = "destructive",
}: ConfirmModalProps) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[100]">
      <div className="bg-bg-elevated border border-line rounded-lg p-6 min-w-80 max-w-[480px]">
        <h3 className="mt-0">{title}</h3>
        <p>{message}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant={variant} onClick={onConfirm} disabled={busy}>
            {busy ? "Working..." : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
