import type { WorkOrderPlan } from "@/api/types";

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function exportPlanAsJson(plan: WorkOrderPlan, version: number): void {
  const blob = new Blob([JSON.stringify(plan, null, 2)], { type: "application/json" });
  downloadBlob(blob, `${plan.bundle_id}_${plan.system_id}_plan_v${version}.json`);
}

function csvCell(val: unknown): string {
  if (val === null || val === undefined) return "";
  const str = String(val);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Exports a clean multi-section Excel-compatible CSV workbook (UTF-8 BOM so Microsoft Excel opens cleanly).
 */
export function exportPlanAsExcelCsv(plan: WorkOrderPlan, version: number): void {
  const rows: string[] = [];

  rows.push("WORK ORDER PLAN SUMMARY");
  rows.push(
    ["Bundle ID", "System ID", "Plan Version", "Planning Start", "Created At"]
      .map(csvCell)
      .join(",")
  );
  rows.push(
    [plan.bundle_id, plan.system_id, `v${version}`, plan.planning_start, plan.created_at]
      .map(csvCell)
      .join(",")
  );
  rows.push("");

  rows.push("WORK ORDERS");
  rows.push(
    [
      "WO ID",
      "Customer PO",
      "Item Code",
      "Variant",
      "Qty",
      "UoM",
      "Priority",
      "Due Date",
      "Planned Start",
      "Planned Finish",
      "Late",
      "Days Late",
    ]
      .map(csvCell)
      .join(",")
  );
  for (const wo of plan.work_orders) {
    const variantStr = wo.variant
      ? [wo.variant.colour, wo.variant.size].filter(Boolean).join(" / ")
      : "";
    rows.push(
      [
        wo.wo_id,
        wo.customer_po,
        wo.item_code,
        variantStr,
        wo.qty,
        wo.uom,
        wo.priority,
        wo.due_date,
        wo.planned_start,
        wo.planned_finish,
        wo.is_late ? "YES" : "NO",
        wo.days_late,
      ]
        .map(csvCell)
        .join(",")
    );
  }
  rows.push("");

  rows.push("OPERATIONS SCHEDULE");
  rows.push(
    ["WO ID", "Seq", "Operation", "Machine ID", "Start", "End", "Qty", "Uses Overtime"]
      .map(csvCell)
      .join(",")
  );
  for (const wo of plan.work_orders) {
    for (const op of wo.operations) {
      rows.push(
        [
          wo.wo_id,
          op.seq,
          op.operation,
          op.machine_id,
          op.start,
          op.end,
          op.qty,
          op.uses_overtime ? "YES" : "NO",
        ]
          .map(csvCell)
          .join(",")
      );
    }
  }
  rows.push("");

  rows.push("MATERIAL REQUIREMENTS & SHORTAGES");
  rows.push(
    ["Item Code", "UoM", "Required Qty", "On Hand", "Reserved", "On Order", "Net Shortage", "Work Orders"]
      .map(csvCell)
      .join(",")
  );
  for (const m of plan.material_requirements) {
    rows.push(
      [
        m.item_code,
        m.uom,
        m.required_qty,
        m.on_hand_qty,
        m.reserved_qty,
        m.on_order_qty,
        m.net_shortage_qty,
        m.for_work_orders.join("; "),
      ]
        .map(csvCell)
        .join(",")
    );
  }
  rows.push("");

  rows.push("SUGGESTED PURCHASE ORDERS");
  rows.push(["Item Code", "Qty", "UoM", "Supplier ID", "Need By", "Expedite", "Reason"].map(csvCell).join(","));
  for (const po of plan.suggested_pos) {
    rows.push(
      [po.item_code, po.qty, po.uom, po.supplier_id ?? "", po.need_by, po.expedite ? "YES" : "NO", po.reason]
        .map(csvCell)
        .join(",")
    );
  }
  rows.push("");

  rows.push("FLAGS & RISK REGISTER");
  rows.push(["Flag ID", "Type", "Severity", "Needs Human", "Message", "Sources", "Resolution"].map(csvCell).join(","));
  for (const f of plan.flags) {
    const src = f.sources
      .map((s) => `${s.document}:${s.field}${s.row ? `@r${s.row}` : ""}${s.page ? `@p${s.page}` : ""}`)
      .join("; ");
    rows.push(
      [f.flag_id, f.type, f.severity, f.needs_human ? "YES" : "NO", f.message, src, f.resolution ?? ""]
        .map(csvCell)
        .join(",")
    );
  }

  const bom = "\uFEFF";
  const blob = new Blob([bom + rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
  downloadBlob(blob, `${plan.bundle_id}_${plan.system_id}_plan_v${version}.csv`);
}

export function exportPlanAsPdf(): void {
  window.print();
}
