"""Run the single-agent baseline on one bundle from the command line.

    cd project
    python -m baseline data/bundles/dev_A_S1_clean            # real model (needs ANTHROPIC_API_KEY)
    python -m baseline data/bundles/dev_A_S1_clean --fake     # fake model, no key
    options: --approve   --reject "notes"   --out plan.json

Prints the trace as it happens and writes the plan JSON (default runs/<run_id>/plan_v<N>.json).
Use dev bundles only (TASKS.md §0.4).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import runner
from core.schemas import ApprovalDecision, RunConfig, TraceEvent


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m baseline")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--fake", action="store_true", help="fake model fed from the bundle's extraction_key.json")
    ap.add_argument("--reject", metavar="NOTES", help="reject the first plan with these notes (re-plan once)")
    ap.add_argument("--approve", action="store_true", help="approve the (last) plan")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    # Windows consoles default to cp1252, which cannot print "→" in hand-off summaries; an
    # exception inside emit() would fail the run, so print UTF-8 and replace what can't be shown.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.fake:
        from . import fakes
        fakes.install(json.loads((args.bundle / "extraction_key.json").read_text()))

    totals = {"in": 0, "out": 0, "calls": 0}

    def emit(e: TraceEvent) -> None:
        tok = ""
        if e.type == "llm_call":
            totals["in"] += e.input_tokens or 0
            totals["out"] += e.output_tokens or 0
            totals["calls"] += 1
            tok = f"  [{e.input_tokens} in / {e.output_tokens} out, {e.latency_ms} ms]"
        print(f"{e.ts:%H:%M:%S} {e.type:<15} {e.agent or '':<8} {e.item or ''} {e.summary}{tok}", flush=True)

    rid = runner.new_run_id()
    result = runner.start_run(rid, args.bundle, RunConfig(mode="baseline"), emit)
    if args.reject and result.status == "awaiting_approval":
        result = runner.resume_run(rid, ApprovalDecision(decision="reject", notes=args.reject), emit)
    if args.approve and result.status == "awaiting_approval":
        result = runner.resume_run(rid, ApprovalDecision(decision="approve", notes="CLI --approve"), emit)
    print(f"\nstatus: {result.status}" + (f"  error: {result.error.code}: {result.error.message}" if result.error else ""))
    print(f"LLM calls {totals['calls']}, tokens {totals['in']} in / {totals['out']} out")
    if result.plan:
        out = args.out or Path("runs") / rid / f"plan_v{result.plan_version}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.plan.model_dump_json(indent=2))
        print(f"plan v{result.plan_version} written to {out}")


if __name__ == "__main__":
    main()
