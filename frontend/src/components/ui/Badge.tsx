import React from "react";
import { cn } from "@/lib/utils";

export type BadgeTone =
  | "neutral"
  | "nominal"
  | "warning"
  | "critical"
  | "focus"
  | "tool"
  | "mock";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-subtle text-muted border-hairline",
  nominal: "bg-status-nominal/10 text-status-nominal border-status-nominal/30",
  warning: "bg-status-warning/10 text-status-warning border-status-warning/30",
  critical: "bg-status-critical/10 text-status-critical border-status-critical/30",
  focus: "bg-status-focus/10 text-status-focus border-status-focus/30",
  tool: "bg-status-tool/10 text-status-tool border-status-tool/30",
  mock: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/50 font-semibold",
};

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  mono?: boolean;
}

export function Badge({
  tone = "neutral",
  mono = false,
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium leading-tight border",
        mono && "font-mono tabular-nums",
        TONE_CLASSES[tone],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
