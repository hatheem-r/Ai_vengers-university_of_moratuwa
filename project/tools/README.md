# project/tools: shared planning tools

These are the tools that TASKS.md §2 assigns to Shazan. Hatheem built a first version so
the multi-agent graph could run on real tools. **Shazan, they are yours now:** change them
as you need, but keep the input and output models in `core/schemas.py` §5 (changing those
is an API.md §8 change that everyone has to see).

| File | Function | In → out (core/schemas.py) |
|---|---|---|
| `loaders.py` | `read_document(path, images="auto")`, `read_input(inputs_dir, name)`, `list_documents(dir)` | file → `LoadedDocument` (pandas sheets, text, PDF page images, `content_blocks()`) |
| `mrp.py` | `mrp_tool(inp)` | `MrpToolInput` → `MrpToolOutput` |
| `schedule.py` | `schedule_tool(inp)` | `ScheduleToolInput` → `ScheduleToolOutput` |
| `rules.py` | `rule_check(inp)` | `RuleCheckInput` → `RuleCheckOutput` |
| `workcal.py` | calendar helpers | working days, lateness, rounding |

Each module's docstring explains its rules in full. The short version:

- **mrp_tool**: multi-level BoM explosion with wastage. Work orders take material in need-by
  order: free stock (on hand − on hold − reserved) first, then open POs, earliest usable first.
  A PO is usable on ETA + `receipt_lag_working_days`. Usable after need-by means a
  `supplier_delay` shortage. Anything left is `insufficient_stock`. Suggested POs go to the
  fastest supplier in `suppliers`. `material_ready` feeds `ScheduleJob.earliest_start`.
- **schedule_tool**: CP-SAT model, 10 s default limit. Each work centre is one resource
  (units × efficiency are folded into the duration) with `NoOverlap`, so it can't double-book.
  Downtime blocks whole days, and an operation can't run through it. Operations can pause
  overnight and over rest days, and can move to `alternates`. Overtime is a yes/no choice per
  work centre (up to `ot_max_min_per_day`), used only when it cuts lateness. The objective is
  working days late, then the number of overtime centres, then the sum of finish times. If
  CP-SAT finds nothing in time, you get the greedy schedule with `status="timeout"`. Jobs that
  can't be scheduled at all are listed in `infeasibility_reason` with `status="infeasible"`.
  The other jobs are still scheduled.
- **rule_check**: double booking, downtime, material not ready, operation order, rest days,
  overtime per day and week (policy), unknown work centre, qty vs customer PO, and missing flags.

## Tests

```bash
cd project
uv run pytest tests/test_tools.py -q
```

`tests/bundle_inputs.py` builds tool inputs from `extraction_key.json` (the correct Intake
output) and `data/generator/solver.py`. The tools never import the solver. They were
written separately on purpose, and the tests compare them on all 24 bundles:

- mrp_tool gives the same shortages, requirements, `material_ready` and PO suppliers as the
  solver on every bundle. The one known exception is the `C_S6_clean` on-hand figure. There
  the stock report and the delivery note disagree on purpose, and the Planner has to resolve it.
- schedule_tool is feasible on every bundle in under 4 s. rule_check finds no structural
  violation in its output. Total lateness is never worse than the answer key's. On
  dev_C_S5_messy it is 4 days against the key's 8, because the solver uses greedy EDD.
- Every answer key passes rule_check, and seeded violations are all caught.
- The multi-agent graph runs end to end on the 3 dev bundles with fake LLMs and these tools.
  `multi_agent/tools.py` finds them automatically. `WOP_STUB_TOOLS=1` switches back to the stubs.

## Known gaps and choices to revisit

- **Lunch break.** `PlanningCalendar` has no break, so schedule_tool counts working minutes
  continuously from `shift_start`. A 480-minute day ends at 16:00, while the answer keys put
  lunch at 12:30 to 13:30 and end at 17:00. Dates and lateness agree. Clock times can be up to
  1 h earlier. rule_check allows a 60-minute break (`BREAK_ALLOWANCE_MIN`) before it counts
  overtime. A proper fix is an optional `break_start` / `break_min` in `PlanningCalendar`.
- **Pack sizes.** Suggested PO quantity is the exact shortfall. The solver rounds up to the
  supplier pack, but pack sizes aren't in the documents or `SupplierOption`.
- **Supplier lead times.** These aren't in the documents either. Without `suppliers`, a short
  work order's `material_ready` stays unknown, and a warning says so.
- **Overtime granularity.** Overtime is per work centre for the whole horizon, as in the solver,
  not per day.
- **CP-SAT determinism.** It runs with 8 workers. Runs that hit the time limit can differ slightly.
- **`read_document` for the baseline.** Wrap `read_input(inputs_dir, name).content_blocks()` as
  the agent's tool, so the model can't read `state.json` or `answer_key.json`. Page images are
  sent only for pages without a text layer (`images="auto"`). Use `"all"` to send every page.
