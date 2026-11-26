# Evaluation Integration Status

## Implemented and tested

- PQS aggregation and mandatory pass rules.
- Configurable schedule feasibility checks.
- Deterministic Q1-Q9 metric foundation and integrated scoring from temporary records.
- Provider-neutral Intake model evaluation harness and measured selection rule.
- Restartable benchmark planning, attempt persistence, trace aggregation, and budget checks.
- Explicit 1, 3, and 5-order scalability orchestration.
- Human evaluation protocol, Excel input template, converter, elapsed-time calculation, and sourced-rate cost calculation.
- API.md section 4.9 report generation, CSV output, paired comparisons, conditional Wilcoxon testing, and SVG charts.

## Awaiting team integration

- `project/core/schemas.py` and final `WorkOrderPlan` validation.
- Final `answer_key.json`, extraction-key, state, unit-alias, and factory-calendar formats.
- `project/core/runner.py`, `start_run()`, real trace events, and budget termination behavior.
- `extract_document(path, model=...)` and verified live provider model identifiers.
- Development, test, and scalability bundles.
- A documented human hourly-rate source selected before human sessions.

## Safety boundaries

- No live model calls or benchmark runs have been made.
- No model winner, human result, benchmark result, or performance improvement is claimed.
- Test doubles and report fixtures exist only in tests and are labelled mock.
- Benchmark orchestration does not read answer keys. The future runner adapter must enforce the team rule that agents receive only each bundle's `inputs/` directory.
- Q9 gives zero Q7 credit to silently resolved planted conflicts and compliance breaches. A silently accepted planted compliance breach is also a mandatory run failure under the agreed pass conditions.

## Contract decisions to confirm

- Canonical unit aliases and conversion factors.
- Factory holidays, shifts, downtime, machine capacities, and material-ready representation.
- Exact one-to-one identity rules for extraction records, shortages, suggested POs, and flags.
- Verified provider model identifiers. Logical candidate names in project documents are not treated as live API identifiers.
