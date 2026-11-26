import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  GitCompare,
  Play,
  XCircle,
} from "lucide-react";
import {
  useRunPlan,
  useRunScore,
  useRunsList,
  useScenarios,
  useStartRun,
} from "@/api/hooks";
import type { RunSummaryItem } from "@/api/types";
import { useAppStore } from "@/state/appStore";
import { formatDuration, formatTokens, formatUsd } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

export function ComparePage() {
  const navigate = useNavigate();
  const { setActiveRunId } = useAppStore();

  const scenariosQuery = useScenarios("all");
  const allRunsQuery = useRunsList(undefined, 50);
  const startRunMutation = useStartRun();

  const [selectedBundle, setSelectedBundle] = useState<string>("A_S3_typical");
  const [multiRunId, setMultiRunId] = useState<string>("");
  const [baseRunId, setBaseRunId] = useState<string>("");

  const bundleRuns = useMemo(() => {
    const runs = allRunsQuery.data?.runs ?? [];
    return runs.filter((r) => r.bundle_id === selectedBundle);
  }, [allRunsQuery.data, selectedBundle]);

  const multiCandidates = useMemo(
    () => bundleRuns.filter((r) => r.mode === "multi_agent"),
    [bundleRuns]
  );
  const baseCandidates = useMemo(
    () => bundleRuns.filter((r) => r.mode === "baseline"),
    [bundleRuns]
  );

  useEffect(() => {
    setMultiRunId(multiCandidates[0]?.run_id ?? "");
    setBaseRunId(baseCandidates[0]?.run_id ?? "");
  }, [selectedBundle, multiCandidates, baseCandidates]);

  const multiRun: RunSummaryItem | undefined = multiCandidates.find((r) => r.run_id === multiRunId);
  const baseRun: RunSummaryItem | undefined = baseCandidates.find((r) => r.run_id === baseRunId);

  const multiPlanQuery = useRunPlan(multiRun?.run_id, undefined, Boolean(multiRun && multiRun.plan_version > 0));
  const basePlanQuery = useRunPlan(baseRun?.run_id, undefined, Boolean(baseRun && baseRun.plan_version > 0));

  const multiScoreQuery = useRunScore(multiRun?.run_id, Boolean(multiRun && multiRun.plan_version > 0));
  const baseScoreQuery = useRunScore(baseRun?.run_id, Boolean(baseRun && baseRun.plan_version > 0));

  const launchMissing = async (mode: "multi_agent" | "baseline") => {
    const res = await startRunMutation.mutateAsync({
      bundle_id: selectedBundle,
      mode,
      model_profile: mode === "multi_agent" ? "mix" : "sonnet",
      auto_approve: false,
    });
    setActiveRunId(res.run_id);
    navigate(`/run/${res.run_id}`);
  };

  const bundleOptions = useMemo(() => {
    const fromRuns = new Set((allRunsQuery.data?.runs ?? []).map((r) => r.bundle_id));
    const fromScenarios = (scenariosQuery.data?.scenarios ?? []).map((s) => s.bundle_id);
    return Array.from(new Set(["A_S3_typical", ...fromRuns, ...fromScenarios]));
  }, [allRunsQuery.data, scenariosQuery.data]);

  const mScore = multiScoreQuery.data;
  const bScore = baseScoreQuery.data;
  const mPlan = multiPlanQuery.data?.plan;
  const bPlan = basePlanQuery.data?.plan;

  const pqsDelta = mScore && bScore ? mScore.pqs - bScore.pqs : null;
  const costDeltaPct =
    multiRun && baseRun && baseRun.metrics.cost_usd > 0
      ? ((multiRun.metrics.cost_usd - baseRun.metrics.cost_usd) / baseRun.metrics.cost_usd) * 100
      : null;
  const latencyDeltaPct =
    multiRun && baseRun && baseRun.metrics.elapsed_s > 0
      ? ((multiRun.metrics.elapsed_s - baseRun.metrics.elapsed_s) / baseRun.metrics.elapsed_s) * 100
      : null;

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-muted">
            Step 05 · Architectural Isolation & Head-to-Head Diff
          </div>
          <h1 className="text-xl font-semibold tracking-tight mt-0.5 flex items-center gap-2">
            <GitCompare className="h-5 w-5 text-status-focus" strokeWidth={1.5} />
            Multi-Agent vs. Single-Agent Baseline
          </h1>
          <p className="text-xs text-muted mt-1">
            Compare runs on the exact same document bundle, tools, output schema, and runtime budgets.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-muted font-medium">Bundle:</label>
          <select
            value={selectedBundle}
            onChange={(e) => setSelectedBundle(e.target.value)}
            className="h-8 rounded border border-hairline bg-surface px-2.5 font-mono text-xs text-ink"
          >
            {bundleOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </div>
      </div>

      {allRunsQuery.error && (
        <ErrorBanner error={allRunsQuery.error} onRetry={() => allRunsQuery.refetch()} />
      )}

      {allRunsQuery.isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <>
          {/* Run Selector Headers */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Multi-Agent Column Selector */}
            <div className="rounded-md border border-status-focus/40 bg-surface p-4 space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge tone="focus" mono>
                    {multiRun?.system_id ?? "M-mix / M-S"}
                  </Badge>
                  <span className="text-sm font-semibold">Multi-Agent LangGraph</span>
                </div>
                {multiCandidates.length > 0 && (
                  <select
                    value={multiRunId}
                    onChange={(e) => setMultiRunId(e.target.value)}
                    className="h-7 rounded border border-hairline bg-subtle px-2 font-mono text-[11px]"
                  >
                    {multiCandidates.map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        {r.system_id} · {r.run_id.slice(-8)} ({r.status})
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {!multiRun ? (
                <div className="flex items-center justify-between pt-1">
                  <span className="text-xs text-muted">No multi-agent run recorded on {selectedBundle} yet.</span>
                  <Button size="sm" variant="primary" onClick={() => launchMissing("multi_agent")}>
                    <Play className="h-3 w-3 fill-current" /> Run Multi-Agent
                  </Button>
                </div>
              ) : (
                <div className="text-[11px] font-mono text-muted">
                  Run ID: {multiRun.run_id} · Plan v{multiRun.plan_version} · Checker rev:{" "}
                  {multiRun.revisions.checker}/1
                </div>
              )}
            </div>

            {/* Baseline Column Selector */}
            <div className="rounded-md border border-hairline bg-surface p-4 space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge tone="neutral" mono>
                    B
                  </Badge>
                  <span className="text-sm font-semibold">Single-Agent Baseline</span>
                </div>
                {baseCandidates.length > 0 && (
                  <select
                    value={baseRunId}
                    onChange={(e) => setBaseRunId(e.target.value)}
                    className="h-7 rounded border border-hairline bg-subtle px-2 font-mono text-[11px]"
                  >
                    {baseCandidates.map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        B · {r.run_id.slice(-8)} ({r.status})
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {!baseRun ? (
                <div className="flex items-center justify-between pt-1">
                  <span className="text-xs text-muted">No baseline run recorded on {selectedBundle} yet.</span>
                  <Button size="sm" variant="outline" onClick={() => launchMissing("baseline")}>
                    <Play className="h-3 w-3" /> Run Baseline
                  </Button>
                </div>
              ) : (
                <div className="text-[11px] font-mono text-muted">
                  Run ID: {baseRun.run_id} · Plan v{baseRun.plan_version} · Single context window
                </div>
              )}
            </div>
          </div>

          {/* Synchronized Comparison Matrix */}
          {multiRun && baseRun && (
            <div className="rounded-md border border-hairline bg-surface overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-hairline bg-subtle/60 text-[11px] font-semibold uppercase tracking-wider text-muted">
                    <th className="py-2.5 px-4">Metric</th>
                    <th className="py-2.5 px-4">Multi-Agent ({multiRun.system_id})</th>
                    <th className="py-2.5 px-4">Single-Agent (B)</th>
                    <th className="py-2.5 px-4">Multi-Agent Delta</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline font-mono">
                  <tr>
                    <td className="py-3 px-4 font-sans font-medium">Plan Quality Score (PQS)</td>
                    <td className="py-3 px-4 font-semibold text-sm">
                      {mScore ? (
                        <span className="inline-flex items-center gap-2">
                          <span>{mScore.pqs.toFixed(1)}</span>
                          <Badge tone={mScore.passed ? "nominal" : "critical"} mono>
                            {mScore.passed ? "PASS" : "FAIL"}
                          </Badge>
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-3 px-4 font-semibold text-sm">
                      {bScore ? (
                        <span className="inline-flex items-center gap-2">
                          <span>{bScore.pqs.toFixed(1)}</span>
                          <Badge tone={bScore.passed ? "nominal" : "critical"} mono>
                            {bScore.passed ? "PASS" : "FAIL"}
                          </Badge>
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-3 px-4">
                      {pqsDelta !== null ? (
                        <Badge tone={pqsDelta >= 0 ? "nominal" : "critical"} mono>
                          {pqsDelta >= 0 ? (
                            <ArrowUpRight className="h-3 w-3" />
                          ) : (
                            <ArrowDownRight className="h-3 w-3" />
                          )}
                          {pqsDelta >= 0 ? `+${pqsDelta.toFixed(1)}` : pqsDelta.toFixed(1)} pts
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">Flags Caught (Q7 Recall)</td>
                    <td className="py-2.5 px-4">
                      {mScore ? `${Math.round(mScore.metrics.q7_flag_recall * 100)}%` : "—"} (
                      {mPlan?.flags.length ?? 0} raised)
                    </td>
                    <td className="py-2.5 px-4">
                      {bScore ? `${Math.round(bScore.metrics.q7_flag_recall * 100)}%` : "—"} (
                      {bPlan?.flags.length ?? 0} raised)
                    </td>
                    <td className="py-2.5 px-4 text-status-nominal">
                      {mScore && bScore
                        ? `${Math.round((mScore.metrics.q7_flag_recall - bScore.metrics.q7_flag_recall) * 100)}% recall`
                        : "—"}
                    </td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">Schedule Violations (Q5)</td>
                    <td className="py-2.5 px-4">
                      {mScore ? (
                        mScore.schedule_violations.length === 0 ? (
                          <span className="text-status-nominal inline-flex items-center gap-1">
                            <CheckCircle2 className="h-3.5 w-3.5" /> 0 violations
                          </span>
                        ) : (
                          <span className="text-status-critical">
                            {mScore.schedule_violations.length} violation(s)
                          </span>
                        )
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-2.5 px-4">
                      {bScore ? (
                        bScore.schedule_violations.length === 0 ? (
                          <span className="text-status-nominal">0 violations</span>
                        ) : (
                          <span className="text-status-critical inline-flex items-center gap-1">
                            <XCircle className="h-3.5 w-3.5" /> {bScore.schedule_violations.length}{" "}
                            violation(s)
                          </span>
                        )
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-muted">
                      {mScore && bScore
                        ? `${mScore.schedule_violations.length - bScore.schedule_violations.length} violations`
                        : "—"}
                    </td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">Shortages Missed (1 − Q3 Recall)</td>
                    <td className="py-2.5 px-4">
                      {mScore ? `${Math.round((1 - mScore.metrics.q3_shortage_recall) * 100)}%` : "—"}
                    </td>
                    <td className="py-2.5 px-4">
                      {bScore ? `${Math.round((1 - bScore.metrics.q3_shortage_recall) * 100)}%` : "—"}
                    </td>
                    <td className="py-2.5 px-4 text-status-nominal">Exact mrp_tool parity</td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">Wall-Clock Latency</td>
                    <td className="py-2.5 px-4">{formatDuration(multiRun.metrics.elapsed_s)}</td>
                    <td className="py-2.5 px-4">{formatDuration(baseRun.metrics.elapsed_s)}</td>
                    <td className="py-2.5 px-4">
                      {latencyDeltaPct !== null && (
                        <Badge tone={latencyDeltaPct <= 0 ? "nominal" : "warning"} mono>
                          {latencyDeltaPct <= 0 ? "" : "+"}
                          {latencyDeltaPct.toFixed(0)}%
                        </Badge>
                      )}
                    </td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">Total Tokens Processed</td>
                    <td className="py-2.5 px-4">
                      {formatTokens(
                        multiRun.metrics.input_tokens + multiRun.metrics.output_tokens
                      )}
                    </td>
                    <td className="py-2.5 px-4">
                      {formatTokens(baseRun.metrics.input_tokens + baseRun.metrics.output_tokens)}
                    </td>
                    <td className="py-2.5 px-4 text-status-nominal">
                      Focused schema hand-off
                    </td>
                  </tr>

                  <tr>
                    <td className="py-2.5 px-4 font-sans font-medium">API Cost per Run ($)</td>
                    <td className="py-2.5 px-4 font-semibold">
                      {formatUsd(multiRun.metrics.cost_usd)}
                    </td>
                    <td className="py-2.5 px-4 font-semibold">
                      {formatUsd(baseRun.metrics.cost_usd)}
                    </td>
                    <td className="py-2.5 px-4">
                      {costDeltaPct !== null && (
                        <Badge tone={costDeltaPct <= 0 ? "nominal" : "warning"} mono>
                          {costDeltaPct <= 0 ? "" : "+"}
                          {costDeltaPct.toFixed(0)}% cost
                        </Badge>
                      )}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Side-by-Side Flags & Rationale Diff */}
          {mPlan && bPlan && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded-md border border-hairline bg-surface p-4 space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-status-focus">
                  {mPlan.system_id} · Flags & Checker Audit ({mPlan.flags.length})
                </div>
                <p className="text-xs text-muted leading-relaxed">{mPlan.rationale}</p>
                <div className="space-y-2">
                  {mPlan.flags.map((f) => (
                    <div
                      key={f.flag_id}
                      className="rounded border border-hairline bg-subtle/40 p-2.5 text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between font-mono">
                        <span className="font-semibold">
                          {f.flag_id} · {f.type}
                        </span>
                        <Badge tone={f.severity === "high" ? "critical" : "warning"} mono>
                          {f.severity}
                        </Badge>
                      </div>
                      <p className="text-muted text-[11px]">{f.message}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-md border border-hairline bg-surface p-4 space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-muted">
                  Baseline B · Flags & Single-Context Output ({bPlan.flags.length})
                </div>
                <p className="text-xs text-muted leading-relaxed">{bPlan.rationale}</p>
                {bScore && bScore.schedule_violations.length > 0 && (
                  <div className="rounded border border-status-critical/40 bg-status-critical/5 p-2.5 text-xs text-status-critical space-y-1">
                    <div className="font-semibold">Uncaught Schedule Violation:</div>
                    {bScore.schedule_violations.map((v, i) => (
                      <div key={i} className="text-[11px] font-mono">
                        • {v}
                      </div>
                    ))}
                  </div>
                )}
                <div className="space-y-2">
                  {bPlan.flags.map((f) => (
                    <div
                      key={f.flag_id}
                      className="rounded border border-hairline bg-subtle/40 p-2.5 text-xs space-y-1"
                    >
                      <div className="flex items-center justify-between font-mono">
                        <span className="font-semibold">
                          {f.flag_id} · {f.type}
                        </span>
                        <Badge tone={f.severity === "high" ? "critical" : "warning"} mono>
                          {f.severity}
                        </Badge>
                      </div>
                      <p className="text-muted text-[11px]">{f.message}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
