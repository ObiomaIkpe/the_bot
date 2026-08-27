import type { ReactNode } from "react";

/** One shared table definition replacing the thStyle/tdStyle objects
 * that used to be duplicated verbatim in every page. Callers render
 * plain <thead>/<tbody> children -- styling comes from the .table CSS
 * class (src/styles/components.css). */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="table-wrap">
      <table className="table">{children}</table>
    </div>
  );
}
