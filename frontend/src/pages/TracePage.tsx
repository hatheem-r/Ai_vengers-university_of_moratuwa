import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowRightLeft,
  ClipboardCheck,
  Code2,
  Download,
  Filter,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react";
import { apiClient } from "@/api/client";
import { useRunStatus, useRunTrace } from "@/api/hooks";
import type { TraceEvent } from "@/api/types";
import { useAppStore } from "@/state/appStore";
import { cn, formatDateTime, formatTokens } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

type TraceFilter = "all" | "handoffs" | "llm" | "tools" | "verdicts";

function eventTone(type: TraceEvent["type"]): BadgeTone {
  switch (type) {
    case "handoff":
      return "focus";
    case "tool_call":
      return "tool";
    case "checker_verdict":
      return "warning";
    case "interrupt":
    case "human_decision":
      return "nominal";
    case "error":
      return "critical";
    default:
      return "neutral";
  }
}

export function TracePage() {
  const { runId } = useParams<{ runId: string }>();
  const { activeRunId, setActiveRunId } = useAppStore();
  const effectiveRunId = runId || activeRunId;

  useEffect(() => {
    if (runId && runId !== activeRunId) {
      setActiveRunId(runId);
    }
  }, [runId, activeRunId, setActiveRunId]);

  const [filter, setFilter] = useState<TraceFilter>("all");
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(null);
  const [downloadError, setDownloadError] = useState<unknown>(null);

  const statusQuery = useRunStatus(effectiveRunId);
  const isLive =
    !statusQuery.data ||
    statusQuery.data.status === "queued" ||
    statusQuery.data.status === "running";
  const traceQuery = useRunTrace(effectiveRunId, isLive);

  const events = traceQuery.data?.events ?? [];

  const filteredEvents = useMemo(() => {
    return events.filter((ev) => {
      if (filter === "all") return true;
      if (filter === "handoffs") return ev.type === "handoff";
      if (filter === "llm") return ev.type === "llm_call";
      if (filter === "tools") return ev.type === "tool_call";
      if (filter === "verdicts") {
        return (
          ev.type === "checker_verdict" ||
          ev.type === "interrupt" ||
          ev.type === "human_decision"
        );
      }
      return true;
    });
  }, [events, filter]);

  const handoffCount = events.filter((e) => e.type === "handoff").length;
  const toolCount = events.filter((e) => e.type === "tool_call").length;
  const llmCount = events.filter((e) => e.type === "llm_call").length;

  const handleDownload = async () => {
    setDownloadError(null);
    try {
      await apiClient.downloadTraceJsonl(effectiveRunId);
    } catch (err) {
      setDownloadError(err);
    }
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-muted">
            Step 04 · Agent Interaction Evidence (Competition Rule 02)
          </div>
          <div className="flex flex-wrap items-center gap-2.5 mt-1">
            <h1 className="text-xl font-semibold tracking-tight">Execution Trace & Hand-Offs</h1>
            <Badge tone="focus" mono>
              {effectiveRunId}
            </Badge>
            {statusQuery.data && (
              <Badge tone="neutral" mono>
                {statusQuery.data.bundle_id} · {statusQuery.data.system_id}
              </Badge>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Link to={`/run/${effectiveRunId}/review`}>
            <Button variant="outline" size="md">
              <ClipboardCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
              Plan Review
            </Button>
          </Link>
          <Button variant="primary" size="md" onClick={handleDownload}>
            <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
            Download trace.jsonl
          </Button>
        </div>
      </div>

      {(Boolean(traceQuery.error) || Boolean(downloadError)) && (
        <ErrorBanner
          title="Trace retrieval error"
          error={downloadError || traceQuery.error}
          onRetry={() => traceQuery.refetch()}
        />
      )}

      {/* Summary Bar + Filter Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-hairline bg-surface p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Filter className="h-3.5 w-3.5 text-muted mr-1" strokeWidth={1.5} />
          {(
            [
              { id: "all", label: `All Events (${events.length})` },
              { id: "handoffs", label: `Hand-offs (${handoffCount})` },
              { id: "llm", label: `LLM Calls (${llmCount})` },
              { id: "tools", label: `Code Tools (${toolCount})` },
              { id: "verdicts", label: "Verdicts & Gates" },
            ] as const
          ).map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={cn(
                "px-2.5 py-1 rounded text-xs font-medium transition-colors border",
                filter === f.id
                  ? "bg-status-focus text-white border-status-focus"
                  : "bg-subtle/60 text-muted border-hairline hover:text-ink"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="text-[11px] font-mono text-muted">
          Click any step to open structured JSON payload inspector
        </div>
      </div>

      {/* Vertical Timeline */}
      {traceQuery.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : filteredEvents.length === 0 ? (
        <div className="rounded-md border border-hairline bg-surface p-10 text-center text-xs text-muted">
          No trace events match the selected filter.
        </div>
      ) : (
        <div className="relative pl-6 space-y-2 before:content-[''] before:absolute before:left-2.5 before:top-2 before:bottom-2 before:w-px before:bg-hairline">
          {filteredEvents.map((ev) => {
            const isHighlight =
              ev.type === "handoff" ||
              ev.type === "checker_verdict" ||
              ev.type === "interrupt" ||
              ev.type === "human_decision";

            return (
              <div key={ev.seq} className="relative">
                <span
                  className={cn(
                    "absolute -left-[19px] top-4 h-2.5 w-2.5 rounded-full border-2 border-surface",
                    ev.type === "handoff"
                      ? "bg-status-focus"
                      : ev.type === "tool_call"
                        ? "bg-status-tool"
                        : ev.type === "checker_verdict"
                          ? "bg-status-warning"
                          : ev.type === "interrupt" || ev.type === "human_decision"
                            ? "bg-status-nominal"
                            : "bg-strong"
                  )}
                />

                <button
                  type="button"
                  onClick={() => setSelectedEvent(ev)}
                  className={cn(
                    "w-full text-left rounded-md border p-3.5 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-3",
                    isHighlight
                      ? "bg-surface border-status-focus/40 hover:border-status-focus"
                      : "bg-surface border-hairline hover:border-strong"
                  )}
                >
                  <div className="space-y-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-faint">
                        #{String(ev.seq).padStart(2, "0")}
                      </span>
                      <Badge tone={eventTone(ev.type)} mono>
                        {ev.type === "handoff" && <ArrowRightLeft className="h-3 w-3" />}
                        {ev.type === "tool_call" && <Wrench className="h-3 w-3" />}
                        {ev.type === "checker_verdict" && <ShieldCheck className="h-3 w-3" />}
                        {ev.type}
                      </Badge>

                      {ev.from_agent && ev.to_agent ? (
                        <span className="font-mono text-xs font-semibold text-status-focus">
                          {ev.from_agent} → {ev.to_agent}
                        </span>
                      ) : ev.agent ? (
                        <span className="font-mono text-xs text-muted">[{ev.agent}]</span>
                      ) : null}

                      {ev.model && (
                        <Badge tone="neutral" mono>
                          {ev.model}
                        </Badge>
                      )}
                      {ev.tool && (
                        <Badge tone="tool" mono>
                          {ev.tool}
                        </Badge>
                      )}
                      {ev.item && (
                        <span className="font-mono text-[11px] text-muted">{ev.item}</span>
                      )}
                    </div>

                    <div className="text-xs text-ink font-medium leading-snug">{ev.summary}</div>
                  </div>

                  <div className="flex sm:flex-col items-end justify-between gap-1 shrink-0 font-mono text-[11px] text-muted">
                    <span>{formatDateTime(ev.ts)}</span>
                    <div className="flex items-center gap-2">
                      {ev.input_tokens !== null && (
                        <span>
                          {formatTokens((ev.input_tokens || 0) + (ev.output_tokens || 0))} tok
                        </span>
                      )}
                      {ev.latency_ms !== null && <span>{ev.latency_ms}ms</span>}
                      {ev.cost_usd !== null && ev.cost_usd > 0 && (
                        <span className="text-ink font-semibold">${ev.cost_usd.toFixed(3)}</span>
                      )}
                      <Code2 className="h-3.5 w-3.5 text-status-focus" strokeWidth={1.5} />
                    </div>
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Slide-Over JSON Payload Inspector Drawer */}
      <Drawer
        open={Boolean(selectedEvent)}
        onOpenChange={(open) => {
          if (!open) setSelectedEvent(null);
        }}
        title={
          selectedEvent
            ? `Trace Event #${selectedEvent.seq} · ${selectedEvent.type}`
            : "Trace Event Inspector"
        }
        subtitle={selectedEvent ? `${selectedEvent.run_id} · ${selectedEvent.ts}` : undefined}
      >
        {selectedEvent && (
          <div className="space-y-4 text-xs">
            <div className="rounded border border-hairline bg-subtle/50 p-3 space-y-1">
              <div className="text-[11px] font-semibold uppercase text-muted">Summary</div>
              <p className="text-ink font-medium">{selectedEvent.summary}</p>
            </div>

            <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
              <div className="rounded border border-hairline p-2.5">
                <div className="text-muted">Agent / Route</div>
                <div className="font-semibold text-ink mt-0.5">
                  {selectedEvent.from_agent && selectedEvent.to_agent
                    ? `${selectedEvent.from_agent} → ${selectedEvent.to_agent}`
                    : selectedEvent.agent || "runner"}
                </div>
              </div>
              <div className="rounded border border-hairline p-2.5">
                <div className="text-muted">Model / Tool</div>
                <div className="font-semibold text-ink mt-0.5">
                  {selectedEvent.model || selectedEvent.tool || "—"}
                </div>
              </div>
              <div className="rounded border border-hairline p-2.5">
                <div className="text-muted">Tokens (In / Out / Cache)</div>
                <div className="font-semibold text-ink mt-0.5">
                  {selectedEvent.input_tokens ?? 0} / {selectedEvent.output_tokens ?? 0} /{" "}
                  {selectedEvent.cached_input_tokens ?? 0}
                </div>
              </div>
              <div className="rounded border border-hairline p-2.5">
                <div className="text-muted">Latency / Cost</div>
                <div className="font-semibold text-ink mt-0.5">
                  {selectedEvent.latency_ms !== null ? `${selectedEvent.latency_ms} ms` : "—"} ·{" "}
                  {selectedEvent.cost_usd !== null ? `$${selectedEvent.cost_usd.toFixed(4)}` : "$0"}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-muted">
                <Terminal className="h-3.5 w-3.5" />
                <span>Structured Event Payload</span>
              </div>
              <pre className="rounded-md border border-hairline bg-subtle p-3.5 font-mono text-[11px] text-ink overflow-x-auto leading-relaxed">
                {JSON.stringify(selectedEvent.payload ?? {}, null, 2)}
              </pre>
            </div>

            <div className="space-y-1.5">
              <div className="text-[11px] font-semibold uppercase text-muted">Raw JSONL Record</div>
              <pre className="rounded-md border border-hairline bg-subtle p-3.5 font-mono text-[11px] text-muted overflow-x-auto leading-relaxed">
                {JSON.stringify(selectedEvent, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
