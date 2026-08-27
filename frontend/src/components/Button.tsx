import type { ButtonHTMLAttributes } from "react";
import { buttonClasses } from "../lib/buttonStyles";

export type ButtonVariant = "primary" | "secondary" | "destructive" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "secondary", className, ...rest }: ButtonProps) {
  return <button type="button" className={buttonClasses(variant, className)} {...rest} />;
}
