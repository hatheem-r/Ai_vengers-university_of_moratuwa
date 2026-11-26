import { Fragment, useEffect, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  FileSpreadsheet,
  FileText,
  Printer,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useRunPlan, useRunScore, useRunStatus, useSubmitApproval } from "@/api/hooks";
import type { Flag, SourceRef, WorkOrder } from "@/api/types";
import { useAppStore } from "@/state/appStore";
import {
  exportPlanAsExcelCsv,
  exportPlanAsJson,
  exportPlanAsPdf,
} from "@/lib/exportPlan";
import { formatDateTime, formatShortDate } from "@/lib/utils";
import { MachineGantt } from "@/components/gantt/MachineGantt";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

function CitationChip({ src }: { src: SourceRef }) {
  const loc = [
    src.page !== null ? `p.${src.page}` : null,
    src.row !== null ? `row ${src.row}` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-hairline bg-subtle px-2 py-0.5 font-mono text-[11px] text-ink">
      <FileText className="h-3 w-3 text-status-focus shrink-0" strokeWidth={1.5} />
      <span>{src.document}</span>
      <span className="text-muted">· {src.field}</span>
      {src.value !== null && src.value !== undefined && (
        <span className="font-semibold text-status-focus">={String(src.value)}</span>
      )}
      {loc && <span className="text-faint">({loc})</span>}
    </span>
  );
}

export function PlanReviewPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { activeRunId, setActiveRunId } = useAppStore();
  const effectiveRunId = runId || activeRunId;

  useEffect(() => {
    if (runId && runId !== activeRunId) {
      setActiveRunId(runId);
    }
  }, [runId, activeRunId, setActiveRunId]);

  const [selectedVersion, setSelectedVersion] = useState<number | undefined>(undefined);
  const [expandedWoId, setExpandedWoId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [validationMsg, setValidationMsg] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>("gantt");

  const statusQuery = useRunStatus(effectiveRunId);
  const planReady =
    Boolean(statusQuery.data) &&
    (statusQuery.data!.plan_version > 0 ||
      statusQuery.data!.status === "awaiting_approval" ||
      statusQuery.data!.status === "completed");

  const planQuery = useRunPlan(effectiveRunId, selectedVersion, planReady);
  const scoreQuery = useRunScore(effectiveRunId, planReady);
  const approvalMutation = useSubmitApproval(effectiveRunId);

  if (statusQuery.error || planQuery.error) {
    return (
      <div className="space-y-4">
        <ErrorBanner
          title="Unable to load work order plan"
          error={planQuery.error || statusQuery.error}
          onRetry={() => {
            statusQuery.refetch();
            planQuery.refetch();
          }}
        />
        <Button variant="outline" onClick={() => navigate(`/run/${effectiveRunId}`)}>
          Return to Live Run
        </Button>
      </div>
    );
  }

  if (!statusQuery.data || !planReady || !planQuery.data) {
    return (
      <div className="space-y-4">
        <div className="rounded-md border border-hairline bg-surface p-6 text-center space-y-3">
          <div className="text-sm font-semibold">Plan Not Ready Yet</div>
          <p className="text-xs text-muted">
            Run <span className="font-mono">{effectiveRunId}</span> is currently in{" "}
            <span className="font-mono">{statusQuery.data?.status ?? "loading"}</span> state.
          </p>
          <Button variant="primary" onClick={() => navigate(`/run/${effectiveRunId}`)}>
            Watch Live Pipeline →
          </Button>
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const run = statusQuery.data;
  const { plan, plan_version, approval } = planQuery.data;
  const score = scoreQuery.data;

  const lateCount = plan.work_orders.filter((w) => w.is_late).length;
  const onTimeCount = plan.work_orders.length - lateCount;
  const humanFlagsCount = plan.flags.filter((f) => f.needs_human).length;
  const isAwaiting = run.status === "awaiting_approval";

  const handleDecision = async (decision: "approve" | "reject") => {
    setValidationMsg(null);
    if (decision === "reject" && !notes.trim()) {
      setValidationMsg("Planner notes are required when rejecting a plan so the Planner knows how to revise it.");
      return;
    }
    try {
      await approvalMutation.mutateAsync({
        decision,
        notes: notes.trim() || undefined,
      });
      setNotes("");
      if (decision === "reject") {
        navigate(`/run/${run.run_id}`);
      }
    } catch {
      // Displayed via mutation error banner
    }
  };

  const versionOptions = Array.from({ length: run.plan_version }, (_, i) => i + 1);

  const flagsForWo = (wo: WorkOrder): Flag[] =>
    plan.flags.filter(
      (f) =>
        f.message.includes(wo.wo_id) ||
        f.message.includes(wo.customer_po) ||
        plan.shortages.some(
          (s) => s.affected_work_orders.includes(wo.wo_id) && f.message.includes(s.item_code)
        )
    );

  return (
    <div className="space-y-5 pb-24">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-muted">
            Step 03 · Plan Verification & Human Sign-Off
          </div>
          <div className="flex flex-wrap items-center gap-2.5 mt-1">
            <h1 className="text-xl font-semibold tracking-tight font-mono">{plan.bundle_id}</h1>
            <Badge tone="focus" mono>
              {plan.system_id}
            </Badge>
            <Badge
              tone={
                approval.status === "approved"
                  ? "nominal"
                  : approval.status === "rejected"
                    ? "critical"
                    : "warning"
              }
              mono
              className="uppercase"
            >
              {approval.status === "pending" ? "Awaiting Approval" : approval.status}
            </Badge>
            {score && (
              <Badge tone={score.passed ? "nominal" : "critical"} mono>
                PQS {score.pqs.toFixed(1)}/100 · {score.passed ? "PASS" : "FAIL"}
              </Badge>
            )}
          </div>
        </div>

        {/* Version Selector & Export Buttons */}
        <div className="flex flex-wrap items-center gap-2 no-print">
          {versionOptions.length > 1 && (
            <select
              aria-label="Select plan version"
              value={selectedVersion ?? run.plan_version}
              onChange={(e) => setSelectedVersion(Number(e.target.value))}
              className="h-8 rounded border border-hairline bg-surface px-2.5 font-mono text-xs"
            >
              {versionOptions.map((v) => (
                <option key={v} value={v}>
                  Plan v{v} {v === run.plan_version ? "(latest)" : ""}
                </option>
              ))}
            </select>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => exportPlanAsJson(plan, plan_version)}
          >
            <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
            JSON
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => exportPlanAsExcelCsv(plan, plan_version)}
          >
            <FileSpreadsheet className="h-3.5 w-3.5 text-status-nominal" strokeWidth={1.5} />
            Excel (CSV)
          </Button>
          <Button variant="outline" size="sm" onClick={() => exportPlanAsPdf()}>
            <Printer className="h-3.5 w-3.5" strokeWidth={1.5} />
            PDF
          </Button>
        </div>
      </div>

      {approvalMutation.error && (
        <ErrorBanner title="Approval submission failed" error={approvalMutation.error} />
      )}

      {/* KPI Summary Header */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="text-xs text-muted">Total Work Orders</div>
          <div className="font-mono text-xl font-semibold mt-1">{plan.work_orders.length}</div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            start {formatShortDate(plan.planning_start)}
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="text-xs text-muted">On-Time vs. Late</div>
          <div className="font-mono text-xl font-semibold mt-1">
            <span className="text-status-nominal">{onTimeCount}</span>
            <span className="text-faint mx-1">/</span>
            <span className={lateCount > 0 ? "text-status-critical" : "text-muted"}>
              {lateCount} late
            </span>
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">against PO due dates</div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="text-xs text-muted">Material Shortages</div>
          <div
            className={`font-mono text-xl font-semibold mt-1 ${
              plan.shortages.length > 0 ? "text-status-warning" : "text-status-nominal"
            }`}
          >
            {plan.shortages.length}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            {plan.suggested_pos.length} suggested PO(s)
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="text-xs text-muted">Active Risk Flags</div>
          <div
            className={`font-mono text-xl font-semibold mt-1 ${
              humanFlagsCount > 0 ? "text-status-critical" : "text-ink"
            }`}
          >
            {plan.flags.length}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            {humanFlagsCount} require human sign-off
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-3.5">
          <div className="text-xs text-muted">Checker Review</div>
          <div className="font-mono text-xl font-semibold mt-1 uppercase">
            {plan.checker ? (
              <span
                className={
                  plan.checker.verdict === "pass" ? "text-status-nominal" : "text-status-warning"
                }
              >
                {plan.checker.verdict}
              </span>
            ) : (
              <span className="text-muted text-sm">N/A (Baseline)</span>
            )}
          </div>
          <div className="text-[11px] font-mono text-faint mt-0.5">
            {plan.checker ? `${plan.checker.revisions_used}/1 revision used` : "single-agent mode"}
          </div>
        </div>
      </div>

      {/* Planner Rationale & Independent Checker Card */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-8 rounded-md border border-hairline bg-surface p-4 space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted">
            Planner Judgement & Trade-Off Rationale
          </div>
          <p className="text-xs text-ink leading-relaxed">{plan.rationale}</p>
          {plan.assumptions.length > 0 && (
            <div className="pt-2 border-t border-hairline/70 flex flex-wrap gap-1.5">
              {plan.assumptions.map((a, idx) => (
                <span
                  key={idx}
                  className="text-[11px] rounded bg-subtle border border-hairline px-2 py-0.5 text-muted"
                >
                  • {a}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="lg:col-span-4 rounded-md border border-hairline bg-surface p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">
              Checker Audit
            </span>
            {plan.checker ? (
              <Badge tone={plan.checker.verdict === "pass" ? "nominal" : "warning"} mono>
                <ShieldCheck className="h-3 w-3" />
                {plan.checker.verdict.toUpperCase()}
              </Badge>
            ) : (
              <Badge tone="neutral" mono>No Checker</Badge>
            )}
          </div>
          {plan.checker ? (
            <div className="space-y-1.5 text-xs">
              {plan.checker.issues.map((iss, i) => (
                <div key={i} className="text-muted">
                  <span className="font-mono text-status-nominal">✓ Caught in v1:</span> {iss}
                </div>
              ))}
              {plan.checker.open_issues.map((open, i) => (
                <div key={i} className="text-status-warning font-medium">
                  ⚑ Open for human: {open}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted">
              Single-agent baseline omits the independent Checker node; self-checked in one context.
            </p>
          )}
        </div>
      </div>

      {/* Tabbed Workspace: Gantt, Work Orders, Materials & Shortages, Flags & Risk Register */}
      <Tabs.Root value={activeTab} onValueChange={setActiveTab} className="space-y-3">
        <Tabs.List className="flex flex-wrap gap-1 border-b border-hairline">
          {[
            { id: "gantt", label: "Machine Gantt Chart", count: null },
            { id: "wos", label: "Work Orders", count: plan.work_orders.length },
            {
              id: "materials",
              label: "Materials & Shortages",
              count: plan.shortages.length,
            },
            { id: "flags", label: "Flags & Risk Register", count: plan.flags.length },
          ].map((t) => (
            <Tabs.Trigger
              key={t.id}
              value={t.id}
              className="px-3.5 py-2 text-xs font-medium text-muted data-[state=active]:text-status-focus data-[state=active]:border-b-2 data-[state=active]:border-status-focus transition-colors flex items-center gap-1.5 -mb-px"
            >
              <span>{t.label}</span>
              {t.count !== null && (
                <span className="font-mono text-[10px] rounded bg-subtle border border-hairline px-1.5 py-0.2">
                  {t.count}
                </span>
              )}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        {/* TAB 1: Machine Gantt Chart */}
        <Tabs.Content value="gantt" className="focus:outline-none">
          <div className="rounded-md border border-hairline bg-surface p-4">
            <MachineGantt plan={plan} />
          </div>
        </Tabs.Content>

        {/* TAB 2: Work Orders Table with Expandable Reason & Source Citations */}
        <Tabs.Content value="wos" className="focus:outline-none">
          <div className="rounded-md border border-hairline bg-surface overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-hairline bg-subtle/60 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th className="py-2.5 px-3 w-8" />
                  <th className="py-2.5 px-3">WO ID</th>
                  <th className="py-2.5 px-3">Customer PO</th>
                  <th className="py-2.5 px-3">Item & Variant</th>
                  <th className="py-2.5 px-3 text-right">Quantity</th>
                  <th className="py-2.5 px-3">Priority</th>
                  <th className="py-2.5 px-3">Planned Start</th>
                  <th className="py-2.5 px-3">Planned Finish vs. Due</th>
                  <th className="py-2.5 px-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {plan.work_orders.map((wo) => {
                  const isOpen = expandedWoId === wo.wo_id;
                  const relatedFlags = flagsForWo(wo);
                  return (
                    <Fragment key={wo.wo_id}>
                      <tr
                        onClick={() => setExpandedWoId(isOpen ? null : wo.wo_id)}
                        className="hover:bg-subtle/40 cursor-pointer transition-colors"
                      >
                        <td className="py-2.5 px-3 text-muted">
                          {isOpen ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                        </td>
                        <td className="py-2.5 px-3 font-mono font-semibold">{wo.wo_id}</td>
                        <td className="py-2.5 px-3 font-mono text-status-focus">{wo.customer_po}</td>
                        <td className="py-2.5 px-3">
                          <span className="font-mono font-medium">{wo.item_code}</span>
                          {wo.variant && (
                            <span className="text-muted ml-1.5">
                              ({[wo.variant.colour, wo.variant.size].filter(Boolean).join(" / ")})
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 text-right font-mono">
                          {wo.qty.toLocaleString()} {wo.uom}
                        </td>
                        <td className="py-2.5 px-3 font-mono">P{wo.priority}</td>
                        <td className="py-2.5 px-3 font-mono text-muted">
                          {formatDateTime(wo.planned_start)}
                        </td>
                        <td className="py-2.5 px-3 font-mono">
                          <span className={wo.is_late ? "text-status-critical font-semibold" : "text-ink"}>
                            {formatShortDate(wo.planned_finish)}
                          </span>
                          <span className="text-muted"> / due {formatShortDate(wo.due_date)}</span>
                        </td>
                        <td className="py-2.5 px-3">
                          {wo.is_late ? (
                            <Badge tone="critical" mono>
                              +{wo.days_late}d LATE
                            </Badge>
                          ) : (
                            <Badge tone="nominal" mono>
                              ON TIME
                            </Badge>
                          )}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-subtle/30">
                          <td colSpan={9} className="p-4 space-y-3">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              <div className="space-y-1.5">
                                <div className="text-[11px] font-semibold uppercase text-muted">
                                  Routing Operations ({wo.operations.length})
                                </div>
                                <div className="space-y-1 font-mono text-[11px]">
                                  {wo.operations.map((op) => (
                                    <div
                                      key={op.seq}
                                      className="flex items-center justify-between rounded border border-hairline bg-surface px-2.5 py-1.5"
                                    >
                                      <span>
                                        #{op.seq} {op.operation} · <strong>{op.machine_id}</strong>
                                      </span>
                                      <span className="text-muted">
                                        {formatShortDate(op.start)} → {formatShortDate(op.end)}
                                        {op.uses_overtime ? " (OT)" : ""}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                              <div className="space-y-2">
                                <div className="text-[11px] font-semibold uppercase text-muted">
                                  Reason & Source Document Citations
                                </div>
                                <p className="text-xs text-muted leading-relaxed">{plan.rationale}</p>
                                {relatedFlags.length > 0 && (
                                  <div className="flex flex-wrap gap-1.5">
                                    {relatedFlags
                                      .flatMap((f) => f.sources)
                                      .map((src, i) => (
                                        <CitationChip key={i} src={src} />
                                      ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Tabs.Content>

        {/* TAB 3: Materials, Shortages & Suggested POs */}
        <Tabs.Content value="materials" className="focus:outline-none space-y-4">
          {/* Suggested Supplier POs */}
          {plan.suggested_pos.length > 0 && (
            <div className="rounded-md border border-status-warning/40 bg-status-warning/[0.04] p-4 space-y-2.5">
              <div className="text-xs font-semibold uppercase tracking-wider text-status-warning">
                Suggested Supplier Purchase Orders ({plan.suggested_pos.length})
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {plan.suggested_pos.map((po, i) => (
                  <div
                    key={i}
                    className="rounded border border-hairline bg-surface p-3 space-y-1.5 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-semibold text-ink">
                        {po.item_code} · {po.qty.toLocaleString()} {po.uom}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {po.supplier_id && (
                          <Badge tone="focus" mono>
                            {po.supplier_id}
                          </Badge>
                        )}
                        {po.expedite && (
                          <Badge tone="warning" mono>
                            EXPEDITE
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="font-mono text-[11px] text-muted">
                      Need by: <strong className="text-ink">{formatShortDate(po.need_by)}</strong>
                    </div>
                    <p className="text-muted text-[11px]">{po.reason}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Exact MRP Netting Table */}
          <div className="rounded-md border border-hairline bg-surface overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-hairline bg-subtle/60 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  <th className="py-2.5 px-3">Component Code</th>
                  <th className="py-2.5 px-3 text-right">Required (incl. Wastage)</th>
                  <th className="py-2.5 px-3 text-right">On Hand</th>
                  <th className="py-2.5 px-3 text-right">Reserved</th>
                  <th className="py-2.5 px-3 text-right">Open POs</th>
                  <th className="py-2.5 px-3 text-right">Net Shortage</th>
                  <th className="py-2.5 px-3">Work Orders</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline font-mono">
                {plan.material_requirements.map((m) => (
                  <tr key={m.item_code} className="hover:bg-subtle/40">
                    <td className="py-2.5 px-3 font-semibold">{m.item_code}</td>
                    <td className="py-2.5 px-3 text-right">
                      {m.required_qty.toLocaleString()} {m.uom}
                    </td>
                    <td className="py-2.5 px-3 text-right">{m.on_hand_qty.toLocaleString()}</td>
                    <td className="py-2.5 px-3 text-right text-muted">
                      {m.reserved_qty.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right text-status-tool">
                      {m.on_order_qty.toLocaleString()}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      {m.net_shortage_qty > 0 ? (
                        <Badge tone="critical" mono>
                          -{m.net_shortage_qty.toLocaleString()} {m.uom}
                        </Badge>
                      ) : (
                        <Badge tone="nominal" mono>
                          0 {m.uom}
                        </Badge>
                      )}
                    </td>
                    <td className="py-2.5 px-3">{m.for_work_orders.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Tabs.Content>

        {/* TAB 4: Flags & Risk Register */}
        <Tabs.Content value="flags" className="focus:outline-none space-y-3">
          {plan.flags.length === 0 ? (
            <div className="rounded-md border border-hairline bg-surface p-8 text-center text-xs text-muted">
              No conflicts, missing fields, or policy breaches flagged on this plan.
            </div>
          ) : (
            plan.flags.map((flag) => (
              <div
                key={flag.flag_id}
                className={`rounded-md border bg-surface p-4 space-y-2.5 ${
                  flag.severity === "high"
                    ? "border-status-critical/45"
                    : flag.severity === "medium"
                      ? "border-status-warning/45"
                      : "border-hairline"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-semibold">{flag.flag_id}</span>
                    <Badge
                      tone={
                        flag.severity === "high"
                          ? "critical"
                          : flag.severity === "medium"
                            ? "warning"
                            : "neutral"
                      }
                      mono
                      className="uppercase"
                    >
                      {flag.type.replace(/_/g, " ")} · {flag.severity}
                    </Badge>
                    {flag.needs_human && (
                      <Badge tone="warning">
                        <ShieldAlert className="h-3 w-3" />
                        System Did Not Guess — Escalated to Human
                      </Badge>
                    )}
                  </div>
                </div>

                <p className="text-xs text-ink font-medium leading-relaxed">{flag.message}</p>

                {flag.sources.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-muted mr-1">Sources:</span>
                    {flag.sources.map((src, i) => (
                      <CitationChip key={i} src={src} />
                    ))}
                  </div>
                )}

                {flag.resolution && (
                  <div className="text-[11px] text-status-nominal font-medium pt-1 border-t border-hairline/60">
                    Resolution: {flag.resolution}
                  </div>
                )}
              </div>
            ))
          )}
        </Tabs.Content>
      </Tabs.Root>

      {/* Sticky Human-in-the-Loop Action Bar */}
      <div className="fixed bottom-0 left-0 right-0 z-30 border-t border-hairline bg-surface/95 backdrop-blur px-5 py-3 no-print">
        <div className="max-w-[1480px] mx-auto flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
          <div className="flex-1 flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={notes}
                disabled={!isAwaiting || approvalMutation.isPending}
                onChange={(e) => {
                  setNotes(e.target.value);
                  if (validationMsg) setValidationMsg(null);
                }}
                placeholder={
                  isAwaiting
                    ? "Enter sign-off notes, or binding re-plan constraints if rejecting (e.g., 'Do not use overtime on Line 2')..."
                    : `Decision recorded: ${approval.status.toUpperCase()}${
                        approval.notes ? ` — "${approval.notes}"` : ""
                      }`
                }
                className="w-full h-9 px-3 rounded border border-hairline bg-subtle/60 text-xs text-ink placeholder:text-faint focus:outline-none focus:border-status-focus disabled:opacity-70"
              />
            </div>
            {validationMsg && (
              <span className="text-[11px] text-status-critical flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" /> {validationMsg}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <Button
              variant="danger"
              size="md"
              disabled={!isAwaiting || approvalMutation.isPending}
              onClick={() => handleDecision("reject")}
            >
              <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
              Reject Plan
            </Button>
            <Button
              variant="nominal"
              size="md"
              disabled={!isAwaiting || approvalMutation.isPending}
              onClick={() => handleDecision("approve")}
            >
              <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.75} />
              {approval.status === "approved" ? "Plan Approved" : "Approve Plan"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
