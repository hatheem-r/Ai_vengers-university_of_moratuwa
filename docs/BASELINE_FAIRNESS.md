# Baseline fairness: single agent vs multi-agent

TASKS.md §2 asks for proof that the single-agent baseline (`B`) and the multi-agent system
use the same model settings, tools, tool descriptions, budgets and retries, so that only the
architecture differs. The main comparison is `B` against `M-S` (multi-agent, every agent on
Sonnet 5.5). `M-mix` uses cheaper models on purpose and is reported as the cost result.

Every row below points to the code that makes it true. Checked on 25 Nov 2026 and updated 26 Nov after merging main, against the
code in `project/`.

## Same in both systems

| Setting | Multi-agent (`M-S`) | Baseline (`B`) | Where |
|---|---|---|---|
| Model | Sonnet 5.5 for Intake, Planner, Checker | Sonnet 5.5 | `core/models.py` (`PROFILES["sonnet"]`, `BASELINE_MODEL`) |
| Model settings | `max_tokens` 16,384, SDK retries off (`max_retries=0`), no sampling overrides | same | `core/models.py`, `core/harness.py` `build_chat_model` |
| Retries | harness retries with backoff and `retry-after` | same | `core/harness.py` `invoke_llm` |
| Every LLM call | `invoke_llm()`: budget check, retries, prompt caching, `llm_call` trace | same function (via `multi_agent/llm.py` `call_llm`) | `core/harness.py` |
| Prompt caching | system-prompt breakpoint, last tool definition, automatic caching | same | `core/harness.py` `invoke_llm` |
| Run budget | 40 LLM calls, 1.5M tokens, 600 s per run | same | `core/schemas.py` `Budget`, `core/harness.py` `RunContext` |
| Over budget | run ends `failed`, `error.code="budget_exceeded"` | same | `core/runner.py` |
| Tools | `mrp_tool`, `schedule_tool`, `rule_check` from `project/tools/` | same code | `multi_agent/tools.py` `run_tool` |
| Tool descriptions | `multi_agent/tools.py` `SPECS` | same `SPECS` | `multi_agent/tools.py` |
| How a document reaches the model | `intake.document_content` (text, sheets, page images for scans) | same function, via `read_document` | `multi_agent/intake.py`, `baseline/agent.py` |
| Hidden data | runner passes only `inputs/` | same | `core/runner.py` |
| Prompts | `multi_agent/prompts/{intake,planner,checker}.md` | the same three texts, loaded at run time, in one prompt with a short wrapper and no extra hints | `baseline/prompt.py` |
| Final answer to plan | `PlannerDraft` + `assemble_plan` | same | `multi_agent/planner.py` |
| Invalid final answer | error sent back: "Your final answer could not be used…" | same message | `planner.py`, `baseline/agent.py` |
| Human reject | notes go back to the planning step, new plan version | same (notes as a new message in the same conversation) | `multi_agent/graph.py`, `baseline/agent.py` |
| Traces | same `TraceEvent` format and cost table | same | `core/tracing.py` |

## Differences (the architecture, stated openly)

| | Multi-agent | Baseline | Why it is fair |
|---|---|---|---|
| Agents and context | Intake ×N, Planner, Checker, each with its own context | one agent, one context | This is the thing being compared. |
| Reading documents | one Intake call per document, in parallel (`Send`) | `read_document` calls inside the one conversation | Same content per document; only who reads it differs. |
| Agent loop | Planner: `core/harness.py` `run_tool_loop`, 8 turns per plan version | its own loop in `baseline/agent.py` over the same `invoke_llm`, 40 turns | The baseline also reads the documents. Both are bounded by the same 40-call run budget. |
| Review | Checker agent, at most 1 revision (enforced in code) | the agent calls `rule_check` itself; "revise once" is a prompt instruction, not enforced in code | The rule checks are the same code. Enforcing a limit inside one agent's loop would mean adding a mechanism the baseline doesn't otherwise have. |
| `rule_check` input | the Checker passes the full plan | the agent passes its draft; the plan is assembled from its latest tool outputs exactly as for its final answer | Same checks on the same plan. |
| Schema repair | Intake and Checker: one repair call each | repairs happen inside its loop | Same error message for the final answer. |

## Not yet verified

- No real benchmark run yet: these settings are checked in code and in tests with fake models.
- The combined baseline prompt follows the multi-agent prompts automatically, so the prompt
  freeze (Thu 26 Nov, 20:00) covers both systems at once. `prompt_version` is traced per run.
