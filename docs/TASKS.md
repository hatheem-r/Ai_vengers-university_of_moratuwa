# Team Tasks (draft)

_Draft, 19 Nov 2026. Target: MVP on **Sun 29 Nov 2026** (Sri Lanka time). Companion to [API.md](API.md), [Workflow.md](Workflow.md) and [PROJECT_DETAILS.md](PROJECT_DETAILS.md)._

| Owner | Area | One-line goal |
|---|---|---|
| **Hatheem** | Multi-agent system + synthetic data | Build the LangGraph Intake → Planner → Checker → approval pipeline, and the generator that makes the test bundles and their answer keys |
| **Shazan** | Single-agent baseline + shared harness and tools | Build one Sonnet 5.5 agent with the same harness, tools and output schema, plus the shared tools both systems call |
| **Ajey** | Evaluation + Intake model choice | Score every plan, run the benchmark (multi-agent vs single agent vs humans), and pick the best model for the Intake agent |
| **Jisfaaz** | Frontend + API layer | Build the web app and the FastAPI layer it calls (with a mock mode), following [API.md](API.md) |

> **Two assumptions in this draft.** The shared tools and harness (`mrp_tool`, `schedule_tool`, `rule_check`, loaders, middleware) are given to Shazan, because the baseline needs all of them and Hatheem is already on the critical path. The FastAPI layer is given to Jisfaaz, so the frontend never waits on the agents. Swap either if the team prefers.

---

## 0. What everyone shares

### 0.1 Fixed design (do not change without a team decision)

- **Multi-agent:** Intake (Claude Haiku 5.5, one call per document, in parallel; Ajey may replace the model) → Planner (Claude Sonnet 5.5, calls `mrp_tool` and the OR-Tools `schedule_tool`) → Checker (Claude Haiku 5.5, at most **one** revision back to the Planner) → human approval (LangGraph `interrupt()`).
- **Baseline:** one Sonnet 5.5 agent, the **same** harness (LangChain `create_agent` + middleware), the **same** tools, the **same** `WorkOrderPlan` output, the **same** budgets (40 LLM calls, 1.5M tokens, 10 min per run).
- **Systems in the benchmark:** `B` (baseline), `M-S` (multi-agent, all Sonnet 5.5, main comparison), `M-mix` (Haiku/Sonnet mix, cost result).
- **Data:** 18 synthetic bundles = 3 factory profiles (A apparel 8, B light assembly 5, C food packer 5) × 9 scenarios (S1–S9) × 3 mess levels (clean / typical / messy). Each bundle is rendered from one hidden `state.json`; the answer key is computed from it.
- **Scoring:** `score.py` gives a 0–100 Plan Quality Score (PQS, weights Q1–Q7 = 15/10/15/10/15/10/20, −2 per false flag up to −10, Q9 gate). A run passes at PQS ≥ 80 with no schedule violations and no missed shortage.
- **Also required:** scalability test with 1, 3 and 5 orders; JSONL traces for every run; human baseline of 2 members on 6 bundles.

### 0.2 Shared contracts (frozen **Sat 21 Nov**)

These four things let everyone work in parallel. After the freeze, change them only by a PR that all four people see.

| Contract | File | Owner | Used by |
|---|---|---|---|
| `WorkOrderPlan` and extraction schemas (Pydantic) | `project/core/schemas.py` | Hatheem | everyone |
| Trace event format (one JSON object per line) | `project/core/schemas.py` (`TraceEvent`) + `project/core/tracing.py` | Shazan | Hatheem, Jisfaaz, Ajey |
| Runner interface: `start_run()` / `resume_run()` | `project/core/runner.py` | Hatheem | Jisfaaz (API), Ajey (benchmark), Shazan (baseline plugs in) |
| HTTP API | [docs/API.md](API.md) | Jisfaaz | frontend |

**Runner interface** (the only way the API and the benchmark start a run):

```python
# project/core/runner.py
def start_run(run_id: str, bundle_dir: Path, config: RunConfig,
              emit: Callable[[TraceEvent], None]) -> RunResult:
    """Runs until the plan is waiting for approval (or failed). Blocks."""

def resume_run(run_id: str, decision: ApprovalDecision,
               emit: Callable[[TraceEvent], None]) -> RunResult:
    """Approve -> completed. Reject with notes -> re-plan, then back to awaiting approval."""

# RunConfig: mode ("multi_agent" | "baseline"), model_profile ("mix" | "sonnet"),
#            auto_approve (bool, True in the benchmark), budgets, seed
# RunResult: status, plan (WorkOrderPlan | None), error (str | None)
```

`runner.py` sends `mode="multi_agent"` to `project/multi_agent/graph.py` (Hatheem) and `mode="baseline"` to `project/baseline/agent.py` (Shazan). Both must call `emit()` for every event, so traces look the same.

### 0.3 Repository layout

```
project/
  main.py                    CLI entry point (exists)
  core/
    schemas.py               Hatheem  WorkOrderPlan, extraction schemas, TraceEvent, RunConfig
    runner.py                Hatheem  start_run / resume_run
    models.py                Hatheem  model registry (Ajey adds candidates)
    harness.py               Shazan   create_agent factory + middleware (retry, caps, schema repair, tracing)
    tracing.py               Shazan   JSONL writer, token and cost table
  tools/                     Shazan   loaders.py, mrp.py, schedule.py, rules.py
  multi_agent/               Hatheem  graph.py, intake.py, planner.py, checker.py, prompts/
  baseline/                  Shazan   agent.py, prompts/
  api/                       Jisfaaz  app.py, routes.py, store.py, mock.py, mocks/*.json
frontend/                    Jisfaaz  web app
data/
  generator/                 Hatheem  generate.py, solver.py, renderers/, masters/
  bundles/<bundle_id>/       Hatheem  inputs/, state.json, manifest.json, answer_key.json
evaluation/                  Ajey     score.py, feasibility.py, run_benchmark.py, scalability.py,
                                      model_eval/, human/, report.py
results/                     Ajey     benchmark outputs that we commit
runs/                        (git-ignored) one folder per run: meta.json, trace.jsonl, plan.json
tests/                       everyone, one test file per module
```

**Hidden-data rule.** A system only ever receives `data/bundles/<id>/inputs/`. `state.json`, `manifest.json` and `answer_key.json` are read only by the generator and by `evaluation/`. The API never serves them.

### 0.4 Working rules

- One branch per person (`feat/<area>`), PR into `main`, one reviewer. Don't commit `.env`, `runs/` or API keys.
- 15-minute check-in each evening. Post blockers in the group chat the same day.
- **Dev set vs test set.** Hatheem also generates 3 dev bundles (different seeds, `dev_` prefix). All prompt tuning happens on dev bundles only. The 18 test bundles are for the benchmark.
- **Prompt freeze Thu 26 Nov, 20:00.** After that, no prompt or tool changes to either system before the benchmark.
- Mark anything mocked or incomplete in `PROJECT_DETAILS.md` (competition rule 05).

---

## 1. Hatheem: multi-agent system and synthetic data

### Goal
A LangGraph pipeline that turns a document bundle into a valid `WorkOrderPlan` with traces, and a generator that produces the 18 test bundles (plus dev and scalability bundles) with computed answer keys.

### Scope
**In:** `schemas.py`, `runner.py`, `models.py`; the graph (`StateGraph`, `Send` fan-out for Intake, Checker → Planner conditional edge with one revision, `interrupt()` with a checkpointer); the three agent prompts; the generator (masters for profiles A/B/C, scenario configs S1–S9, mess and conflict injection, Excel/PDF renderers, scan effects), `solver.py` for answer keys; the extraction key used for Q1 and for Ajey's model evaluation.
**Out:** shared tools and harness (Shazan), scoring (Ajey), API and UI (Jisfaaz).

### Inputs and outputs
| In | Out |
|---|---|
| A bundle folder `inputs/` (PDF, scans, XLSX, CSV, TXT) | `WorkOrderPlan` JSON (see [API.md §5](API.md#5-shared-schema-workorderplan)) |
| `RunConfig` (model profile, budgets) | Trace events via `emit()` |
| Rejection notes from a human | A revised plan |
| Generator: profile, scenario, mess level, seed, number of orders | `data/bundles/<id>/` with `inputs/`, `state.json`, `manifest.json`, `answer_key.json` |

### Interfaces with others
- **Shazan:** uses his `harness.py` for every agent node and his tools inside the Planner and Checker. Shares the three prompts so the baseline prompt can combine them.
- **Ajey:** `answer_key.json` format agreed with Ajey by Sat 21 Nov. `project/multi_agent/intake.py` exposes `extract_document(path, model=...)` so Ajey can test other models on Intake alone. Generator supports `--orders 1|3|5` for the scalability test.
- **Jisfaaz:** the runner emits `agent_start` / `agent_end` / `handoff` events with the `agent` names in [API.md §4.4](API.md#44-run-status-and-progress); the progress bar depends on them.

### Acceptance criteria
- [ ] `schemas.py` merged by Sat 21 Nov; `WorkOrderPlan` validates the example in API.md.
- [ ] `python -m data.generator.generate --profile A --scenario S3 --mess typical --seed 7` writes a complete bundle; the same seed gives the same files.
- [ ] All 18 test bundles + 3 dev bundles + 3 scalability bundles (A, S1, typical, with 1, 3 and 5 orders) exist; 3 test bundles hand-checked end to end (answer key matches the documents by hand).
- [ ] Every multi-agent run on the 3 dev bundles returns a schema-valid plan within budget, with traces showing Intake → Planner → Checker hand-offs.
- [ ] The Checker sends a plan back at most once; a second failure goes to approval with the open issues listed.
- [ ] Reject-with-notes resumes the graph from the checkpoint and produces a new plan version.
- [ ] `model_profile="sonnet"` runs every agent on Sonnet 5.5 (`M-S`); `"mix"` uses the Intake model Ajey picks.

### Deliverables and files
`project/core/schemas.py`, `runner.py`, `models.py`; `project/multi_agent/*`; `data/generator/*`; `data/bundles/*`; a short `data/README.md` (how to regenerate, bundle list, which bundles are dev/test/scale/human).

### Dependencies
Needs Shazan's harness and tools (Mon 23 Nov) for the Planner and Checker. Until then, build the graph with stub tools that return fixed JSON. Needs Ajey's Intake model decision (Wed 25 Nov) for `M-mix`.

### Milestones
| Date | Done when |
|---|---|
| Fri 20 Nov | Proposal submitted |
| Sat 21 Nov | `schemas.py` + `runner.py` signatures merged; `answer_key.json` format agreed with Ajey |
| Sun 22 Nov | Master data A, `solver.py`, **one sample bundle `dev_A_S1_clean`** shared with everyone |
| Mon 23 Nov | Graph runs end to end on the sample bundle with stub tools; Excel renderers done |
| Tue 24 Nov | Graph on real tools; PDF renderers + scans; masters B and C |
| Wed 25 Nov | Mess and conflict injection; 3 dev bundles; scalability bundles |
| Thu 26 Nov | 18 test bundles generated, 3 hand-checked; prompt freeze 20:00 |
| Fri 27 – Sat 28 Nov | Fix crashes only; support benchmark runs |
| Sun 29 Nov | MVP |

**If time runs short:** follow the data plan's cut order: reduce profiles B and C to 3 bundles each before cutting any apparel bundle; keep S6, S8 and S9.

---

## 2. Shazan: single-agent baseline, shared harness and tools

### Goal
A fair single-agent baseline: one Sonnet 5.5 ReAct agent that reads the same bundle and returns the same `WorkOrderPlan`, using exactly the same harness and tools as the multi-agent system.

### Scope
**In:** `harness.py` (LangChain `create_agent` factory with retry + backoff, call/token/time caps, one schema-repair retry, prompt caching, tracing); `tracing.py`; tools `read_document` (pandas for XLSX/CSV, page images + text for PDFs), `mrp_tool`, `schedule_tool` (OR-Tools CP-SAT, 10 s limit), `rule_check`, `submit_plan`; the baseline agent and its prompt (the three multi-agent prompts combined, no extra hints).
**Out:** the multi-agent graph, the answer-key solver, scoring.

### Inputs and outputs
| In | Out |
|---|---|
| Bundle `inputs/`, `RunConfig` | `WorkOrderPlan` JSON + trace events (same format as multi-agent) |
| Tool inputs: structured orders, BoM, stock, POs, capacity, maintenance, policy | `mrp_tool`: requirements and shortages. `schedule_tool`: operation schedule + conflicts. `rule_check`: list of rule violations |

### Interfaces with others
- **Hatheem:** tool input/output models live in `schemas.py`; agree them Sat 21 Nov. `mrp_tool` and Hatheem's `solver.py` are written separately on purpose: if they disagree on a dev bundle, one of them has a bug.
- **Ajey:** trace events must carry `input_tokens`, `cached_input_tokens`, `output_tokens`, `latency_ms`, `cost_usd` per LLM call, so cost and latency come straight from traces. The baseline must plug into `start_run(mode="baseline")`.
- **Jisfaaz:** the baseline emits the same event types; its progress shows as one agent step plus tool-call counts.

### Acceptance criteria
- [ ] `harness.py` and all tools merged by Mon 23 Nov, with unit tests on the sample bundle (`mrp_tool` shortages match `solver.py` on `dev_A_S1_clean`).
- [ ] `schedule_tool` returns a feasible schedule or a clear infeasibility reason within 10 s; never double-books a machine or schedules into logged downtime.
- [ ] Hitting any budget ends the run cleanly with `status=failed`, `error.code="budget_exceeded"`.
- [ ] Baseline returns a schema-valid plan on all 3 dev bundles.
- [ ] A side-by-side check (a table in the PR) shows the baseline and multi-agent use the same model settings, tools, tool descriptions, budgets and retries.
- [ ] Reject-with-notes resumes the baseline with the notes as a new message and returns a new plan version.

### Deliverables and files
`project/core/harness.py`, `project/core/tracing.py`, `project/tools/{loaders,mrp,schedule,rules}.py`, `project/baseline/agent.py`, `project/baseline/prompts/`, `tests/test_tools.py`, `tests/test_baseline.py`.

### Dependencies
Needs `schemas.py` (Sat 21 Nov) and the sample bundle (Sun 22 Nov). Needs Hatheem's final prompts by the prompt freeze (Thu 26 Nov) to build the combined baseline prompt.

### Milestones
| Date | Done when |
|---|---|
| Sat 21 Nov | Tool input/output models agreed with Hatheem; trace event fields agreed with Ajey |
| Sun 22 Nov | `harness.py` + `tracing.py` working with a dummy tool |
| Mon 23 Nov | `read_document`, `mrp_tool`, `schedule_tool`, `rule_check` merged with tests |
| Tue 24 Nov | Baseline runs end to end on the sample bundle |
| Wed 25 Nov | Baseline on all 3 dev bundles; reject/resume works |
| Thu 26 Nov | Combined prompt built from Hatheem's final prompts; prompt freeze 20:00 |
| Fri 27 – Sat 28 Nov | Fix crashes only; human baseline session (as one of the 2 human planners) |
| Sun 29 Nov | MVP |

---

## 3. Ajey: evaluation, benchmark and Intake model choice

### Goal
Turn every plan into a trusted score, run the benchmark that compares multi-agent, single agent and humans on quality, time and cost, and choose the Intake model with evidence.

### Scope
**In:**
1. `score.py`: PQS 0–100 per run from the plan + `answer_key.json` + `state.json`, using the rubric in `plans/baseline-and-evaluation-plan.md` (unit normalisation, ±1 working day, item alias mapping, Q9 gate).
2. `feasibility.py`: counts hard schedule violations for Q5 (double-booking, operation before material arrives, work during downtime, hours over shift capacity).
3. `run_benchmark.py`: runs `B`, `M-S`, `M-mix` on the 18 bundles via `start_run(auto_approve=True)`, 3 repeats for B and M-S, 1 for M-mix (≈ $90). Resumes after a crash; skips finished runs.
4. `scalability.py`: 1, 3 and 5 orders on the scale bundles, for B and M-mix (M-S if budget allows); reports latency, tokens, cost and PQS against the number of orders.
5. Human baseline: protocol, plan template, timing, and `human_to_plan.py` that converts a filled template into a `WorkOrderPlan` scored by the same `score.py`.
6. Intake model evaluation (`evaluation/model_eval/`): compare candidates on the Intake job alone.
7. `report.py`: tables and charts by system, scenario (S1–S9) and mess level; paired win/tie/loss and Wilcoxon test; writes `results/<eval_id>/summary.json` in the format in [API.md §4.9](API.md#49-evaluations).

**Out:** building either system or the generator.

### Intake model evaluation
- **Candidates:** Claude Haiku 5.5 (current choice), Claude Sonnet 5.5, Gemini 3.5 Flash-Lite, Gemini 3.8 Flash; plus one non-VLM route (OCR such as Tesseract or docling, then a text model such as gpt-oss-120b on Groq or Haiku). Add an open-weight VLM only if a hosted API is easy to use.
- **Data:** every document in the 3 dev bundles plus extra scanned and messy documents from the generator (aim for ≥ 40 documents, at least 10 scanned). Never the 18 test bundles.
- **Metrics:** field-level F1 against the extraction key (overall, messy, scanned), schema-valid rate, cost per document, median latency.
- **Decision rule:** pick the cheapest model within 2 F1 points of the best on messy and scanned documents. Write it up in `results/model_eval/decision.md` and tell Hatheem.
- **Fairness note:** this changes `M-mix` only. `M-S` stays all Sonnet so the main comparison is unchanged. Any new model must be added to the AI declaration in `PROJECT_DETAILS.md`.

### Human baseline
- **Who:** 2 team members who did not build the test cases and have not seen the answer keys: **Jisfaaz and Shazan** (Hatheem built the data; Ajey holds the keys and runs the sessions).
- **What:** 6 test bundles chosen by Ajey and Hatheem to cover S1, S3, S5, S6, S8, S9 and all three mess levels. Default: each person plans 3 of them; if time allows, both do all 6. Jisfaaz and Shazan must not open these 6 bundles before their session.
- **How:** Excel and a calculator only, no AI tools; 90-minute cap per bundle; stopwatch time from opening the folder to handing in the template. Cost = time × an assumed planner hourly wage (state the figure and its source).
- **Report:** PQS, minutes, cost per bundle, and the limitation (2 people, 6 bundles, team members rather than professional planners).

### Interfaces with others
- **Hatheem:** answer key and extraction key format (Sat 21 Nov); `extract_document(path, model=...)`; scale bundles.
- **Shazan:** trace fields for tokens, cost and latency (Sat 21 Nov).
- **Jisfaaz:** `summary.json` and per-run score JSON in the API.md format, so the evaluation page reads them as-is. Give him a realistic mock `summary.json` by Mon 23 Nov.

### Acceptance criteria
- [ ] `score.py` gives 100 on a plan built from the answer key itself, and a known lower score on 3 hand-made broken plans (a missed shortage, a double-booked machine, a silently resolved conflict).
- [ ] 3 bundles hand-checked with Hatheem before the full run.
- [ ] `run_benchmark.py` is restartable and writes one row per run (bundle, system, repeat, PQS, pass, latency, tokens, cost, success).
- [ ] Results broken down by system, scenario and mess level, with paired comparisons; claims labelled as results only where measured.
- [ ] Scalability table and chart for 1/3/5 orders.
- [ ] Human baseline completed and scored with the same scorer.
- [ ] Intake model decision written up by Wed 25 Nov.

### Deliverables and files
`evaluation/{score,feasibility,run_benchmark,scalability,report}.py`, `evaluation/model_eval/`, `evaluation/human/` (protocol.md, plan template .xlsx, `human_to_plan.py`), `results/<eval_id>/` (summary.json, runs.csv, charts), `results/model_eval/decision.md`, `tests/test_score.py`.

### Dependencies
Needs schemas (Sat 21 Nov), the sample bundle (Sun 22 Nov), both systems running (Tue 24 – Wed 25 Nov), all bundles (Thu 26 Nov).

### Milestones
| Date | Done when |
|---|---|
| Sat 21 Nov | Answer key format and trace fields agreed |
| Sun 22 Nov | `score.py` skeleton scores the sample bundle's answer key as 100 |
| Mon 23 Nov | `feasibility.py`; mock `summary.json` for Jisfaaz; model-eval harness running on the sample bundle |
| Tue 24 Nov | `score.py` complete with tests; model eval on dev documents |
| Wed 25 Nov | **Intake model decision**; `run_benchmark.py` dry run on dev bundles; human protocol and template ready |
| Thu 26 Nov | Hand-check 3 test bundles with Hatheem |
| Fri 27 Nov | Full benchmark run; human baseline sessions |
| Sat 28 Nov | Scalability runs; `report.py` tables and charts; results write-up |
| Sun 29 Nov | MVP |

---

## 4. Jisfaaz: frontend and API layer

### Goal
A web app that lets a judge upload a bundle (or pick a scenario), run either system, watch each agent work, review and approve the plan, read the trace, and see the benchmark results.

### Scope
**In:** the FastAPI app in `project/api/` exactly as in [API.md](API.md), including **mock mode** (`MOCK=1`) that serves the JSON in `project/api/mocks/` and fakes progress; the run store (`runs/<run_id>/`); background execution of `start_run` / `resume_run`; the frontend in `frontend/`.
**Stack:** React + Vite + TypeScript is recommended, with types generated from FastAPI's `/openapi.json`. Streamlit is fine if it is faster for you; decide by Fri 20 Nov and update `README.md` and `PROJECT_DETAILS.md` (they currently say Streamlit).
**Out:** agent logic, scoring.

### Pages
1. **Start:** upload files or pick one of the scenarios; choose mode (multi-agent / single agent) and model profile.
2. **Live run:** pipeline view (Intake ×N with documents done/total, Planner, Checker with revision count, Approval), elapsed time, tokens, cost. Poll `GET /runs/{id}` every 2 s.
3. **Plan review:** work orders table, Gantt by machine, materials and shortages, suggested POs, flags with their source documents, rationale, Checker verdict. Approve, or reject with notes.
4. **Trace:** timeline of events, with hand-offs between agents highlighted (this is our evidence for competition rule 02). Download the JSONL.
5. **Compare:** two runs on the same bundle side by side (multi-agent vs baseline), with score if the bundle has an answer key.
6. **Evaluation:** PQS by system, scenario and mess level; cost and latency; scalability chart (1/3/5 orders); human vs agents.

### Interfaces with others
- **Hatheem and Shazan:** only through `runner.py`. The API never imports agent code directly.
- **Ajey:** reads `results/<eval_id>/summary.json` and calls `evaluation.score.score_run()` for `GET /runs/{id}/score`.

### Acceptance criteria
- [ ] Mock mode serves every endpoint in API.md by Sat 21 Nov, so the frontend never waits for the agents.
- [ ] All six pages work against mock mode by Tue 24 Nov.
- [ ] With `MOCK=0`, a full multi-agent run and a baseline run on a dev bundle work from the UI, including reject with notes and approve.
- [ ] Hidden files (`state.json`, `manifest.json`, `answer_key.json`) can never be fetched through the API.
- [ ] Errors from the API show a readable message, not a blank page.
- [ ] The demo flow (pick scenario → run → watch → review → approve → compare → evaluation) takes under 5 minutes.

### Deliverables and files
`project/api/{app,routes,store,mock}.py`, `project/api/mocks/*.json`, `frontend/`, setup and run steps added to `README.md`, `tests/test_api.py` (mock mode).

### Dependencies
None to start (mock mode). Real runs need `runner.py` with at least one system working (Tue 24 Nov). Real results need Ajey's `summary.json` (Sat 28 Nov).

### Milestones
| Date | Done when |
|---|---|
| Fri 20 Nov | Stack chosen; API.md reviewed with Hatheem |
| Sat 21 Nov | FastAPI mock mode serves every endpoint |
| Mon 23 Nov | Start, Live run and Plan review pages on mocks |
| Tue 24 Nov | Trace, Compare and Evaluation pages on mocks |
| Wed 25 Nov | Wired to the real runner (multi-agent and baseline) |
| Thu 26 Nov | Reject/approve flow end to end; error states; README run steps |
| Fri 27 Nov | Human baseline session (as one of the 2 human planners) |
| Sat 28 Nov | Real benchmark results on the Evaluation page; demo rehearsal and recording |
| Sun 29 Nov | MVP |

---

## 5. Team timeline at a glance

| Date | Hatheem | Shazan | Ajey | Jisfaaz |
|---|---|---|---|---|
| Fri 20 | Proposal | Proposal | Proposal | Proposal; pick stack |
| Sat 21 | **Schemas + runner frozen** | Tool models, trace fields | Answer key format | **Mock API live** |
| Sun 22 | **Sample bundle**, solver | Harness + tracing | `score.py` skeleton | Pages on mocks |
| Mon 23 | Graph on stub tools | **Tools merged** | Feasibility, mock summary | Pages on mocks |
| Tue 24 | Graph on real tools | Baseline end to end | Model eval running | All pages on mocks |
| Wed 25 | Dev + scale bundles | Baseline on dev bundles | **Intake model decision** | Wired to real runner |
| Thu 26 | **18 test bundles**, prompt freeze | Combined prompt, freeze | Hand-check 3 bundles | Approve/reject end to end |
| Fri 27 | Support | Human session | **Benchmark + human sessions** | Human session |
| Sat 28 | Support, write-up | Support | Scalability, report | Real results, demo |
| Sun 29 | **MVP** | **MVP** | **MVP** | **MVP** |

**Critical path:** schemas (21) → sample bundle (22) → tools (23) → both systems on dev bundles (25) → 18 bundles + prompt freeze (26) → benchmark (27). A slip on any of these moves the benchmark, so flag it the same day.
