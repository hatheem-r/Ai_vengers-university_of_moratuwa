"""Work Order Planner: entry point.

Multi-agent pipeline (LangGraph):
    Intake Agent (Claude Haiku 5.5, parallel)
      -> Planner Agent (Claude Sonnet 5.5, with mrp_tool and schedule_tool)
      -> Checker Agent (Claude Haiku 5.5, at most one revision)
      -> Human approval

This is a placeholder. The graph is not implemented yet.
"""

import argparse
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Build a production work order plan from factory documents.")
    parser.add_argument("--input", help="Folder containing the document bundle (BOMs, orders, inventory, POs, ...)")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.", file=sys.stderr)
        return 1

    print("Work Order Planner")
    print(f"Input bundle: {args.input or '(none given)'}")
    print("TODO: build and run the LangGraph workflow (Intake -> Planner -> Checker -> Human approval).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
