# Work Order Planner

**Team Ai vengers, University of Moratuwa** · Innodata AI Catalyst 2026 (Manufacturing track)

Work Order Planner is a multi-agent system, built on LangGraph, that turns a factory's everyday documents into a production work order plan. It reads bills of materials (BOMs), customer orders, inventory sheets, purchase orders and invoices, machine capacity data and maintenance reports, works out material needs and shortages, schedules production around machine capacity and downtime, checks the plan for risks, and hands it to a human planner for approval.

## Why multi-agent

| Agent | Model | Job |
|---|---|---|
| Intake Agent | Claude Haiku 5.5 | Reads PDFs (including scans), Excel and CSV files in parallel and returns validated JSON |
| Planner Agent | Claude Sonnet 5.5 | Calls `mrp_tool` (material requirements) and `schedule_tool` (OR-Tools CP-SAT), then makes the judgement calls: priority, split or expedite, substitutes |
| Checker Agent | Claude Haiku 5.5 | Reviews the plan independently and can send it back to the Planner once |
| Human approval | Planner (person) | Approves or rejects the final plan before release |

We benchmark this system against a single-agent baseline (same model, same tools) and against human planners, on plan quality, completion time and cost. See [docs/Workflow.md](docs/Workflow.md) and [docs/PROJECT_DETAILS.md](docs/PROJECT_DETAILS.md).

## Repository structure

```
.
├── project/
│   ├── main.py            # entry point
│   └── requirements.txt   # Python dependencies
├── docs/
│   ├── Proposal.docx      # project proposal
│   ├── Workflow.md        # agent workflow diagram
│   └── PROJECT_DETAILS.md # objectives, features, tech stack, AI declaration
├── project-demo.mp4       # demo video
├── .env.example           # environment variable template (copy to .env)
└── README.md
```

## Setup

> TODO: confirm once the MVP is runnable.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r project/requirements.txt
cp .env.example .env               # then add your ANTHROPIC_API_KEY
```

## Run

> TODO: document the CLI and Streamlit UI once implemented.

```bash
python project/main.py --input path/to/document/bundle
```

## Team

> TODO: add team member names.

## License

> TODO
