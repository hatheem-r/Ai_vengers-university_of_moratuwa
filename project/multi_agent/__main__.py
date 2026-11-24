"""Run the multi-agent pipeline on one bundle from the command line.

    cd project
    uv run python -m multi_agent data/bundles/dev_A_S1_clean            # real models (needs ANTHROPIC_API_KEY)
    uv run python -m multi_agent data/bundles/dev_A_S1_clean --fake     # fake models, no key
    options: --profile sonnet   --approve   --out plan.json

Prints the trace as it happens and writes the plan JSON (default runs/<run_id>/plan.json).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import runner
from core.schemas import ApprovalDecision, RunConfig, TraceEvent


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m multi_agent")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--profile", choices=["mix", "sonnet"], default="mix")
    ap.add_argument("--fake", action="store_true", help="fake models fed from the bundle's extraction_key.json")
    ap.add_argument("--approve", action="store_true", help="approve the first plan")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.fake:
        from . import fakes
        fakes.install(extraction_key=json.loads((args.bundle / "extraction_key.json").read_text()))

    def emit(e: TraceEvent) -> None:
        tok = f"  [{e.input_tokens} in / {e.output_tokens} out, {e.latency_ms} ms]" if e.type == "llm_call" else ""
        print(f"{e.ts:%H:%M:%S} {e.type:<15} {e.agent or '':<8} {e.item or ''} {e.summary}{tok}", flush=True)

    rid = runner.new_run_id()
    config = RunConfig(mode="multi_agent", model_profile=args.profile)
    result = runner.start_run(rid, args.bundle, config, emit)
    if args.approve and result.status == "awaiting_approval":
        result = runner.resume_run(rid, ApprovalDecision(decision="approve", notes="CLI --approve"), emit)
    print(f"\nstatus: {result.status}" + (f"  error: {result.error.code}: {result.error.message}" if result.error else ""))
    if result.plan:
        out = args.out or Path("runs") / rid / "plan.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.plan.model_dump_json(indent=2))
        print(f"plan v{result.plan_version} written to {out}")


if __name__ == "__main__":
    main()
