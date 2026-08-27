import type { CSSProperties, ReactNode } from "react";

export function Card({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`bg-bg-elevated border border-line rounded-lg p-5${className ? ` ${className}` : ""}`} style={style}>
      {children}
    </div>
  );
}
