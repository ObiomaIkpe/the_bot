import type { ProvisioningStatus } from "../api/types";
import type { BadgeVariant } from "../components/Badge";

/** Kept out of Badge.tsx so that file only exports the component
 * (oxlint's react/only-export-components rule). */
export function provisioningBadgeVariant(status: ProvisioningStatus): BadgeVariant {
  switch (status) {
    case "active":
      return "connected";
    case "in_progress":
      return "shadow";
    case "pending":
      return "neutral";
    case "failed":
      return "error";
    case "not_requested":
      return "neutral";
  }
}

// Mirrors app/models/broker_credential.py's VALID_PROVISIONING_STEPS --
// keep in sync by hand, same convention this file already follows for
// mirroring backend schemas.
const STEP_LABELS: Record<string, string> = {
  cleaning_up: "Cleaning up...",
  copying_terminal: "Copying your MT5 terminal...",
  launching_and_logging_in: "Launching and logging in...",
  verifying_login: "Verifying login...",
  configuring_worker: "Configuring worker...",
  installing_service: "Installing service...",
  opening_firewall: "Opening firewall...",
  waiting_for_health: "Waiting for connection to come up...",
};

export function provisioningStepLabel(step: string | null): string | null {
  return step ? (STEP_LABELS[step] ?? step) : null;
}
