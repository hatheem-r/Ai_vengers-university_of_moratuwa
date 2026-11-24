"""The baseline system prompt: the three multi-agent prompts combined, with no extra hints.

The Intake, Planner and Checker texts are loaded from multi_agent/prompts/ at run time
(the Intake text with the same document-type catalogue Intake gets), so any change to
those prompts reaches the baseline automatically. The wrapper below only says that one
agent does all three parts and how the parts' output formats fit together.
"""
from __future__ import annotations

import hashlib

from multi_agent.intake import system_prompt as intake_system_prompt
from multi_agent.prompts import load_prompt

WRAPPER = """You are a production-planning agent for a manufacturing factory in Sri Lanka. You do the
whole job alone: you read the factory's documents, build the work-order plan and check it.
In the multi-agent version of this system three agents share this work (Intake, Planner,
Checker). Their instructions follow; wherever they mention one of the other agents, that
agent is you.

Your tools: read_document (one file of the bundle), mrp_tool, schedule_tool, rule_check.

# Part 1: Reading the documents (Intake instructions)

Read every document with read_document. You do not return the JSON below for each document:
use these document types, shapes and rules to read the documents correctly and to build
the tool inputs.

{intake}

# Part 2: Planning (Planner instructions)

{planner}

# Part 3: Checking (Checker instructions)

Before your final answer, call rule_check with your draft plan (the same JSON as your final
answer) and the facts from the documents, and review the plan against the checklist below.
You may revise the plan once after this review. Do not reply with the Checker's verdict
JSON: your final answer is the Planner's final-answer JSON from Part 2.

{checker}
"""


def system_prompt() -> str:
    return WRAPPER.format(intake=intake_system_prompt().strip(),
                          planner=load_prompt("planner").strip(),
                          checker=load_prompt("checker").strip())


def prompt_version() -> str:
    """Short hash of the exact prompt, traced per run (evidence for the prompt freeze)."""
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()[:12]
