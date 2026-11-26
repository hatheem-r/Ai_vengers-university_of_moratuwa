import React from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "outline" | "nominal" | "danger" | "ghost";
type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-status-focus text-white border border-status-focus hover:bg-blue-700 active:bg-blue-800",
  secondary:
    "bg-subtle text-ink border border-hairline hover:border-strong hover:bg-hairline/40",
  outline:
    "bg-surface text-ink border border-hairline hover:border-strong hover:bg-subtle",
  nominal:
    "bg-status-nominal text-white border border-status-nominal hover:bg-emerald-700",
  danger:
    "bg-surface text-status-critical border border-status-critical/40 hover:bg-status-critical/10",
  ghost:
    "bg-transparent text-muted hover:text-ink hover:bg-subtle border border-transparent",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-xs font-medium gap-2",
  lg: "h-10 px-4 text-sm font-medium gap-2",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", className, disabled, children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled}
        className={cn(
          "inline-flex items-center justify-center rounded transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-focus/40 disabled:opacity-50 disabled:pointer-events-none select-none",
          VARIANT_CLASSES[variant],
          SIZE_CLASSES[size],
          className
        )}
        {...props}
      >
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";
