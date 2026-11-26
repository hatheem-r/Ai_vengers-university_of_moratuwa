/**
 * Strict TypeScript contract matching `docs/API.md` (draft v0.1, schema_version 1.0).
 * Security: never references or requests `state.json`, `manifest.json`, or `answer_key.json`.
 */

export type SystemId = "B" | "M-S" | "M-mix" | "human";
export type RunMode = "multi_agent" | "baseline";
export type ModelProfile = "mix" | "sonnet";
export type RunStatusCode = "queued" | "running" | "awaiting_approval" | "completed" | "failed";
export type StepStatusCode = "pending" | "running" | "done" | "failed" | "skipped";
export type MessLevel = "clean" | "typical" | "messy";
export type Severity = "low" | "medium" | "high";

export type FlagType =
  | "conflict"
  | "missing_field"
  | "shortage"
  | "supplier_delay"
  | "machine_down"
  | "capacity_overload"
  | "due_date_risk"
  | "compliance_breach"
  | "invoice_po_mismatch"
  | "needs_approval";

export type ShortageCause = "insufficient_stock" | "supplier_delay" | "quality_reject" | "other";
export type CheckerVerdict = "pass" | "revise" | "fail_after_revision";

export type TraceEventType =
  | "run_start"
  | "agent_start"
  | "llm_call"
  | "tool_call"
  | "handoff"
  | "checker_verdict"
  | "interrupt"
  | "human_decision"
  | "agent_end"
  | "run_end"
  | "error";

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
  };
}

export interface HealthResponse {
  status: string;
  mock: boolean;
  api_version: string;
  schema_version: string;
}

export interface BundleFile {
  name: string;
  type: string;
  size_bytes: number;
  pages: number | null;
}

export interface BundleResponse {
  bundle_id: string;
  name: string;
  source: "upload" | "scenario";
  created_at: string;
  files: BundleFile[];
  has_answer_key: boolean;
}

export interface ScenarioItem {
  bundle_id: string;
  set: "test" | "dev" | "scale" | string;
  profile: "A" | "B" | "C" | string;
  profile_name: string;
  scenario: string;
  scenario_name: string;
  mess_level: MessLevel;
  n_documents: number;
  n_orders: number;
  has_answer_key: boolean;
  files: BundleFile[];
}

export interface ScenariosResponse {
  scenarios: ScenarioItem[];
}

export interface StartRunRequest {
  bundle_id: string;
  mode: RunMode;
  model_profile?: ModelProfile;
  auto_approve?: boolean;
  seed?: number;
}

export interface StartRunResponse {
  run_id: string;
  status: RunStatusCode;
  system_id: SystemId;
}

export interface StepProgress {
  done: number;
  total: number;
}

export interface StepItem {
  name: string;
  status: StepStatusCode;
}

export interface RunStep {
  key: "intake" | "planner" | "checker" | "agent" | "approval" | string;
  label: string;
  model: string | null;
  status: StepStatusCode;
  started_at: string | null;
  ended_at: string | null;
  progress: StepProgress | null;
  detail: string | null;
  items: StepItem[];
}

export interface RunMetrics {
  elapsed_s: number;
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface RunBudget {
  max_llm_calls: number;
  max_tokens: number;
  max_seconds: number;
}

export interface RunStatusResponse {
  run_id: string;
  bundle_id: string;
  mode: RunMode;
  model_profile: ModelProfile;
  system_id: SystemId;
  status: RunStatusCode;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  current_step: string | null;
  plan_version: number;
  revisions: {
    checker: number;
    human: number;
  };
  steps: RunStep[];
  metrics: RunMetrics;
  budget: RunBudget;
  error: {
    code: string;
    message: string;
  } | null;
}

export type RunSummaryItem = Omit<RunStatusResponse, "steps">;

export interface RunsListResponse {
  runs: RunSummaryItem[];
}

/* --- WorkOrderPlan (§5) --- */

export interface SourceRef {
  document: string;
  field: string;
  value: string | number | null;
  page: number | null;
  row: number | null;
}

export interface Variant {
  colour?: string | null;
  size?: string | null;
  sizes?: Record<string, number> | null;
}

export interface Operation {
  seq: number;
  operation: string;
  machine_id: string;
  start: string;
  end: string;
  qty: number;
  uses_overtime: boolean;
}

export interface WorkOrder {
  wo_id: string;
  item_code: string;
  qty: number;
  uom: string;
  customer_po: string;
  variant: Variant | null;
  bom_version: string | null;
  priority: number;
  due_date: string;
  planned_start: string;
  planned_finish: string;
  is_late: boolean;
  days_late: number;
  operations: Operation[];
}

export interface MaterialRequirement {
  item_code: string;
  uom: string;
  required_qty: number;
  on_hand_qty: number;
  reserved_qty: number;
  on_order_qty: number;
  net_shortage_qty: number;
  for_work_orders: string[];
}

export interface Shortage {
  item_code: string;
  uom: string;
  shortfall_qty: number;
  need_by: string;
  affected_work_orders: string[];
  cause: ShortageCause;
}

export interface SuggestedPO {
  item_code: string;
  qty: number;
  uom: string;
  supplier_id: string | null;
  need_by: string;
  expedite: boolean;
  reason: string;
}

export interface Flag {
  flag_id: string;
  type: FlagType;
  severity: Severity;
  message: string;
  sources: SourceRef[];
  needs_human: boolean;
  resolution: string | null;
}

export interface CheckerReview {
  verdict: CheckerVerdict;
  revisions_used: number;
  issues: string[];
  open_issues: string[];
}

export interface WorkOrderPlan {
  schema_version: string;
  bundle_id: string;
  system_id: SystemId;
  created_at: string;
  planning_start: string;
  work_orders: WorkOrder[];
  material_requirements: MaterialRequirement[];
  shortages: Shortage[];
  suggested_pos: SuggestedPO[];
  flags: Flag[];
  assumptions: string[];
  rationale: string;
  checker: CheckerReview | null;
}

export interface PlanResponse {
  run_id: string;
  plan_version: number;
  approval: {
    status: "pending" | "approved" | "rejected";
    decided_at: string | null;
    notes: string | null;
  };
  plan: WorkOrderPlan;
}

export interface ApprovalRequest {
  decision: "approve" | "reject";
  notes?: string;
}

export interface ApprovalResponse {
  run_id: string;
  status: RunStatusCode;
  plan_version: number;
}

/* --- Trace (§6) --- */

export interface TraceEvent {
  run_id: string;
  seq: number;
  ts: string;
  type: TraceEventType;
  agent: string | null;
  item: string | null;
  model: string | null;
  tool: string | null;
  input_tokens: number | null;
  cached_input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  from_agent: string | null;
  to_agent: string | null;
  summary: string;
  payload: Record<string, unknown> | null;
  payload_ref: string | null;
}

export interface TraceResponse {
  run_id: string;
  events: TraceEvent[];
}

/* --- Score (§4.10) --- */

export interface RunScore {
  run_id: string;
  bundle_id: string;
  plan_version: number;
  pqs: number;
  passed: boolean;
  fail_reasons: string[];
  metrics: {
    q1_extraction_f1: number;
    q2_material_reqs: number;
    q3_shortage_f1: number;
    q3_shortage_recall: number;
    q4_suggested_pos: number;
    q5_schedule_feasibility: number;
    q6_due_dates: number;
    q7_flag_recall: number;
    q8_false_flags: number;
    q8_penalty: number;
    q9_gate_triggered: boolean;
  };
  schedule_violations: string[];
  missed_flags: Array<{ type: string; description: string }>;
  false_flags: Array<{ flag_id: string; type: string }>;
}

/* --- Evaluations (§4.9) --- */

export interface EvaluationListItem {
  eval_id: string;
  created_at: string;
  status: string;
  is_mock: boolean;
  n_runs: number;
}

export interface EvaluationSummary {
  eval_id: string;
  created_at: string;
  status: string;
  is_mock: boolean;
  config: {
    bundles: number;
    repeats: Record<string, number>;
    budgets: RunBudget;
    intake_model_mix: string;
  };
  systems: Array<{
    system_id: SystemId;
    label: string;
    mode: RunMode;
    models: Record<string, string>;
  }>;
  overall: Array<{
    system_id: SystemId;
    n_runs: number;
    pqs_mean: number;
    pqs_std: number;
    pass_rate: number;
    run_success_rate: number;
    latency_median_s: number;
    latency_max_s: number;
    cost_mean_usd: number;
    tokens_mean: number;
    metrics: {
      q1: number;
      q2: number;
      q3: number;
      q3_recall: number;
      q4: number;
      q5: number;
      q6: number;
      q7: number;
      false_flags_mean: number;
    };
  }>;
  by_scenario: Array<{
    system_id: SystemId;
    scenario: string;
    n_runs: number;
    pqs_mean: number;
    pass_rate: number;
  }>;
  by_mess_level: Array<{
    system_id: SystemId;
    mess_level: MessLevel;
    n_runs: number;
    pqs_mean: number;
    q1: number;
  }>;
  paired: Array<{
    a: SystemId;
    b: SystemId;
    n_bundles: number;
    mean_pqs_diff: number;
    wins: number;
    ties: number;
    losses: number;
    wilcoxon_p: number | null;
  }>;
  scalability: Array<{
    system_id: SystemId;
    n_orders: number;
    n_runs: number;
    latency_median_s: number;
    tokens_mean: number;
    cost_mean_usd: number;
    pqs_mean: number;
  }>;
  human: {
    participants: number;
    bundles: number;
    hourly_rate_lkr: number | null;
    rate_source: string | null;
    results: Array<{
      participant: string;
      bundle_id: string;
      pqs: number;
      minutes: number;
      cost_lkr: number;
    }>;
    summary: {
      pqs_mean: number;
      minutes_mean: number;
    };
  };
  runs: Array<{
    run_id: string;
    bundle_id: string;
    system_id: SystemId;
    repeat: number;
    pqs: number;
    passed: boolean;
    success: boolean;
    latency_s: number;
    tokens: number;
    cost_usd: number;
  }>;
  limitations: string[];
}
