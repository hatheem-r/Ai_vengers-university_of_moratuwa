import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AlertTriangle, Award, Info, TrendingUp, Users } from "lucide-react";
import { useEvaluationSummary } from "@/api/hooks";
import { formatDuration, formatTokens, formatUsd } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

const COLORS = {
  MS: "#2563EB", // Industrial Blue
  MMIX: "#059669", // Nominal Emerald
  BASE: "#D97706", // Warning Amber
  HUMAN: "#0D9488", // Tool Teal
};

const SCENARIO_LABELS: Record<string, string> = {
  S1: "S1 Normal",
  S2: "S2 Rush",
  S3: "S3 Shortage",
  S4: "S4 Breakdown",
  S5: "S5 Delay",
  S6: "S6 Conflict",
  S7: "S7 Overload",
  S8: "S8 Combined",
  S9: "S9 Compliance",
};

export function EvaluationPage() {
  const evalQuery = useEvaluationSummary("latest");
  const summary = evalQuery.data;

  const scenarioChartData = useMemo(() => {
    if (!summary) return [];
    const scenarios = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"];
    return scenarios.map((sc) => {
      const row: Record<string, string | number> = {
        scenario: SCENARIO_LABELS[sc] || sc,
      };
      for (const item of summary.by_scenario) {
        if (item.scenario === sc) {
          row[item.system_id] = item.pqs_mean;
        }
      }
      return row;
    });
  }, [summary]);

  const messChartData = useMemo(() => {
    if (!summary) return [];
    return (["clean", "typical", "messy"] as const).map((lvl) => {
      const row: Record<string, string | number> = { mess_level: lvl.toUpperCase() };
      for (const item of summary.by_mess_level) {
        if (item.mess_level === lvl) {
          row[item.system_id] = item.pqs_mean;
          row[`${item.system_id}_q1`] = Math.round(item.q1 * 100);
        }
      }
      return row;
    });
  }, [summary]);

  const scalabilityChartData = useMemo(() => {
    if (!summary) return [];
    return [1, 3, 5].map((orders) => {
      const row: Record<string, string | number> = { orders: `${orders} Order(s)` };
      for (const item of summary.scalability) {
        if (item.n_orders === orders) {
          row[`${item.system_id}_latency`] = item.latency_median_s;
          row[`${item.system_id}_cost`] = item.cost_mean_usd;
          row[`${item.system_id}_tokens`] = Math.round(item.tokens_mean / 1000);
        }
      }
      return row;
    });
  }, [summary]);

  if (evalQuery.error) {
    return (
      <ErrorBanner
        title="Unable to load benchmark evaluation summary"
        error={evalQuery.error}
        onRetry={() => evalQuery.refetch()}
      />
    );
  }

  if (!summary) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-muted">
            Step 06 · Empirical Benchmark & Human Baseline
          </div>
          <div className="flex flex-wrap items-center gap-2.5 mt-1">
            <h1 className="text-xl font-semibold tracking-tight">
              Benchmark Evaluation Dashboard
            </h1>
            <Badge tone="focus" mono>
              {summary.eval_id}
            </Badge>
            {summary.is_mock && (
              <Badge tone="mock">
                <AlertTriangle className="h-3 w-3" />
                MOCK SAMPLE SUMMARY
              </Badge>
            )}
          </div>
        </div>
        <div className="text-xs font-mono text-muted">
          {summary.config.bundles} bundles · Intake mix: {summary.config.intake_model_mix}
        </div>
      </div>

      {/* Transparency Banner when Mock */}
      {summary.is_mock && (
        <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3.5 text-xs flex items-start gap-2.5">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div className="text-ink">
            <strong>Mock Preview Notice (Competition Rule 05):</strong> Backend is currently serving{" "}
            <span className="font-mono">evaluation_summary.json</span> with{" "}
            <span className="font-mono">is_mock: true</span>. Numbers below illustrate dashboard layout only and will
            be replaced automatically by <span className="font-mono">results/&lt;eval_id&gt;/summary.json</span> once{" "}
            <span className="font-mono">evaluation/run_benchmark.py</span> completes.
          </div>
        </div>
      )}

      {/* 1. Executive System Leaderboard Table */}
      <div className="rounded-md border border-hairline bg-surface overflow-hidden">
        <div className="px-4 py-3 border-b border-hairline bg-subtle/40 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider">
            <Award className="h-4 w-4 text-status-focus" strokeWidth={1.5} />
            <span>Overall Performance by System</span>
          </div>
          <span className="text-[11px] font-mono text-muted">
            Pass gate: PQS ≥ 80, 0 schedule violations, 0 missed shortages
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-hairline bg-subtle/30 text-[11px] font-semibold uppercase tracking-wider text-muted">
                <th className="py-2.5 px-4">System</th>
                <th className="py-2.5 px-4 text-right">Runs</th>
                <th className="py-2.5 px-4 text-right">Mean PQS (±SD)</th>
                <th className="py-2.5 px-4 text-right">Bundle Pass Rate</th>
                <th className="py-2.5 px-4 text-right">Q7 Flag Recall</th>
                <th className="py-2.5 px-4 text-right">Median Latency</th>
                <th className="py-2.5 px-4 text-right">Mean Tokens</th>
                <th className="py-2.5 px-4 text-right">Mean Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline font-mono">
              {summary.overall.map((row) => {
                const sysInfo = summary.systems.find((s) => s.system_id === row.system_id);
                return (
                  <tr key={row.system_id} className="hover:bg-subtle/40">
                    <td className="py-3 px-4 font-sans">
                      <div className="font-semibold text-ink">{row.system_id}</div>
                      <div className="text-[11px] text-muted">{sysInfo?.label}</div>
                    </td>
                    <td className="py-3 px-4 text-right">{row.n_runs}</td>
                    <td className="py-3 px-4 text-right font-semibold text-ink">
                      {row.pqs_mean.toFixed(1)}{" "}
                      <span className="text-muted font-normal">±{row.pqs_std.toFixed(1)}</span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <Badge tone={row.pass_rate >= 0.8 ? "nominal" : "warning"} mono>
                        {(row.pass_rate * 100).toFixed(1)}%
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-right">{(row.metrics.q7 * 100).toFixed(0)}%</td>
                    <td className="py-3 px-4 text-right">{formatDuration(row.latency_median_s)}</td>
                    <td className="py-3 px-4 text-right">{formatTokens(row.tokens_mean)}</td>
                    <td className="py-3 px-4 text-right font-semibold">
                      {formatUsd(row.cost_mean_usd)}
                    </td>
                  </tr>
                );
              })}

              {/* Human Baseline Summary Row */}
              <tr className="bg-subtle/25">
                <td className="py-3 px-4 font-sans">
                  <div className="font-semibold text-ink">H (Human Baseline)</div>
                  <div className="text-[11px] text-muted">
                    Manual Excel + calculator ({summary.human.participants} planners ×{" "}
                    {summary.human.bundles} bundles)
                  </div>
                </td>
                <td className="py-3 px-4 text-right">{summary.human.results.length}</td>
                <td className="py-3 px-4 text-right font-semibold">
                  {summary.human.summary.pqs_mean.toFixed(1)}
                </td>
                <td className="py-3 px-4 text-right text-muted">—</td>
                <td className="py-3 px-4 text-right text-muted">—</td>
                <td className="py-3 px-4 text-right">
                  {summary.human.summary.minutes_mean.toFixed(1)} min
                </td>
                <td className="py-3 px-4 text-right text-muted">0</td>
                <td className="py-3 px-4 text-right text-muted">
                  {summary.human.hourly_rate_lkr
                    ? `LKR ${Math.round(
                        (summary.human.summary.minutes_mean / 60) * summary.human.hourly_rate_lkr
                      )}`
                    : "—"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Paired Win / Tie / Loss Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
        {summary.paired.map((pair) => (
          <div
            key={`${pair.a}-${pair.b}`}
            className="rounded-md border border-hairline bg-surface p-4 space-y-1.5"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs font-semibold">
                {pair.a} vs. {pair.b}
              </span>
              <Badge tone={pair.mean_pqs_diff >= 0 ? "nominal" : "warning"} mono>
                {pair.mean_pqs_diff >= 0 ? `+${pair.mean_pqs_diff.toFixed(1)}` : pair.mean_pqs_diff.toFixed(1)}{" "}
                PQS
              </Badge>
            </div>
            <div className="font-mono text-sm text-ink">
              <span className="text-status-nominal font-semibold">{pair.wins}W</span> ·{" "}
              <span className="text-muted">{pair.ties}T</span> ·{" "}
              <span className="text-status-critical">{pair.losses}L</span>{" "}
              <span className="text-xs text-muted">({pair.n_bundles} bundles)</span>
            </div>
            <div className="text-[11px] font-mono text-faint">
              Wilcoxon p = {pair.wilcoxon_p !== null ? pair.wilcoxon_p.toFixed(4) : "n/a"} (directional)
            </div>
          </div>
        ))}
      </div>

      {/* 2. By Scenario (S1–S9) Grouped Bar Chart */}
      <div className="rounded-md border border-hairline bg-surface p-4 space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Plan Quality Score by Scenario (S1–S9)</h2>
          <p className="text-xs text-muted">
            Largest multi-agent separation occurs on S6 (Conflicting Documents), S8 (Combined Shock), and S9
            (Compliance Breach).
          </p>
        </div>
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={scenarioChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
              <XAxis dataKey="scenario" tick={{ fontSize: 11 }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
              <RechartsTooltip contentStyle={{ fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="M-S" name="Multi-Agent (M-S)" fill={COLORS.MS} radius={[3, 3, 0, 0]} />
              <Bar dataKey="M-mix" name="Multi-Agent (M-mix)" fill={COLORS.MMIX} radius={[3, 3, 0, 0]} />
              <Bar dataKey="B" name="Baseline (B)" fill={COLORS.BASE} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 3. By Mess Level & 4. Scalability (1, 3, 5 Orders) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-md border border-hairline bg-surface p-4 space-y-3">
          <div>
            <h2 className="text-sm font-semibold">Degradation by Document Mess Level</h2>
            <p className="text-xs text-muted">
              Clean exports vs. typical merged/UoM sheets vs. messy rotated scans & missing fields.
            </p>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={messChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="mess_level" tick={{ fontSize: 11 }} />
                <YAxis domain={[50, 100]} tick={{ fontSize: 11 }} />
                <RechartsTooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="M-S" stroke={COLORS.MS} strokeWidth={2.2} />
                <Line type="monotone" dataKey="M-mix" stroke={COLORS.MMIX} strokeWidth={2.2} />
                <Line type="monotone" dataKey="B" stroke={COLORS.BASE} strokeWidth={2.2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-md border border-hairline bg-surface p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold flex items-center gap-1.5">
                <TrendingUp className="h-4 w-4 text-status-tool" strokeWidth={1.5} />
                Scalability Across 1, 3, and 5 Orders
              </h2>
              <p className="text-xs text-muted">
                Median latency (s) and mean cost ($) growth as bundle document count expands.
              </p>
            </div>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={scalabilityChartData}
                margin={{ top: 8, right: 16, left: 0, bottom: 8 }}
              >
                <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                <XAxis dataKey="orders" tick={{ fontSize: 11 }} />
                <YAxis yAxisId="left" tick={{ fontSize: 11 }} unit="s" />
                <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} unit="$" />
                <RechartsTooltip contentStyle={{ fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="M-mix_latency"
                  name="M-mix Latency (s)"
                  stroke={COLORS.MMIX}
                  strokeWidth={2.2}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="B_latency"
                  name="Baseline Latency (s)"
                  stroke={COLORS.BASE}
                  strokeWidth={2.2}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="M-mix_cost"
                  name="M-mix Cost ($)"
                  stroke={COLORS.MS}
                  strokeDasharray="4 4"
                  strokeWidth={2}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="B_cost"
                  name="Baseline Cost ($)"
                  stroke="#DC2626"
                  strokeDasharray="4 4"
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* 5. Human Baseline Comparison & Disclosed Limitations */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-7 rounded-md border border-hairline bg-surface overflow-hidden">
          <div className="px-4 py-3 border-b border-hairline bg-subtle/40 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider flex items-center gap-1.5">
              <Users className="h-3.5 w-3.5 text-status-tool" />
              Human Planners (Excel + Calculator) on 6 Shared Bundles
            </span>
            {summary.human.hourly_rate_lkr && (
              <span className="font-mono text-[11px] text-muted">
                Rate: LKR {summary.human.hourly_rate_lkr}/hr
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-hairline bg-subtle/20 text-[11px] font-sans font-semibold text-muted uppercase">
                  <th className="py-2 px-3">Planner</th>
                  <th className="py-2 px-3">Bundle</th>
                  <th className="py-2 px-3 text-right">Human PQS</th>
                  <th className="py-2 px-3 text-right">Manual Time</th>
                  <th className="py-2 px-3 text-right">Est. Cost (LKR)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {summary.human.results.map((h, idx) => (
                  <tr key={`${h.participant}-${h.bundle_id}-${idx}`}>
                    <td className="py-2 px-3">{h.participant}</td>
                    <td className="py-2 px-3 font-semibold">{h.bundle_id}</td>
                    <td className="py-2 px-3 text-right">{h.pqs.toFixed(1)}</td>
                    <td className="py-2 px-3 text-right">{h.minutes.toFixed(1)} min</td>
                    <td className="py-2 px-3 text-right">{h.cost_lkr.toFixed(0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="lg:col-span-5 rounded-md border border-hairline bg-surface p-4 space-y-2.5">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted flex items-center gap-1.5">
            <Info className="h-3.5 w-3.5 text-status-focus" />
            Methodological Limitations & Disclosures
          </div>
          <ul className="space-y-1.5 text-xs text-muted list-disc pl-4 leading-relaxed">
            {summary.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
          {summary.human.rate_source && (
            <div className="pt-2 border-t border-hairline text-[11px] text-faint">
              Wage assumption source: {summary.human.rate_source}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
