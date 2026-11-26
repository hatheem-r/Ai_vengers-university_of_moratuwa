import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  Clock,
  Coins,
  Cpu,
  FileCheck2,
  FileWarning,
  Loader2,
  RotateCcw,
  ShieldCheck,
  Terminal,
  UserCheck,
  Wrench,
} from "lucide-react";
import { useRunStatus, useRunTrace } from "@/api/hooks";
import type { RunStep, StepStatusCode } from "@/api/types";
import { useAppStore } from "@/state/appStore";
import { cn, formatDuration, formatTokens, formatUsd } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

function stepTone(status: StepStatusCode) {
  if (status === "done") return "nominal" as const;
  if (status === "running") return "focus" as const;
  if (status === "failed") return "critical" as const;
  return "neutral" as const;
}

function StepStatusIcon({ status }: { status: StepStatusCode }) {
  if (status === "done") {
    return <CheckCircle2 className="h-4 w-4 text-status-nominal shrink-0" strokeWidth={1.75} />;
  }
  if (status === "running") {
    return <Loader2 className="h-4 w-4 text-status-focus animate-spin shrink-0" strokeWidth={1.75} />;
  }
  if (status === "failed") {
    return <FileWarning className="h-4 w-4 text-status-critical shrink-0" strokeWidth={1.75} />;
  }
  return <div className="h-3.5 w-3.5 rounded-full border border-strong shrink-0" />;
}

export function LiveRunPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { activeRunId, setActiveRunId } = useAppStore();
  const effectiveRunId = runId || activeRunId;

  useEffect(() => {
    if (runId && runId !== activeRunId) {
      setActiveRunId(runId);
    }
  }, [runId, activeRunId, setActiveRunId]);

  const statusQuery = useRunStatus(effectiveRunId);
  const run = statusQuery.data;
  const isLive = !run || run.status === "queued" || run.status === "running";
  const traceQuery = useRunTrace(effectiveRunId, isLive);

  if (statusQuery.error) {
    return (
      <div className="space-y-4">
        <ErrorBanner
          title={`Failed to load run ${effectiveRunId}`}
          error={statusQuery.error}
          onRetry={() => statusQuery.refetch()}
        />
        <Button variant="outline" onClick={() => navigate("/")}>
          Back to Start
        </Button>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-20 w-full" />
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </div>
    );
  }

  const totalTokens = run.metrics.input_tokens + run.metrics.output_tokens;
  const recentEvents = (traceQuery.data?.events ?? []).slice(-6).reverse();

  return (
    <div className="space-y-5">
      {/* Header Strip */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-mono text-muted">
            <span>Step 02 · Live Orchestration Pipeline</span>
            <span>·</span>
            <span>{run.run_id}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2.5 mt-1">
            <h1 className="text-xl font-semibold tracking-tight font-mono">{run.bundle_id}</h1>
            <Badge tone="focus" mono>
              {run.system_id} ({run.mode})
            </Badge>
            <Badge
              tone={
                run.status === "completed"
                  ? "nominal"
                  : run.status === "awaiting_approval"
                    ? "warning"
                    : run.status === "failed"
                      ? "critical"
                      : "focus"
              }
              mono
              className="uppercase"
            >
              {run.status.replace("_", " ")}
            </Badge>
            {run.plan_version > 0 && (
              <Badge tone="neutral" mono>
                Plan v{run.plan_version}
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Link to={`/run/${run.run_id}/trace`}>
            <Button variant="outline" size="md">
              <Terminal className="h-3.5 w-3.5" strokeWidth={1.5} />
              Inspect Trace
            </Button>
          </Link>
          {(run.status === "awaiting_approval" || run.status === "completed") && (
            <Button
              variant={run.status === "awaiting_approval" ? "nominal" : "primary"}
              size="md"
              onClick={() => navigate(`/run/${run.run_id}/review`)}
            >
              Review & Approve Plan
              <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.75} />
            </Button>
          )}
        </div>
      </div>

      {run.error && (
        <ErrorBanner title={`Run failed (${run.error.code})`} error={run.error.message} />
      )}

      {/* Live Monospace Telemetry Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Elapsed Time</span>
            <Clock className="h-3.5 w-3.5 text-muted" strokeWidth={1.5} />
          </div>
          <div className="font-mono text-lg font-semibold mt-1 tabular-nums">
            {formatDuration(run.metrics.elapsed_s)}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            cap: {run.budget.max_seconds}s
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Total Tokens</span>
            <Cpu className="h-3.5 w-3.5 text-muted" strokeWidth={1.5} />
          </div>
          <div className="font-mono text-lg font-semibold mt-1 tabular-nums">
            {formatTokens(totalTokens)}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            {formatTokens(run.metrics.input_tokens)} in · {formatTokens(run.metrics.output_tokens)} out (
            {formatTokens(run.metrics.cached_input_tokens)} cached)
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Estimated Cost</span>
            <Coins className="h-3.5 w-3.5 text-muted" strokeWidth={1.5} />
          </div>
          <div className="font-mono text-lg font-semibold mt-1 tabular-nums">
            {formatUsd(run.metrics.cost_usd)}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            target: ≤ $0.50 / cycle
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>LLM & Deterministic Calls</span>
            <Wrench className="h-3.5 w-3.5 text-status-tool" strokeWidth={1.5} />
          </div>
          <div className="font-mono text-lg font-semibold mt-1 tabular-nums">
            {run.metrics.llm_calls} <span className="text-xs font-normal text-muted">LLM</span> ·{" "}
            <span className="text-status-tool">{run.metrics.tool_calls}</span>{" "}
            <span className="text-xs font-normal text-muted">Tools</span>
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            LLM budget: {run.metrics.llm_calls}/{run.budget.max_llm_calls}
          </div>
        </div>
      </div>

      {/* Horizontal Visual Pipeline Stepper Mirroring LangGraph */}
      <div
        className={cn(
          "grid grid-cols-1 gap-3.5",
          run.steps.length === 2 ? "lg:grid-cols-2" : "lg:grid-cols-4"
        )}
      >
        {run.steps.map((step: RunStep, idx: number) => {
          const isApprovalReady = step.key === "approval" && run.status === "awaiting_approval";
          const checkerLooped = step.key === "checker" && run.revisions.checker > 0;

          return (
            <div
              key={step.key}
              className={cn(
                "rounded-md border bg-surface p-4 flex flex-col justify-between gap-3 transition-colors relative",
                isApprovalReady
                  ? "border-status-warning ring-1 ring-status-warning/30"
                  : step.status === "running"
                    ? "border-status-focus ring-1 ring-status-focus/25"
                    : step.status === "done"
                      ? "border-status-nominal/40"
                      : "border-hairline"
              )}
            >
              <div className="space-y-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <StepStatusIcon status={isApprovalReady ? "running" : step.status} />
                    <div>
                      <div className="text-[10px] font-mono uppercase text-faint">
                        Stage 0{idx + 1}
                      </div>
                      <h3 className="text-sm font-semibold leading-tight">{step.label}</h3>
                    </div>
                  </div>
                  <Badge tone={isApprovalReady ? "warning" : stepTone(step.status)} mono>
                    {isApprovalReady ? "INTERRUPT" : step.status.toUpperCase()}
                  </Badge>
                </div>

                {step.model ? (
                  <div className="text-[11px] font-mono text-muted">{step.model}</div>
                ) : (
                  <div className="text-[11px] font-mono text-muted flex items-center gap-1">
                    <UserCheck className="h-3.5 w-3.5 text-status-warning" strokeWidth={1.5} />
                    Human-in-the-loop Gate
                  </div>
                )}

                {/* Deterministic Tool & Revision Badges */}
                {step.key === "planner" && (
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    <Badge tone="tool" mono>mrp_tool</Badge>
                    <Badge tone="tool" mono>schedule_tool</Badge>
                    {run.revisions.human > 0 && (
                      <Badge tone="warning" mono>Human Re-plan ×{run.revisions.human}</Badge>
                    )}
                  </div>
                )}

                {step.key === "checker" && (
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    <Badge tone="tool" mono>rule_check</Badge>
                    {checkerLooped ? (
                      <Badge tone="warning" mono>
                        <RotateCcw className="h-3 w-3" />
                        1x Revision Triggered
                      </Badge>
                    ) : (
                      <Badge tone="neutral" mono>0/1 Revisions</Badge>
                    )}
                  </div>
                )}

                {step.key === "agent" && (
                  <div className="flex flex-wrap gap-1.5 pt-0.5">
                    <Badge tone="tool" mono>read_document</Badge>
                    <Badge tone="tool" mono>mrp_tool</Badge>
                    <Badge tone="tool" mono>schedule_tool</Badge>
                    <Badge tone="tool" mono>rule_check</Badge>
                  </div>
                )}

                {/* Parallel Intake Per-Document Progress Chips */}
                {step.progress && (
                  <div className="space-y-1.5 pt-1">
                    <div className="flex items-center justify-between text-[11px] font-mono">
                      <span className="text-muted">Documents Validated</span>
                      <span className="font-semibold text-ink">
                        {step.progress.done}/{step.progress.total}
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-subtle overflow-hidden">
                      <div
                        className="h-full bg-status-nominal transition-all duration-300"
                        style={{
                          width: `${
                            step.progress.total > 0
                              ? Math.round((step.progress.done / step.progress.total) * 100)
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  </div>
                )}

                {step.items && step.items.length > 0 && (
                  <div className="max-h-32 overflow-y-auto space-y-1 pt-1 pr-0.5">
                    {step.items.map((doc) => (
                      <div
                        key={doc.name}
                        className="flex items-center justify-between gap-1.5 rounded bg-subtle/60 border border-hairline px-2 py-1 text-[11px] font-mono"
                      >
                        <span className="truncate">{doc.name}</span>
                        {doc.status === "done" ? (
                          <FileCheck2 className="h-3 w-3 text-status-nominal shrink-0" />
                        ) : doc.status === "running" ? (
                          <Loader2 className="h-3 w-3 text-status-focus animate-spin shrink-0" />
                        ) : doc.status === "failed" ? (
                          <FileWarning className="h-3 w-3 text-status-critical shrink-0" />
                        ) : (
                          <span className="text-[10px] text-faint">wait</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="pt-2 border-t border-hairline/70 space-y-2">
                <p className="text-xs text-muted leading-snug min-h-[2rem]">
                  {step.detail || "Waiting for upstream stage..."}
                </p>

                {isApprovalReady && (
                  <Button
                    variant="nominal"
                    size="sm"
                    onClick={() => navigate(`/run/${run.run_id}/review`)}
                    className="w-full font-semibold"
                  >
                    <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.75} />
                    Review & Approve Plan →
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Live Trace Telemetry Stream */}
      <div className="rounded-md border border-hairline bg-surface overflow-hidden">
        <div className="px-4 py-2.5 border-b border-hairline bg-subtle/40 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <Terminal className="h-3.5 w-3.5 text-status-focus" strokeWidth={1.5} />
            <span>Live Execution Feed</span>
            {isLive && (
              <span className="inline-flex items-center gap-1 text-[11px] font-mono text-status-focus">
                <span className="h-1.5 w-1.5 rounded-full bg-status-focus animate-ping" />
                polling 2s
              </span>
            )}
          </div>
          <Link
            to={`/run/${run.run_id}/trace`}
            className="text-xs font-medium text-status-focus hover:underline"
          >
            Full Trace Timeline →
          </Link>
        </div>

        <div className="divide-y divide-hairline font-mono text-xs">
          {recentEvents.length === 0 ? (
            <div className="p-4 text-muted text-center">Waiting for trace events...</div>
          ) : (
            recentEvents.map((ev) => (
              <div key={ev.seq} className="px-4 py-2 flex items-center justify-between gap-4">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="text-faint w-7 shrink-0">#{String(ev.seq).padStart(2, "0")}</span>
                  <Badge
                    tone={
                      ev.type === "handoff"
                        ? "focus"
                        : ev.type === "tool_call"
                          ? "tool"
                          : ev.type === "checker_verdict"
                            ? "warning"
                            : ev.type === "interrupt"
                              ? "nominal"
                              : "neutral"
                    }
                    mono
                  >
                    {ev.type}
                  </Badge>
                  <span className="truncate font-sans text-ink">{ev.summary}</span>
                </div>
                <div className="text-[11px] text-muted shrink-0">
                  {ev.latency_ms !== null ? `${ev.latency_ms}ms` : ""}
                  {ev.cost_usd ? ` · $${ev.cost_usd.toFixed(3)}` : ""}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
