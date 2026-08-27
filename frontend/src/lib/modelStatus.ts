import type { ModelStatus } from "../api/types";
import type { BadgeVariant } from "../components/Badge";

/** Kept out of Badge.tsx so that file only exports the component
 * (oxlint's react/only-export-components rule). */
export function modelStatusBadgeVariant(status: ModelStatus): BadgeVariant {
  switch (status) {
    case "active":
      return "active";
    case "shadow":
      return "shadow";
    case "disabled":
      return "disabled";
  }
}
