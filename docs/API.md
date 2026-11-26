# API Contract (draft v0.1)

_Draft, 19 Nov 2026. Freeze target: **Sat 21 Nov**. Owner: Jisfaaz (API layer). Schema owner: Hatheem (`project/core/schemas.py`). See [TASKS.md](TASKS.md) for who builds what._

This is the contract between the frontend and the backend. Both systems (multi-agent and single-agent baseline) sit behind the same endpoints and return the same `WorkOrderPlan`, which is also what `score.py` scores.

---

## 1. Conventions

| Item | Rule |
|---|---|
| Server | FastAPI, `project/api/app.py`. Run: `uvicorn project.api.app:app --reload --port 8000` |
| Base URL | `http://localhost:8000/api/v1` |
| Format | JSON, UTF-8, `snake_case` keys. Uploads use `multipart/form-data`. |
| Times | ISO 8601 with Sri Lanka offset, e.g. `2026-11-23T09:30:00+05:30`. Plan dates (no time) are `YYYY-MM-DD`. |
| Quantities | Numbers in the item's canonical unit from the factory master data (`uom` is always given next to the number). |
| IDs | Opaque strings. `bundle_id` e.g. `A_S3_typical` or `up_7f3k2a`; `run_id` e.g. `run_20261123_093000_a1b2`. |
| CORS | Allow `http://localhost:5173` (Vite) and `http://localhost:8501` (Streamlit) in development. |
| Auth | None for the MVP (local demo only). |
| Source of truth | The Pydantic models in `project/core/schemas.py`. FastAPI publishes them at `/openapi.json`; the frontend can generate TypeScript types from it (for example with `openapi-typescript`). If this file and the code disagree, fix one of them in the same PR. |

### Errors

Every non-2xx response has this body:

```json
{ "error": { "code": "plan_not_ready", "message": "Run run_20261123_093000_a1b2 is still running." } }
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Bad field, unknown mode, empty upload |
| 404 | `not_found` | Unknown bundle, run or evaluation |
| 404 | `no_answer_key` | Score requested for an uploaded bundle |
| 409 | `plan_not_ready` | Plan requested before the run reaches `awaiting_approval` |
| 409 | `not_awaiting_approval` | Approval sent when the run is not waiting for one |
| 413 | `too_large` | Upload over the limits |
| 500 | `run_failed` / `internal` | Unexpected server error |

---

## 2. Mock mode

`MOCK=1 uvicorn project.api.app:app --port 8000` serves every endpoint from `project/api/mocks/`:

| Mock file | Served by |
|---|---|
| `scenarios.json` | `GET /scenarios` |
| `run_status_sequence.json` | `GET /runs/{id}`: steps through `queued → running (intake 1/9 … 9/9) → planner → checker (revise) → planner → checker (pass) → awaiting_approval` over about 20 s |
| `plan.json` | `GET /runs/{id}/plan` (the example in §5.3) |
| `trace.jsonl` | `GET /runs/{id}/trace` |
| `score.json` | `GET /runs/{id}/score` |
| `evaluation_summary.json` | `GET /evaluations/{id}` (Ajey supplies a realistic one by Mon 23 Nov) |

In mock mode every response carries the header `X-Mock: true`, and the frontend shows a small "mock data" badge so we never present mock numbers as results (competition rule 05).

---

## 3. Endpoint summary

| # | Method | Path | Purpose |
|---|---|---|---|
| 4.1 | `POST` | `/bundles` | Upload a document bundle |
| 4.1 | `GET` | `/bundles/{bundle_id}` | Bundle details and file list |
| 4.2 | `GET` | `/scenarios` | List synthetic scenario bundles |
| 4.3 | `POST` | `/runs` | Start a run (multi-agent or baseline) |
| 4.4 | `GET` | `/runs/{run_id}` | Run status and progress per agent |
| 4.5 | `GET` | `/runs` | List runs (filter by bundle) |
| 4.6 | `GET` | `/runs/{run_id}/plan` | Get the `WorkOrderPlan` |
| 4.7 | `POST` | `/runs/{run_id}/approval` | Approve or reject the plan with notes |
| 4.8 | `GET` | `/runs/{run_id}/trace` | Get the trace (JSON or JSONL) |
| 4.9 | `GET` | `/evaluations`, `/evaluations/{eval_id}` | Benchmark results |
| 4.10 | `GET` | `/runs/{run_id}/score` | Score one run against its answer key |
| 4.11 | `GET` | `/health` | Liveness, mock flag, versions |

---

## 4. Endpoints

### 4.1 Upload a document bundle

`POST /bundles` (`multipart/form-data`)

| Field | Type | Notes |
|---|---|---|
| `files` | file, repeated | `.pdf .xlsx .xls .csv .txt .png .jpg .jpeg`; max 25 files, 20 MB each, 100 MB total. A single `.zip` is also accepted and unpacked. |
| `name` | string, optional | Display name |

Response `201`:

```json
{
  "bundle_id": "up_7f3k2a",
  "name": "Polo order, week 48",
  "source": "upload",
  "created_at": "2026-11-23T09:28:11+05:30",
  "files": [
    { "name": "customer_po_4471.pdf", "type": "pdf", "size_bytes": 182344, "pages": 2 },
    { "name": "bom_polo.xlsx", "type": "xlsx", "size_bytes": 24110, "pages": null }
  ],
  "has_answer_key": false
}
```

`GET /bundles/{bundle_id}` returns the same object. Files are stored in `runs/uploads/<bundle_id>/inputs/`.

### 4.2 List scenarios

`GET /scenarios?set=test` where `set` is `test` (default, the 18 benchmark bundles), `dev`, `scale`, or `all`.

Response `200`:

```json
{
  "scenarios": [
    {
      "bundle_id": "A_S3_typical",
      "set": "test",
      "profile": "A",
      "profile_name": "Apparel export (cut-make-trim)",
      "scenario": "S3",
      "scenario_name": "Material shortage",
      "mess_level": "typical",
      "n_documents": 9,
      "n_orders": 2,
      "has_answer_key": true,
      "files": [ { "name": "customer_po_4471.pdf", "type": "pdf", "size_bytes": 182344, "pages": 2 } ]
    }
  ]
}
```

Built from `data/bundles/*/manifest.json`, but only these display fields. Planted conflicts, defects, `state.json` and `answer_key.json` are never returned.

### 4.3 Start a run

`POST /runs`

```json
{
  "bundle_id": "A_S3_typical",
  "mode": "multi_agent",
  "model_profile": "mix",
  "auto_approve": false,
  "seed": 0
}
```

| Field | Values | Notes |
|---|---|---|
| `mode` | `multi_agent` \| `baseline` | Required |
| `model_profile` | `mix` (default) \| `sonnet` | `mix` = Intake model chosen by Ajey (currently Haiku 5.5), Planner Sonnet 5.5, Checker Haiku 5.5. `sonnet` = all Sonnet 5.5. Ignored for `baseline` (always Sonnet 5.5). |
| `auto_approve` | bool, default `false` | `true` skips the human step (used by the benchmark) |
| `seed` | int, optional | Passed to the runner for repeatability where possible |

Response `202`:

```json
{ "run_id": "run_20261123_093000_a1b2", "status": "queued", "system_id": "M-mix" }
```

`system_id` is derived: `baseline → B`, `multi_agent + sonnet → M-S`, `multi_agent + mix → M-mix`.

### 4.4 Run status and progress

`GET /runs/{run_id}`. The frontend polls this every 2 s until `status` is `awaiting_approval`, `completed` or `failed`.

**Run statuses**

```
queued → running → awaiting_approval ──approve──► completed
                        │
                        └──reject with notes──► running (re-plan) → awaiting_approval
any state ──error or budget hit──► failed
```

**Step statuses:** `pending`, `running`, `done`, `failed`, `skipped`.

**Steps by mode** (the `key` values are fixed; the runner's trace events use the same names in `agent`):

| Mode | Steps (in order) |
|---|---|
| `multi_agent` | `intake`, `planner`, `checker`, `approval` |
| `baseline` | `agent`, `approval` |

Response `200`:

```json
{
  "run_id": "run_20261123_093000_a1b2",
  "bundle_id": "A_S3_typical",
  "mode": "multi_agent",
  "model_profile": "mix",
  "system_id": "M-mix",
  "status": "running",
  "created_at": "2026-11-23T09:30:00+05:30",
  "started_at": "2026-11-23T09:30:01+05:30",
  "ended_at": null,
  "current_step": "checker",
  "plan_version": 1,
  "revisions": { "checker": 1, "human": 0 },
  "steps": [
    {
      "key": "intake", "label": "Intake Agent", "model": "claude-haiku-5-5",
      "status": "done", "started_at": "2026-11-23T09:30:01+05:30", "ended_at": "2026-11-23T09:30:16+05:30",
      "progress": { "done": 9, "total": 9 },
      "detail": "9 documents read, 1 repair retry",
      "items": [
        { "name": "customer_po_4471.pdf", "status": "done" },
        { "name": "stock_report.xlsx", "status": "done" }
      ]
    },
    {
      "key": "planner", "label": "Planner Agent", "model": "claude-sonnet-5-5",
      "status": "done", "started_at": "2026-11-23T09:30:16+05:30", "ended_at": "2026-11-23T09:31:05+05:30",
      "progress": null, "detail": "Revised once after Checker feedback", "items": []
    },
    {
      "key": "checker", "label": "Checker Agent", "model": "claude-haiku-5-5",
      "status": "running", "started_at": "2026-11-23T09:31:05+05:30", "ended_at": null,
      "progress": null, "detail": "Second review", "items": []
    },
    {
      "key": "approval", "label": "Human approval", "model": null,
      "status": "pending", "started_at": null, "ended_at": null,
      "progress": null, "detail": null, "items": []
    }
  ],
  "metrics": {
    "elapsed_s": 71.4,
    "llm_calls": 15,
    "tool_calls": 6,
    "input_tokens": 142300,
    "cached_input_tokens": 38000,
    "output_tokens": 21450,
    "cost_usd": 0.31
  },
  "budget": { "max_llm_calls": 40, "max_tokens": 1500000, "max_seconds": 600 },
  "error": null
}
```

When `status = failed`, `error` is `{ "code": "budget_exceeded" | "schema_invalid" | "tool_error" | "model_error" | "internal", "message": "..." }`.

For `baseline`, the `agent` step uses `detail` for the latest action (for example `"Calling schedule_tool"`) and `progress` for documents read so far.

### 4.5 List runs

`GET /runs?bundle_id=A_S3_typical&limit=20` returns `{ "runs": [ <run status object without steps> ] }`, newest first. Used by the Compare page.

### 4.6 Get the plan

`GET /runs/{run_id}/plan?version=2` (`version` optional, default latest)

Response `200`:

```json
{
  "run_id": "run_20261123_093000_a1b2",
  "plan_version": 2,
  "approval": { "status": "pending", "decided_at": null, "notes": null },
  "plan": { "...": "WorkOrderPlan, see §5" }
}
```

`approval.status` is `pending`, `approved` or `rejected`. Returns `409 plan_not_ready` before the first plan exists.

### 4.7 Approve or reject the plan

`POST /runs/{run_id}/approval`

```json
{ "decision": "reject", "notes": "Customer agreed to a 2-day slip on PO-4471. Do not use overtime on Sunday." }
```

| Field | Values | Notes |
|---|---|---|
| `decision` | `approve` \| `reject` | Required |
| `notes` | string, max 2,000 chars | Required for `reject` |

Response `202`:

```json
{ "run_id": "run_20261123_093000_a1b2", "status": "running", "plan_version": 2 }
```

- `approve`: the run moves to `completed`; the plan is final.
- `reject`: multi-agent resumes the graph at the Planner with the notes (the Checker still reviews; this does not use up the Checker's one revision). Baseline resumes the same agent with the notes as a new message. Either way a new `plan_version` follows and the run returns to `awaiting_approval`.
- Returns `409 not_awaiting_approval` in any other state.

### 4.8 Get the trace

`GET /runs/{run_id}/trace?format=json&after_seq=0`

- `format=json` (default): `{ "run_id": "...", "events": [ <TraceEvent>, ... ] }`. `after_seq` lets the UI fetch only new events while the run is live.
- `format=jsonl`: the raw `runs/<run_id>/trace.jsonl` file as `application/x-ndjson`, for download.

Event format: §6.

### 4.9 Evaluations

`GET /evaluations` lists result sets in `results/`:

```json
{
  "evaluations": [
    { "eval_id": "bench_20261127", "created_at": "2026-11-27T22:10:00+05:30", "status": "complete", "is_mock": false, "n_runs": 126 }
  ]
}
```

`GET /evaluations/{eval_id}` (or `/evaluations/latest`) returns `results/<eval_id>/summary.json`, written by Ajey's `report.py`:

```json
{
  "eval_id": "bench_20261127",
  "created_at": "2026-11-27T22:10:00+05:30",
  "status": "complete",
  "is_mock": false,
  "config": {
    "bundles": 18,
    "repeats": { "B": 3, "M-S": 3, "M-mix": 1 },
    "budgets": { "max_llm_calls": 40, "max_tokens": 1500000, "max_seconds": 600 },
    "intake_model_mix": "claude-haiku-5-5"
  },
  "systems": [
    { "system_id": "B", "label": "Single agent (Sonnet 5.5)", "mode": "baseline",
      "models": { "agent": "claude-sonnet-5-5" } },
    { "system_id": "M-S", "label": "Multi-agent (all Sonnet 5.5)", "mode": "multi_agent",
      "models": { "intake": "claude-sonnet-5-5", "planner": "claude-sonnet-5-5", "checker": "claude-sonnet-5-5" } },
    { "system_id": "M-mix", "label": "Multi-agent (Haiku/Sonnet)", "mode": "multi_agent",
      "models": { "intake": "claude-haiku-5-5", "planner": "claude-sonnet-5-5", "checker": "claude-haiku-5-5" } }
  ],
  "overall": [
    {
      "system_id": "M-S", "n_runs": 54,
      "pqs_mean": 0.0, "pqs_std": 0.0, "pass_rate": 0.0, "run_success_rate": 0.0,
      "latency_median_s": 0.0, "latency_max_s": 0.0, "cost_mean_usd": 0.0, "tokens_mean": 0,
      "metrics": { "q1": 0.0, "q2": 0.0, "q3": 0.0, "q3_recall": 0.0, "q4": 0.0, "q5": 0.0,
                   "q6": 0.0, "q7": 0.0, "false_flags_mean": 0.0 }
    }
  ],
  "by_scenario": [ { "system_id": "M-S", "scenario": "S6", "n_runs": 6, "pqs_mean": 0.0, "pass_rate": 0.0 } ],
  "by_mess_level": [ { "system_id": "M-S", "mess_level": "messy", "n_runs": 15, "pqs_mean": 0.0, "q1": 0.0 } ],
  "paired": [
    { "a": "M-S", "b": "B", "n_bundles": 18, "mean_pqs_diff": 0.0,
      "wins": 0, "ties": 0, "losses": 0, "wilcoxon_p": null }
  ],
  "scalability": [
    { "system_id": "M-mix", "n_orders": 1, "n_runs": 3, "latency_median_s": 0.0,
      "tokens_mean": 0, "cost_mean_usd": 0.0, "pqs_mean": 0.0 }
  ],
  "human": {
    "participants": 2,
    "bundles": 6,
    "hourly_rate_lkr": null,
    "rate_source": null,
    "results": [ { "participant": "H1", "bundle_id": "A_S6_messy", "pqs": 0.0, "minutes": 0.0, "cost_lkr": 0.0 } ],
    "summary": { "pqs_mean": 0.0, "minutes_mean": 0.0 }
  },
  "runs": [
    { "run_id": "run_...", "bundle_id": "A_S3_typical", "system_id": "M-S", "repeat": 1,
      "pqs": 0.0, "passed": false, "success": true, "latency_s": 0.0, "tokens": 0, "cost_usd": 0.0 }
  ],
  "limitations": [ "Synthetic data only.", "Human baseline is 2 team members on 6 bundles." ]
}
```

(The zeros are placeholders for shape only. Real numbers come only from the benchmark.)

### 4.10 Score one run

`GET /runs/{run_id}/score`. Only for scenario bundles (`has_answer_key = true`); uploads return `404 no_answer_key`. Calls `evaluation.score.score_run(plan, bundle_dir)`.

Response `200` (`RunScore`):

```json
{
  "run_id": "run_20261123_093000_a1b2",
  "bundle_id": "A_S3_typical",
  "plan_version": 2,
  "pqs": 86.3,
  "passed": true,
  "fail_reasons": [],
  "metrics": {
    "q1_extraction_f1": 0.94,
    "q2_material_reqs": 1.0,
    "q3_shortage_f1": 1.0,
    "q3_shortage_recall": 1.0,
    "q4_suggested_pos": 1.0,
    "q5_schedule_feasibility": 1.0,
    "q6_due_dates": 0.75,
    "q7_flag_recall": 0.67,
    "q8_false_flags": 1,
    "q8_penalty": -2,
    "q9_gate_triggered": false
  },
  "schedule_violations": [],
  "missed_flags": [ { "type": "conflict", "description": "Delivery note vs stock report on FAB-PIQ-NVY received qty" } ],
  "false_flags": [ { "flag_id": "F4", "type": "due_date_risk" } ]
}
```

(Values here are illustrative only.)

### 4.11 Health

`GET /health` → `{ "status": "ok", "mock": false, "api_version": "0.1", "schema_version": "1.0" }`

---

## 5. Shared schema: `WorkOrderPlan`

Every system (multi-agent, baseline, and the human baseline via `human_to_plan.py`) outputs this object. `score.py` scores it. The Pydantic model in `project/core/schemas.py` is the source of truth.

### 5.1 Top level

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | string | yes | `"1.0"` |
| `bundle_id` | string | yes | |
| `system_id` | `B` \| `M-S` \| `M-mix` \| `human` | yes | |
| `created_at` | datetime | yes | |
| `planning_start` | date | yes | First day the plan may schedule work |
| `work_orders` | `WorkOrder[]` | yes | One per item per customer PO line (split orders have several) |
| `material_requirements` | `MaterialRequirement[]` | yes | One per component across all work orders |
| `shortages` | `Shortage[]` | yes | Empty list if none |
| `suggested_pos` | `SuggestedPO[]` | yes | Empty list if none |
| `flags` | `Flag[]` | yes | Conflicts, missing fields, risks, breaches |
| `assumptions` | string[] | yes | Anything the system assumed instead of reading |
| `rationale` | string | yes | Short explanation of priority and trade-offs (max ~300 words) |
| `checker` | `CheckerReview` \| null | no | Multi-agent only; `null` for baseline and human |

### 5.2 Nested objects

**WorkOrder**

| Field | Type | Notes |
|---|---|---|
| `wo_id` | string | `WO-001`, unique in the plan |
| `item_code` | string | Master-data code (not the name) |
| `qty` | number | |
| `uom` | string | |
| `customer_po` | string | e.g. `PO-4471` |
| `variant` | object \| null | e.g. `{ "colour": "Navy", "size": "M" }` for apparel; `null` otherwise |
| `bom_version` | string \| null | |
| `priority` | int | 1 = highest |
| `due_date` | date | From the customer PO |
| `planned_start` | datetime | |
| `planned_finish` | datetime | |
| `is_late` | bool | `planned_finish` date after `due_date` |
| `days_late` | int | 0 if on time (working days) |
| `operations` | `Operation[]` | In sequence |

**Operation**: `seq` (int), `operation` (string), `machine_id` (string, master-data machine ID), `start` (datetime), `end` (datetime), `qty` (number), `uses_overtime` (bool).

**MaterialRequirement**: `item_code`, `uom`, `required_qty` (incl. wastage), `on_hand_qty`, `reserved_qty`, `on_order_qty` (open POs arriving before need), `net_shortage_qty` (≥ 0), `for_work_orders` (string[]).

**Shortage**: `item_code`, `uom`, `shortfall_qty`, `need_by` (date), `affected_work_orders` (string[]), `cause` (`insufficient_stock` \| `supplier_delay` \| `quality_reject` \| `other`).

**SuggestedPO**: `item_code`, `qty`, `uom`, `supplier_id` (string \| null), `need_by` (date), `expedite` (bool), `reason` (string).

**Flag**

| Field | Type | Notes |
|---|---|---|
| `flag_id` | string | `F1`, `F2`, … |
| `type` | enum | `conflict`, `missing_field`, `shortage`, `supplier_delay`, `machine_down`, `capacity_overload`, `due_date_risk`, `compliance_breach`, `invoice_po_mismatch`, `needs_approval` |
| `severity` | `low` \| `medium` \| `high` | |
| `message` | string | One sentence a planner can act on |
| `sources` | `SourceRef[]` | The documents and fields involved. **A flag only scores if it names the right document or field.** |
| `needs_human` | bool | `true` when the system did not resolve it (S6 conflicts, S9 breaches must be `true`) |
| `resolution` | string \| null | What the plan did about it, if anything |

**SourceRef**: `document` (file name in `inputs/`), `field` (string, e.g. `"delivery_date"`), `value` (string \| number \| null), `page` (int \| null), `row` (int \| null).

**CheckerReview**: `verdict` (`pass` \| `revise` \| `fail_after_revision`), `revisions_used` (0 or 1), `issues` (string[]), `open_issues` (string[], left for the human).

### 5.3 Example

```json
{
  "schema_version": "1.0",
  "bundle_id": "A_S3_typical",
  "system_id": "M-mix",
  "created_at": "2026-11-23T09:31:40+05:30",
  "planning_start": "2026-11-24",
  "work_orders": [
    {
      "wo_id": "WO-001",
      "item_code": "POLO-PIQ-NVY",
      "qty": 2400,
      "uom": "pcs",
      "customer_po": "PO-4471",
      "variant": { "colour": "Navy", "size": "M" },
      "bom_version": "v3",
      "priority": 1,
      "due_date": "2026-12-08",
      "planned_start": "2026-11-27T08:00:00+05:30",
      "planned_finish": "2026-12-09T17:00:00+05:30",
      "is_late": true,
      "days_late": 1,
      "operations": [
        { "seq": 10, "operation": "Cutting", "machine_id": "CUT-01",
          "start": "2026-11-27T08:00:00+05:30", "end": "2026-11-28T12:00:00+05:30", "qty": 2400, "uses_overtime": false },
        { "seq": 20, "operation": "Sewing", "machine_id": "LINE-2",
          "start": "2026-11-30T08:00:00+05:30", "end": "2026-12-08T17:00:00+05:30", "qty": 2400, "uses_overtime": false },
        { "seq": 30, "operation": "Finishing and packing", "machine_id": "PACK-01",
          "start": "2026-12-09T08:00:00+05:30", "end": "2026-12-09T17:00:00+05:30", "qty": 2400, "uses_overtime": false }
      ]
    }
  ],
  "material_requirements": [
    { "item_code": "FAB-PIQ-NVY", "uom": "kg", "required_qty": 612.0, "on_hand_qty": 367.2,
      "reserved_qty": 0, "on_order_qty": 0, "net_shortage_qty": 244.8, "for_work_orders": ["WO-001"] },
    { "item_code": "BTN-4H-15L", "uom": "pcs", "required_qty": 7344, "on_hand_qty": 12000,
      "reserved_qty": 2000, "on_order_qty": 0, "net_shortage_qty": 0, "for_work_orders": ["WO-001"] }
  ],
  "shortages": [
    { "item_code": "FAB-PIQ-NVY", "uom": "kg", "shortfall_qty": 244.8, "need_by": "2026-11-26",
      "affected_work_orders": ["WO-001"], "cause": "insufficient_stock" }
  ],
  "suggested_pos": [
    { "item_code": "FAB-PIQ-NVY", "qty": 250, "uom": "kg", "supplier_id": "SUP-03",
      "need_by": "2026-11-26", "expedite": true,
      "reason": "Covers the 244.8 kg shortfall; SUP-03 lead time is 2 days." }
  ],
  "flags": [
    {
      "flag_id": "F1",
      "type": "shortage",
      "severity": "high",
      "message": "Navy pique fabric is 244.8 kg short for WO-001; cutting cannot start before the expedited PO arrives.",
      "sources": [
        { "document": "stock_report.xlsx", "field": "on_hand", "value": 367.2, "page": null, "row": 14 },
        { "document": "bom_polo.xlsx", "field": "qty_per_unit", "value": 0.25, "page": null, "row": 3 }
      ],
      "needs_human": false,
      "resolution": "Suggested expedited PO to SUP-03; start moved to 27 Nov."
    },
    {
      "flag_id": "F2",
      "type": "due_date_risk",
      "severity": "medium",
      "message": "WO-001 finishes 1 working day after the buyer's ex-factory date.",
      "sources": [ { "document": "customer_po_4471.pdf", "field": "delivery_date", "value": "2026-12-08", "page": 1, "row": null } ],
      "needs_human": true,
      "resolution": null
    }
  ],
  "assumptions": [ "BoM wastage of 2% applied to fabric and buttons." ],
  "rationale": "PO-4471 is the only open order. The fabric shortage delays cutting by 3 days ...",
  "checker": {
    "verdict": "pass",
    "revisions_used": 1,
    "issues": [ "First plan scheduled cutting before the fabric PO arrives." ],
    "open_issues": [ "Ask the buyer whether a 1-day slip is acceptable." ]
  }
}
```

---

## 6. Trace event (`TraceEvent`, one per JSONL line)

Both systems write the same format to `runs/<run_id>/trace.jsonl` through `project/core/tracing.py`. Ajey computes cost and latency from these; the Trace page draws them.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | |
| `seq` | int | 1, 2, 3 … within a run |
| `ts` | datetime | |
| `type` | enum | `run_start`, `agent_start`, `llm_call`, `tool_call`, `handoff`, `checker_verdict`, `interrupt`, `human_decision`, `agent_end`, `run_end`, `error` |
| `agent` | string \| null | `intake`, `planner`, `checker`, `agent` (baseline), `approval` |
| `item` | string \| null | Document name for Intake events |
| `model` | string \| null | e.g. `claude-haiku-5-5` |
| `tool` | string \| null | e.g. `mrp_tool` |
| `input_tokens`, `cached_input_tokens`, `output_tokens` | int \| null | From the API `usage` field (`llm_call` only) |
| `latency_ms` | int \| null | |
| `cost_usd` | number \| null | Tokens × list prices in `tracing.py` |
| `from_agent`, `to_agent` | string \| null | `handoff` only |
| `summary` | string | One line for the UI, e.g. `"Planner → Checker: plan v1 (1 WO, 1 shortage)"` |
| `payload` | object \| null | Small structured detail (tool args/result summary, verdict). Large blobs are written to `runs/<run_id>/payloads/<seq>.json` and referenced by `payload_ref`. |
| `payload_ref` | string \| null | |

Example lines:

```jsonl
{"run_id":"run_20261123_093000_a1b2","seq":14,"ts":"2026-11-23T09:30:41+05:30","type":"tool_call","agent":"planner","item":null,"model":null,"tool":"mrp_tool","input_tokens":null,"cached_input_tokens":null,"output_tokens":null,"latency_ms":212,"cost_usd":0,"from_agent":null,"to_agent":null,"summary":"mrp_tool: 14 components, 1 shortage (FAB-PIQ-NVY 244.8 kg)","payload":null,"payload_ref":"payloads/14.json"}
{"run_id":"run_20261123_093000_a1b2","seq":19,"ts":"2026-11-23T09:31:05+05:30","type":"handoff","agent":"planner","item":null,"model":null,"tool":null,"input_tokens":null,"cached_input_tokens":null,"output_tokens":null,"latency_ms":null,"cost_usd":null,"from_agent":"planner","to_agent":"checker","summary":"Planner → Checker: plan v1","payload":{"plan_version":1},"payload_ref":null}
```

---

## 7. Python runner interface (backend side)

The API and the benchmark both start runs only through `project/core/runner.py` (see [TASKS.md §0.2](TASKS.md#02-shared-contracts-frozen-sat-21-nov)). The API runs `start_run` / `resume_run` in a background thread, passes an `emit` callback that appends to `trace.jsonl`, and builds the §4.4 progress object from those events. So the progress bar works the same for both systems as long as they emit `agent_start` / `agent_end` with the step names above.

Files per run:

```
runs/<run_id>/
  meta.json        request, status, timestamps, metrics
  trace.jsonl      events (§6)
  plan_v1.json     WorkOrderPlan, one file per version
  approval.json    decisions with notes
  payloads/        large tool inputs/outputs
```

`runs/` is git-ignored; benchmark outputs that we keep go to `results/`.

---

## 8. Changing this contract

1. Open a PR that changes this file **and** `schemas.py` together.
2. Bump `api_version` (minor for added fields, major for renamed or removed ones).
3. Update `project/api/mocks/` in the same PR so the frontend keeps working.
4. Tag all four members. After Thu 26 Nov, only additive changes.
