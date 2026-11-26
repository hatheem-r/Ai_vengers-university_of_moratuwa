"""Run the multi-agent pipeline on one bundle from the command line.

    cd project
    uv run python -m multi_agent data/bundles/dev_A_S1_clean            # real models (needs ANTHROPIC_API_KEY)
    uv run python -m multi_agent data/bundles/dev_A_S1_clean --fake     # fake models, no key
    options: --profile sonnet   --approve   --runs runs

Prints the trace as it happens and records the run in runs/<run_id>/ (trace.jsonl,
plan_v<N>.json, meta.json with tokens, cost and timings).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import runner
from core.schemas import RunConfig
from core.tracing import console_printer


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m multi_agent")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--profile", choices=["mix", "sonnet"], default="mix")
    ap.add_argument("--fake", action="store_true", help="fake models fed from the bundle's extraction_key.json")
    ap.add_argument("--approve", action="store_true", help="approve the first plan")
    ap.add_argument("--runs", type=Path, default=Path("runs"), help="folder for run records")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.fake:
        from . import fakes
        fakes.install(extraction_key=json.loads((args.bundle / "extraction_key.json").read_text()))

    config = RunConfig(mode="multi_agent", model_profile=args.profile)
    result, run_dir = runner.run_recorded(args.bundle, config, runs_root=args.runs,
                                          listeners=[console_printer], approve=args.approve)
    meta = json.loads((run_dir / "meta.json").read_text())
    m = meta["metrics"]
    print(f"\nstatus: {result.status}" + (f"  error: {result.error.code}: {result.error.message}" if result.error else ""))
    print(f"{m['llm_calls']} LLM calls, {m['tool_calls']} tool calls, {m['tokens']} tokens, "
          f"${m['cost_usd']:.4f}, {m['active_seconds']} s active")
    print(f"run recorded in {run_dir}")


if __name__ == "__main__":
    main()
