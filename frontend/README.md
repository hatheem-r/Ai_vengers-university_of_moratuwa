# Work Order Planner — Web Frontend (`frontend/`)

Industrial SCADA/MES-inspired React + Vite + TypeScript control interface for **Work Order Planner** (Team Ai vengers, University of Moratuwa).

## Stack

- **Framework:** React 18 + TypeScript + Vite
- **Styling:** Tailwind CSS + Radix UI primitives (`@radix-ui/react-dialog`, `tabs`, `tooltip`)
- **Motion:** Framer Motion (`150ms–200ms` transitions)
- **State & Polling:** TanStack Query v5 (`2s` live run & trace polling)
- **Charts & Scheduling:** Recharts (Evaluation benchmarks) + custom interactive horizontal `MachineGantt` with hatched maintenance downtime and overtime overlays
- **Icons:** `lucide-react` (`1.5px` stroke)

## Routes

| Route | View | Purpose |
|---|---|---|
| `/` | **Start** | Pick scenario bundle (Apparel / Light Assembly / Food Packing) or drag-and-drop documents; choose `M-mix`, `M-S`, or `B` |
| `/run/:runId` | **Live Run** | Horizontal LangGraph pipeline stepper (parallel Intake chips, `mrp_tool`/`schedule_tool`, Checker 1x loop, `interrupt()`) + live telemetry |
| `/run/:runId/review` | **Plan Review** | Machine Gantt chart, Work Orders with source citations, Materials & Suggested POs, Risk Register, Export (JSON/Excel CSV/PDF), Approve/Reject |
| `/run/:runId/trace` | **Trace** | Filterable JSONL hand-off timeline, slide-over JSON payload inspector drawer, and one-click `.jsonl` download |
| `/compare` | **Compare** | Side-by-side diff of Multi-Agent vs. Single-Agent Baseline on the same bundle (PQS, flags, violations, latency, tokens, cost) |
| `/evaluation` | **Evaluation** | Executive benchmark dashboard (`summary.json`): leaderboard, S1–S9 scenarios, mess degradation, 1/3/5-order scalability, and human baseline |

## Quick Start

```bash
npm install
npm run dev       # http://localhost:5173 (connects to http://localhost:8000/api/v1)
npm run build     # Type-checks with tsc -b and produces production bundle in dist/
```

Set `VITE_API_BASE_URL` if running the FastAPI backend on a non-default host/port. Press **`Ctrl/Cmd + K`** anywhere to open the Quick Jump Command Palette.
