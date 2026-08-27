import type { ButtonVariant } from "../components/Button";

/** Shared with anywhere a Link needs to look like a Button (e.g.
 * Overview's "Connect MT5 account" empty-state action) -- kept out of
 * Button.tsx so that file only exports the component (oxlint's
 * react/only-export-components rule). */
const BASE =
  "inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border text-sm font-medium no-underline transition-colors disabled:opacity-50 disabled:cursor-not-allowed";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent border-accent text-[#05070c] hover:bg-accent-hover",
  secondary: "bg-transparent border-line text-text hover:bg-bg-elevated-2",
  destructive: "bg-negative border-negative text-[#1a0508] hover:brightness-110",
  ghost: "bg-transparent border-transparent text-text-muted hover:text-text",
};

export function buttonClasses(variant: ButtonVariant, extra?: string): string {
  return `${BASE} ${VARIANTS[variant]}${extra ? ` ${extra}` : ""}`;
}
