import { AlertTriangle, RefreshCw } from "lucide-react";
import { ApiClientError } from "@/api/client";
import { Button } from "./Button";

interface ErrorBannerProps {
  title?: string;
  error: unknown;
  onRetry?: () => void;
}

export function ErrorBanner({ title = "Request failed", error, onRetry }: ErrorBannerProps) {
  const code = error instanceof ApiClientError ? error.code : "error";
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "An unexpected error occurred while communicating with the planning server.";

  return (
    <div
      role="alert"
      className="rounded-md border border-status-critical/40 bg-status-critical/5 p-3.5 text-xs flex items-start justify-between gap-4"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="h-4 w-4 text-status-critical shrink-0 mt-0.5" strokeWidth={1.75} />
        <div className="space-y-1">
          <div className="font-semibold text-ink flex items-center gap-2">
            <span>{title}</span>
            <span className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-status-critical/10 text-status-critical">
              {code}
            </span>
          </div>
          <p className="text-muted leading-relaxed">{message}</p>
        </div>
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="shrink-0">
          <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} />
          Retry
        </Button>
      )}
    </div>
  );
}
