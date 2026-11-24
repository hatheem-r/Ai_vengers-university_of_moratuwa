"""Run the baseline on one bundle from the command line (real Claude calls; costs money).

    python -m project.baseline --bundle data/bundles/dev_A_S1_clean --planning-start 2026-11-24

Writes runs/<run_id>/trace.jsonl, plan_v1.json and meta.json, and prints each
trace event as it happens. Use it on dev bundles only (TASKS.md §0.4).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from project.baseline.agent import BASELINE_MODEL, run_baseline
from project.core.schemas import Budgets, RunConfig, TraceEvent
from project.core.tracing import JsonlTraceWriter, new_run_id, read_trace, summarize


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m project.baseline", description="Run the single-agent baseline on one bundle.")
    p.add_argument("--bundle", required=True, help="Bundle folder (with inputs/) or a folder of documents")
    p.add_argument("--planning-start", type=date.fromisoformat, help="First day work may be scheduled (YYYY-MM-DD)")
    p.add_argument("--auto-approve", action="store_true", help="Approve the plan automatically (benchmark mode)")
    p.add_argument("--runs-dir", default="runs", help="Where to write run folders (default: runs/)")
    p.add_argument("--max-llm-calls", type=int, default=Budgets().max_llm_calls)
    args = p.parse_args(argv)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.", file=sys.stderr)
        return 2

    run_id = new_run_id()
    run_dir = Path(args.runs_dir) / run_id
    writer = JsonlTraceWriter(run_dir / "trace.jsonl")

    def emit(event: TraceEvent) -> None:
        writer(event)
        print(f"[{event.seq:>3}] {event.type:<14} {event.summary}", flush=True)

    config = RunConfig(mode="baseline", auto_approve=args.auto_approve,
                       budgets=Budgets(max_llm_calls=args.max_llm_calls))
    print(f"Run {run_id}: baseline ({BASELINE_MODEL}) on {args.bundle}")
    try:
        result, session = run_baseline(run_id, args.bundle, config, emit,
                                       planning_start=args.planning_start, run_dir=run_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if result.plan is not None:
        (run_dir / f"plan_v{session.plan_version}.json").write_text(result.plan.model_dump_json(indent=2), encoding="utf-8")
    totals = summarize(read_trace(run_dir / "trace.jsonl"))
    meta = {
        "run_id": run_id, "bundle": str(args.bundle), "mode": "baseline", "system_id": "B", "model": BASELINE_MODEL,
        "status": result.status, "error": result.error.model_dump() if result.error else None,
        "plan_version": session.plan_version, "prompt_version": session.prompt.version,
        "prompt_drafts": list(session.prompt.drafts), "elapsed_s": round(session.budget.elapsed_s, 1), **totals,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\nStatus: {result.status}" + (f" ({result.error.code}: {result.error.message})" if result.error else ""))
    print(f"LLM calls {totals['llm_calls']}, tool calls {totals['tool_calls']}, "
          f"tokens {totals['input_tokens']} in ({totals['cached_input_tokens']} cached) / {totals['output_tokens']} out, "
          f"cost ${totals['cost_usd']:.4f}, {meta['elapsed_s']} s")
    if session.prompt.drafts:
        print(f"Note: prompt sections still DRAFT: {', '.join(session.prompt.drafts)}")
    print(f"Files: {run_dir}")
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
