<!-- Plan conventions from docs/API.md §5. These should be identical in the multi-agent Planner prompt. -->
Submit the plan by calling the `WorkOrderPlan` tool once, with the complete plan.

- `bundle_id`, `system_id` and `created_at` are filled in by the system; use the bundle id you were given, `"B"` and the current time.
- `planning_start` is the first day work may be scheduled.
- Work orders: `wo_id` like `WO-001`, unique; `item_code` is the master-data code; `customer_po` like `PO-4471`; `variant` for apparel (for example `{"colour": "Navy", "size": "M"}`), otherwise null; operations in sequence with the machine id from the master data.
- Dates are `YYYY-MM-DD`; date-times are ISO 8601 with the Sri Lanka offset, for example `2026-11-27T08:00:00+05:30`.
- Quantities are in the item's unit from the master data, with `uom` next to every number.
- `is_late` is true when the planned finish date is after the due date; `days_late` counts working days (0 when on time).
- Every flag has a unique `flag_id` (`F1`, `F2`, ...), a type, a severity, one sentence a planner can act on, its sources (document file name and field, with page or row), `needs_human`, and the resolution if the plan resolves it.
- Lists that have nothing in them are empty lists, not omitted.
- `rationale` is at most about 300 words. `checker` is null.
