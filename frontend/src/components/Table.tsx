import type { ReactNode } from "react";

/** One shared table definition replacing the thStyle/tdStyle objects
 * that used to be duplicated verbatim in every page. Callers render
 * plain <thead>/<tbody> children with no per-cell classes -- styling is
 * applied via Tailwind's arbitrary descendant-selector variants
 * ([&_th]:..., [&_td]:...) since we don't control that markup here. */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table
        className="w-full border-collapse
          [&_th]:text-left [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:uppercase [&_th]:tracking-wide
          [&_th]:text-text-muted [&_th]:border-b [&_th]:border-line [&_th]:whitespace-nowrap
          [&_td]:px-3 [&_td]:py-2.5 [&_td]:border-b [&_td]:border-line [&_td]:text-sm
          [&_tbody_tr:hover]:bg-bg-elevated-2"
      >
        {children}
      </table>
    </div>
  );
}
