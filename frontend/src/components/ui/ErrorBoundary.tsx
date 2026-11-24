import React from "react";
import { AlertOctagon, RotateCcw } from "lucide-react";
import { Button } from "./Button";

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class GlobalErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-canvas text-ink flex items-center justify-center p-6">
          <div className="max-w-lg w-full rounded-md border border-hairline bg-surface p-6 space-y-4">
            <div className="flex items-center gap-2.5 text-status-critical">
              <AlertOctagon className="h-5 w-5" strokeWidth={1.5} />
              <h1 className="text-base font-semibold text-ink">Interface Recovery Guard</h1>
            </div>
            <p className="text-xs text-muted leading-relaxed">
              An unexpected UI rendering fault was caught before producing a blank screen. Your active runs on the
              FastAPI backend are unaffected.
            </p>
            {this.state.error && (
              <pre className="rounded bg-subtle border border-hairline p-3 text-[11px] font-mono text-status-critical overflow-x-auto">
                {this.state.error.message}
              </pre>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Button
                variant="primary"
                onClick={() => {
                  this.setState({ hasError: false, error: null });
                  window.location.href = "/";
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                Return to Start Console
              </Button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
