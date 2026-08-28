import { useState } from "react";
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
  /** When set, renders this text in a monospace box with a one-click
   * Copy button below the message -- for secrets shown exactly once
   * (e.g. a freshly-minted bridge token) where making the user
   * select-and-copy by hand invites a partial/mis-selected copy. */
  copyText?: string;
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
  copyText,
}: ConfirmModalProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    if (!copyText) return;
    try {
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API can be unavailable (insecure context, permissions) --
      // the text is still selectable in the box below as a fallback, so
      // fail quietly rather than showing an alarming error for this.
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[100]">
      <div className="bg-bg-elevated border border-line rounded-lg p-6 min-w-80 max-w-[480px]">
        <h3 className="mt-0">{title}</h3>
        <p>{message}</p>
        {copyText != null && (
          <div className="flex items-center gap-2 mb-3">
            <code className="flex-1 min-w-0 truncate bg-bg-base border border-line rounded px-2 py-1.5 text-[13px]">
              {copyText}
            </code>
            <Button type="button" variant="secondary" onClick={handleCopy}>
              {copied ? "Copied!" : "Copy"}
            </Button>
          </div>
        )}
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
