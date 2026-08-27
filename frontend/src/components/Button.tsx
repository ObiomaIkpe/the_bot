import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "destructive" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "secondary", className, ...rest }: ButtonProps) {
  return <button type="button" className={`btn btn-${variant}${className ? ` ${className}` : ""}`} {...rest} />;
}
