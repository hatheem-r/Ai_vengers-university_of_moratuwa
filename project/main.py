"""Work Order Planner: run one system on one bundle and record the run.

    cd project
    uv run python main.py data/bundles/dev_A_S1_clean                       # multi-agent, M-mix
    uv run python main.py data/bundles/dev_A_S1_clean --mode baseline       # single agent (B)
    uv run python main.py data/bundles/dev_A_S1_clean --profile sonnet      # multi-agent, M-S
    options: --fake (no API key; fake models fed from the bundle's extraction_key.json)
             --approve (approve the first plan)   --runs runs (where run folders go)

Each run is recorded in runs/<run_id>/: trace.jsonl, plan_v<N>.json, meta.json (tokens, cost,
timings), approval.json. Real models need ANTHROPIC_API_KEY (copy .env.example to .env).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core import runner
from core.schemas import RunConfig
from core.tracing import console_printer


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Build a production work-order plan from a document bundle.")
    ap.add_argument("bundle", type=Path, help="bundle folder (with inputs/) or an inputs folder")
    ap.add_argument("--mode", choices=["multi_agent", "baseline"], default="multi_agent")
    ap.add_argument("--profile", choices=["mix", "sonnet"], default="mix")
    ap.add_argument("--fake", action="store_true", help="fake models (needs the bundle's extraction_key.json)")
    ap.add_argument("--approve", action="store_true", help="approve the first plan")
    ap.add_argument("--runs", type=Path, default=Path("runs"))
    args = ap.parse_args()

    if args.fake:
        from multi_agent import fakes
        fakes.install(extraction_key=json.loads((args.bundle / "extraction_key.json").read_text()))
    elif not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, or use --fake.",
              file=sys.stderr)
        return 1

    config = RunConfig(mode=args.mode, model_profile=args.profile)
    result, run_dir = runner.run_recorded(args.bundle, config, runs_root=args.runs,
                                          listeners=[console_printer], approve=args.approve)
    m = json.loads((run_dir / "meta.json").read_text())["metrics"]
    print(f"\n{config.system_id}: {result.status}"
          + (f"  error: {result.error.code}: {result.error.message}" if result.error else ""))
    print(f"{m['llm_calls']} LLM calls, {m['tool_calls']} tool calls, {m['tokens']} tokens, "
          f"${m['cost_usd']:.4f}{'' if m['cost_complete'] else ' (fake models are not priced)'}, "
          f"{m['active_seconds']} s active")
    print(f"run recorded in {run_dir}")
    return 0 if result.status != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
