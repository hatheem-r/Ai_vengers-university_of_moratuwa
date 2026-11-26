import { useMemo } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AlertTriangle, Clock, Wrench } from "lucide-react";
import type { Flag, Operation, WorkOrder, WorkOrderPlan } from "@/api/types";
import { cn, formatDateTime, formatShortDate } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";

interface GanttOperationBlock {
  wo: WorkOrder;
  op: Operation;
  startMs: number;
  endMs: number;
}

interface DowntimeOverlay {
  machineId: string;
  startMs: number;
  endMs: number;
  label: string;
}

const DAY_MS = 24 * 60 * 60 * 1000;

function parseIsoMs(s: string): number {
  const iso = s.length === 10 ? `${s}T00:00:00+05:30` : s;
  return new Date(iso).getTime();
}

/**
 * Extracts maintenance downtime intervals from `machine_down` flags so the Gantt
 * renders hatched maintenance overlays alongside scheduled work orders.
 */
function extractDowntimeOverlays(flags: Flag[], knownMachines: string[]): DowntimeOverlay[] {
  const out: DowntimeOverlay[] = [];
  for (const f of flags) {
    if (f.type !== "machine_down") continue;
    const matchedMachine =
      knownMachines.find((m) => f.message.includes(m)) ||
      (f.message.match(/\b([A-Z]+-\d+)\b/)?.[1] ?? null);
    if (!matchedMachine) continue;

    const dates: string[] = [...(f.message.match(/\d{4}-\d{2}-\d{2}/g) ?? [])];
    for (const src of f.sources) {
      if (typeof src.value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(src.value)) {
        dates.push(src.value);
      }
    }
    if (dates.length >= 1) {
      const sorted = [...dates].sort();
      const startMs = parseIsoMs(`${sorted[0]}T00:00:00+05:30`);
      const endMs = parseIsoMs(`${sorted[sorted.length - 1]}T23:59:59+05:30`);
      out.push({
        machineId: matchedMachine,
        startMs,
        endMs,
        label: f.message,
      });
    }
  }
  return out;
}

const WO_COLORS = [
  "bg-status-focus text-white border-blue-700",
  "bg-teal-600 text-white border-teal-700",
  "bg-indigo-600 text-white border-indigo-700",
  "bg-sky-600 text-white border-sky-700",
];

export function MachineGantt({ plan }: { plan: WorkOrderPlan }) {
  const { machines, blocksByMachine, downtimesByMachine, minMs, maxMs, days, woColorMap } =
    useMemo(() => {
      const colorMap = new Map<string, string>();
      plan.work_orders.forEach((wo, i) => {
        colorMap.set(wo.wo_id, WO_COLORS[i % WO_COLORS.length]);
      });

      const allBlocks: GanttOperationBlock[] = [];
      const machineSet = new Set<string>();

      for (const wo of plan.work_orders) {
        for (const op of wo.operations) {
          machineSet.add(op.machine_id);
          allBlocks.push({
            wo,
            op,
            startMs: parseIsoMs(op.start),
            endMs: parseIsoMs(op.end),
          });
        }
      }

      const machineList = Array.from(machineSet).sort();
      const downtimes = extractDowntimeOverlays(plan.flags, machineList);
      for (const dt of downtimes) {
        if (!machineSet.has(dt.machineId)) {
          machineSet.add(dt.machineId);
          machineList.push(dt.machineId);
        }
      }

      const planStartMs = parseIsoMs(plan.planning_start);
      const dueDatesMs = plan.work_orders.map((w) => parseIsoMs(`${w.due_date}T23:59:59+05:30`));

      const rawMin = Math.min(
        planStartMs,
        ...allBlocks.map((b) => b.startMs),
        ...(downtimes.length ? downtimes.map((d) => d.startMs) : [planStartMs])
      );
      const rawMax = Math.max(
        planStartMs + 7 * DAY_MS,
        ...allBlocks.map((b) => b.endMs),
        ...dueDatesMs
      );

      const startDay = new Date(rawMin);
      startDay.setHours(0, 0, 0, 0);
      const min = startDay.getTime();
      const totalDays = Math.max(5, Math.ceil((rawMax - min) / DAY_MS) + 1);
      const max = min + totalDays * DAY_MS;

      const dayTicks: Array<{ ms: number; label: string; isSunday: boolean }> = [];
      for (let i = 0; i < totalDays; i++) {
        const ms = min + i * DAY_MS;
        const d = new Date(ms);
        dayTicks.push({
          ms,
          label: d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }),
          isSunday: d.getDay() === 0,
        });
      }

      const grouped = new Map<string, GanttOperationBlock[]>();
      const dtGrouped = new Map<string, DowntimeOverlay[]>();
      for (const m of machineList) {
        grouped.set(m, []);
        dtGrouped.set(m, []);
      }
      for (const b of allBlocks) {
        grouped.get(b.op.machine_id)?.push(b);
      }
      for (const dt of downtimes) {
        dtGrouped.get(dt.machineId)?.push(dt);
      }

      return {
        machines: machineList,
        blocksByMachine: grouped,
        downtimesByMachine: dtGrouped,
        minMs: min,
        maxMs: max,
        days: dayTicks,
        woColorMap: colorMap,
      };
    }, [plan]);

  const spanMs = Math.max(1, maxMs - minMs);
  const toPct = (ms: number) => Math.max(0, Math.min(100, ((ms - minMs) / spanMs) * 100));

  if (machines.length === 0) {
    return (
      <div className="p-8 text-center text-xs text-muted border border-dashed border-hairline rounded">
        No scheduled machine operations in this plan.
      </div>
    );
  }

  return (
    <Tooltip.Provider delayDuration={80}>
      <div className="space-y-3">
        {/* Legend Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-3">
            {plan.work_orders.map((wo) => (
              <span key={wo.wo_id} className="inline-flex items-center gap-1.5 font-mono">
                <span
                  className={cn(
                    "h-2.5 w-2.5 rounded-sm",
                    woColorMap.get(wo.wo_id)?.split(" ")[0] ?? "bg-status-focus"
                  )}
                />
                <span className="font-semibold text-ink">{wo.wo_id}</span>
                <span className="text-muted">({wo.customer_po})</span>
                {wo.is_late && <Badge tone="critical" mono>+{wo.days_late}d late</Badge>}
              </span>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-3 w-5 rounded-sm border border-amber-500 bg-striped-overtime inline-block" />
              Overtime Shift
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-3 w-5 rounded-sm border border-status-critical/50 bg-hatched-downtime inline-block" />
              Maintenance Downtime
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-sm bg-subtle border border-hairline inline-block" />
              Sunday Rest
            </span>
          </div>
        </div>

        {/* Scrollable Horizontal Timeline Grid */}
        <div className="rounded-md border border-hairline bg-surface overflow-x-auto">
          <div className="min-w-[760px]">
            {/* Date Axis Header */}
            <div className="grid grid-cols-12 border-b border-hairline bg-subtle/50 text-[10px] font-mono text-muted">
              <div className="col-span-2 px-3 py-2 border-r border-hairline font-semibold uppercase text-ink">
                Machine / Line
              </div>
              <div className="col-span-10 relative h-8">
                {days.map((d, idx) => {
                  const left = (idx / days.length) * 100;
                  const width = 100 / days.length;
                  return (
                    <div
                      key={d.ms}
                      style={{ left: `${left}%`, width: `${width}%` }}
                      className={cn(
                        "absolute top-0 bottom-0 border-r border-hairline/70 px-1 flex items-center justify-center truncate",
                        d.isSunday && "bg-subtle text-faint"
                      )}
                    >
                      {d.label}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Machine Rows */}
            <div className="divide-y divide-hairline">
              {machines.map((machineId) => {
                const ops = blocksByMachine.get(machineId) ?? [];
                const dts = downtimesByMachine.get(machineId) ?? [];

                return (
                  <div key={machineId} className="grid grid-cols-12 items-stretch min-h-[52px]">
                    <div className="col-span-2 px-3 py-2.5 border-r border-hairline flex flex-col justify-center bg-surface">
                      <span className="font-mono text-xs font-semibold text-ink">{machineId}</span>
                      <span className="text-[10px] font-mono text-muted">
                        {ops.length} {ops.length === 1 ? "op" : "ops"}
                        {dts.length > 0 ? " · downtime" : ""}
                      </span>
                    </div>

                    <div className="col-span-10 relative py-2">
                      {/* Background Day Columns + Sunday Shading */}
                      {days.map((d, idx) => (
                        <div
                          key={d.ms}
                          style={{
                            left: `${(idx / days.length) * 100}%`,
                            width: `${100 / days.length}%`,
                          }}
                          className={cn(
                            "absolute top-0 bottom-0 border-r border-hairline/40 pointer-events-none",
                            d.isSunday && "bg-subtle/75"
                          )}
                        />
                      ))}

                      {/* Hatched Maintenance Downtime Blocks */}
                      {dts.map((dt, idx) => {
                        const left = toPct(dt.startMs);
                        const width = Math.max(2, toPct(dt.endMs) - left);
                        return (
                          <Tooltip.Root key={`dt-${idx}`}>
                            <Tooltip.Trigger asChild>
                              <div
                                style={{ left: `${left}%`, width: `${width}%` }}
                                className="absolute top-1.5 bottom-1.5 rounded border border-status-critical/60 bg-hatched-downtime flex items-center justify-center px-1.5 cursor-help z-10"
                              >
                                <span className="text-[10px] font-mono font-semibold text-status-critical truncate flex items-center gap-1">
                                  <Wrench className="h-3 w-3 shrink-0" />
                                  DOWN
                                </span>
                              </div>
                            </Tooltip.Trigger>
                            <Tooltip.Portal>
                              <Tooltip.Content
                                sideOffset={6}
                                className="z-50 max-w-xs rounded border border-hairline bg-surface p-2.5 text-xs text-ink shadow-md space-y-1"
                              >
                                <div className="font-semibold text-status-critical flex items-center gap-1.5">
                                  <Wrench className="h-3.5 w-3.5" />
                                  Planned / Logged Downtime · {machineId}
                                </div>
                                <p className="text-muted text-[11px]">{dt.label}</p>
                              </Tooltip.Content>
                            </Tooltip.Portal>
                          </Tooltip.Root>
                        );
                      })}

                      {/* Scheduled Operation Bars */}
                      {ops.map(({ wo, op, startMs, endMs }) => {
                        const left = toPct(startMs);
                        const width = Math.max(2.5, toPct(endMs) - left);
                        const baseColor = woColorMap.get(wo.wo_id) ?? WO_COLORS[0];

                        return (
                          <Tooltip.Root key={`${wo.wo_id}-${op.seq}`}>
                            <Tooltip.Trigger asChild>
                              <div
                                style={{ left: `${left}%`, width: `${width}%` }}
                                className={cn(
                                  "absolute top-2 bottom-2 rounded border px-2 flex items-center justify-between gap-1 cursor-pointer transition-transform hover:scale-[1.01] z-20 overflow-hidden",
                                  baseColor,
                                  op.uses_overtime && "ring-2 ring-amber-400",
                                  wo.is_late && "ring-1 ring-status-critical"
                                )}
                              >
                                <span className="font-mono text-[11px] font-semibold truncate">
                                  {wo.wo_id} · {op.operation}
                                </span>
                                {op.uses_overtime && (
                                  <span className="text-[9px] font-mono uppercase bg-amber-500 text-slate-950 font-bold px-1 rounded shrink-0">
                                    OT
                                  </span>
                                )}
                              </div>
                            </Tooltip.Trigger>
                            <Tooltip.Portal>
                              <Tooltip.Content
                                sideOffset={6}
                                className="z-50 w-72 rounded-md border border-hairline bg-surface p-3 text-xs text-ink shadow-lg space-y-1.5"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="font-mono font-semibold">
                                    {wo.wo_id} · Seq {op.seq}
                                  </span>
                                  <Badge tone="focus" mono>
                                    {machineId}
                                  </Badge>
                                </div>
                                <div className="font-medium">{op.operation}</div>
                                <div className="text-[11px] font-mono text-muted space-y-0.5 pt-1 border-t border-hairline">
                                  <div>Start: {formatDateTime(op.start)}</div>
                                  <div>End:   {formatDateTime(op.end)}</div>
                                  <div>
                                    Qty: {op.qty.toLocaleString()} {wo.uom} · PO: {wo.customer_po}
                                  </div>
                                  <div>Due Date: {formatShortDate(wo.due_date)}</div>
                                </div>
                                <div className="flex items-center gap-1.5 pt-1">
                                  {op.uses_overtime && (
                                    <Badge tone="warning" mono>
                                      <Clock className="h-3 w-3" /> Uses Overtime
                                    </Badge>
                                  )}
                                  {wo.is_late && (
                                    <Badge tone="critical" mono>
                                      <AlertTriangle className="h-3 w-3" /> {wo.days_late}d Late
                                    </Badge>
                                  )}
                                </div>
                              </Tooltip.Content>
                            </Tooltip.Portal>
                          </Tooltip.Root>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </Tooltip.Provider>
  );
}
