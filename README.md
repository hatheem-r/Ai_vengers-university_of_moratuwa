# Work Order Planner

**Team Ai vengers, University of Moratuwa** · Innodata AI Catalyst 2026 (Manufacturing track)

Work Order Planner is a multi-agent system, built on LangGraph, that turns a factory's everyday documents into a production work order plan. It reads bills of materials (BOMs), customer orders, inventory sheets, purchase orders and invoices, machine capacity data and maintenance reports, works out material needs and shortages, schedules production around machine capacity and downtime, checks the plan for risks, and hands it to a human planner for approval.

## Why multi-agent

| Agent | Model | Job |
|---|---|---|
| Intake Agent | Claude Haiku 5.5 | Reads PDFs (including scans), Excel and CSV files in parallel and returns validated JSON |
| Planner Agent | Claude Sonnet 5.5 | Calls `mrp_tool` (material requirements) and `schedule_tool` (OR-Tools CP-SAT), then makes the judgement calls: priority, split or expedite, substitutes |
| Checker Agent | Claude Haiku 5.5 | Reviews the plan independently (`rule_check`) and can send it back to the Planner once |
| Human approval | Planner (person) | Approves or rejects the final plan with notes before release |

We benchmark this system against a single-agent baseline (same model, same tools) and against human planners, on plan quality, completion time and cost. See [docs/Workflow.md](docs/Workflow.md) and [docs/PROJECT_DETAILS.md](docs/PROJECT_DETAILS.md).

## Repository structure

```
.
├── project/
│   ├── api/               # FastAPI server + MOCK=1 sample endpoints (project/api/mocks/)
│   ├── main.py            # CLI entry point
│   └── requirements.txt   # Python dependencies
├── frontend/              # React 18 + Vite + TypeScript industrial control UI
├── tests/                 # Unit & API contract tests (pytest)
├── docs/
│   ├── Proposal.docx      # project proposal
│   ├── Workflow.md        # agent workflow diagram
│   └── PROJECT_DETAILS.md # objectives, features, tech stack, AI declaration
├── .env.example           # environment variable template (copy to .env)
└── README.md
```

## Setup

### 1. Python Backend (`project/api`)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r project/requirements.txt
cp .env.example .env               # add ANTHROPIC_API_KEY for live (MOCK=0) runs
```

### 2. Web Frontend (`frontend/`)

```bash
cd frontend
npm install
```

## Run

### Option A: Mock Mode (`MOCK=1` — instant UI walkthrough, no API key required)

Start the FastAPI server in mock mode (port `8000`):

```bash
# macOS / Linux
MOCK=1 uvicorn project.api.app:app --reload --port 8000

# Windows PowerShell
$env:MOCK="1"; uvicorn project.api.app:app --reload --port 8000
```

In a second terminal, start the React + Vite UI (port `5173`):

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`. Whenever `MOCK=1` is active, every response carries `X-Mock: true` and the header displays an unmistakable **`MOCK DATA`** amber pill badge.

### Option B: Live Runs (`MOCK=0` — connected to `project/core/runner.py`)

```bash
# macOS / Linux
MOCK=0 uvicorn project.api.app:app --reload --port 8000

# Windows PowerShell
$env:MOCK="0"; uvicorn project.api.app:app --reload --port 8000
```

### CLI Entry Point

```bash
python project/main.py --input path/to/document/bundle
```

### API Contract Tests

```bash
pytest tests/test_api.py
```

## Team

- **Hatheem Rafee** — Multi-agent LangGraph pipeline & synthetic factory bundle generator
- **Shazan Shaheed** — Single-agent baseline, shared harness & deterministic planning tools
- **Ajey Abishek** — Evaluation scorer, benchmark orchestration & Intake model evaluation
- **Jiffry Jizfaaz** — React + Vite industrial web UI & FastAPI layer

## License

MIT (University of Moratuwa · Innodata AI Catalyst 2026)
