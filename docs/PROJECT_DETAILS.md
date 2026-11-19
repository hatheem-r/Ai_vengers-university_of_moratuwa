# Project Details

**Project:** Work Order Planner
**Team:** Ai vengers, University of Moratuwa
**Competition:** Innodata AI Catalyst 2026, Manufacturing track

## Problem

Production planners in small and mid-sized factories build work orders by hand from scattered documents: BOMs, customer orders, inventory sheets, purchase orders and invoices, machine capacity data and maintenance reports. These arrive as PDFs, scans, Excel and CSV files in different formats. Reconciling them is slow and error-prone, and missed shortages or machine conflicts lead to late orders.

## Objectives

1. Read mixed-format factory documents automatically and turn them into validated, structured data.
2. Produce a feasible production work order plan that accounts for material availability, open POs, machine capacity and maintenance downtime.
3. Flag risks (shortages, due-date risk, PO and invoice mismatches, compliance gaps) before the plan is released.
4. Keep a human planner in control through an approval step.
5. Show, through a fair benchmark, whether a multi-agent design beats a single agent (same model, same tools) and human planners on plan quality, completion time and cost.

## Features

- Parallel document intake for PDFs (including scanned pages), Excel and CSV files
- Schema-validated extraction with automatic repair retry
- Material requirements planning: BOM explosion, netting against stock and open POs, shortage detection
- Constraint-based scheduling with OR-Tools CP-SAT, respecting capacity and maintenance windows
- Independent Checker agent with one revision loop
- Human-in-the-loop approval before release
- Per-run traces of every agent call, with tokens, cost and latency
- Streamlit interface (planned)

## Architecture

See [Workflow.md](Workflow.md). In short: Intake Agent (parallel) → Planner Agent with `mrp_tool` and `schedule_tool` → Checker Agent (at most one revision) → human approval.

## Tech stack

| Area | Technology |
|---|---|
| Language | Python 3.11+ |
| Agent orchestration | LangGraph (`StateGraph`, `Send`, conditional edges, `interrupt()`, checkpointer) |
| Agent framework | LangChain (`create_agent`, middleware), `langchain-anthropic` |
| LLMs | Claude Haiku 5.5, Claude Sonnet 5.5 (Anthropic API) |
| Scheduling | Google OR-Tools (CP-SAT solver) |
| Data handling | pandas, openpyxl, pdfplumber |
| Validation | Pydantic |
| Configuration | python-dotenv |
| UI | Streamlit |

## Benchmark plan

| System | Description |
|---|---|
| Single-agent baseline | One Claude Sonnet 5.5 agent with the same tools, prompts, output schema and budgets |
| Multi-agent, same model | Intake, Planner and Checker all on Claude Sonnet 5.5 (main comparison) |
| Multi-agent, mixed models | Haiku 5.5 for Intake and Checker, Sonnet 5.5 for Planner (cost-optimised) |
| Human baseline | Team members who did not build the test cases plan a subset of the bundles by hand |

Metrics: plan quality (extraction accuracy, material requirements, schedule feasibility, risk detection), completion time and cost per run. Scored automatically against answer keys for synthetic factory document bundles. All expected gains are hypotheses until the benchmark is run.

## AI models and tools used

As required by the competition rules, we declare every AI model and AI tool used in this project.

### In the product

| Model / tool | Provider | Used for |
|---|---|---|
| Claude Haiku 5.5 (`claude-haiku-5-5`) | Anthropic | Intake Agent (document extraction) and Checker Agent (plan review) |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | Anthropic | Planner Agent and the single-agent baseline |
| LangGraph / LangChain | LangChain | Agent orchestration and tool-calling loop |
| OR-Tools CP-SAT | Google | Production scheduling (not an AI model; a constraint solver) |

Development fallbacks (only if used, to be confirmed): Gemini 3.5 Flash-Lite and Gemini 3.8 Flash (Google), gpt-oss-120b on Groq.

### In development

| Tool | Provider | Used for |
|---|---|---|
| Claude (Claude Code and claude.ai) | Anthropic | AI coding assistant: research, planning documents, proposal drafting, repository scaffolding and code assistance |

> TODO: add any other AI tools the team uses (for example GitHub Copilot or ChatGPT) before submission.

## Reused components and limitations

> TODO: list reused open-source components and any mocked or incomplete parts (the test data is synthetic).
