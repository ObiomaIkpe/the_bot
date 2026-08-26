interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}

/** Real, irreversible broker actions (close a position, cancel a pending
 * order) go through this -- no bare-click destructive buttons, per
 * ADMIN_FRONTEND_PLAN.md's explicit requirement for the Live page. */
export function ConfirmModal({ title, message, confirmLabel, onConfirm, onCancel, busy }: ConfirmModalProps) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div style={{ background: "#fff", padding: 24, borderRadius: 8, minWidth: 320 }}>
        <h3 style={{ marginTop: 0 }}>{title}</h3>
        <p>{message}</p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={{ background: "#c0392b", color: "#fff", border: "none", padding: "8px 16px", borderRadius: 4 }}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
