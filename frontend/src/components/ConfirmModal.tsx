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
    <div className="modal-overlay">
      <div className="modal-card">
        <h3 style={{ marginTop: 0 }}>{title}</h3>
        <p>{message}</p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
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
