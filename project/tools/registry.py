"""One place that builds the shared tools.

Both systems should get their tools from here, so the tools, their names and
their descriptions are identical (a fairness requirement, TASKS.md §2).
The multi-agent system gives each agent the subset it needs.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool

from project.tools.loaders import make_read_document_tool
from project.tools.mrp import make_mrp_tool
from project.tools.rules import make_rule_check_tool
from project.tools.schedule import make_schedule_tool

TOOL_NAMES = ("read_document", "mrp_tool", "schedule_tool", "rule_check")


def build_tools(bundle_dir: Path | str) -> dict[str, StructuredTool]:
    """All shared tools, keyed by name. ``read_document`` is bound to the bundle's inputs."""
    return {
        "read_document": make_read_document_tool(bundle_dir),
        "mrp_tool": make_mrp_tool(),
        "schedule_tool": make_schedule_tool(),
        "rule_check": make_rule_check_tool(),
    }
