"""Runner interface: the only way the API (Jisfaaz) and the benchmark (Ajey) start a run.

    start_run(run_id, bundle_dir, config, emit)  -> RunResult   blocks until awaiting_approval / completed / failed
    resume_run(run_id, decision, emit)            -> RunResult   approve -> completed; reject -> re-plan

`mode="multi_agent"` goes to project/multi_agent/graph.py (Hatheem) and `mode="baseline"`
to project/baseline/agent.py (Shazan). Each backend module exposes two functions:

    def start(run_id: str, inputs_dir: Path, config: RunConfig, emit: Emit) -> RunResult
    def resume(run_id: str, decision: ApprovalDecision, config: RunConfig, emit: Emit) -> RunResult

and must call emit() for every event, using the agent names in API.md §4.4.

The runner itself:
- passes only `<bundle>/inputs/` to a backend (hidden-data rule, TASKS.md §0.3);
- emits `run_start` / `run_end` (backends emit everything in between);
- turns any exception into a failed RunResult, so callers never see a traceback;
- with `config.auto_approve`, approves the first plan straight away (benchmark).

Sequence numbers: `emit` (tracing.py) owns `seq` and renumbers every event it writes;
the runner's own events carry seq=1 as a placeholder.
"""
from __future__ import annotations

import importlib
import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from .schemas import (FACTORY_TZ, ApprovalDecision, RunConfig, RunError, RunResult, TraceEvent,
                      TraceEventType)

Emit = Callable[[TraceEvent], None]

BACKENDS: dict[str, str] = {
    "multi_agent": "multi_agent.graph",
    "baseline": "baseline.agent",
}


class Backend(Protocol):
    def start(self, run_id: str, inputs_dir: Path, config: RunConfig, emit: Emit) -> RunResult: ...
    def resume(self, run_id: str, decision: ApprovalDecision, config: RunConfig, emit: Emit) -> RunResult: ...


class BudgetExceeded(RuntimeError):
    """Raised by the harness when a run hits a budget; becomes error.code='budget_exceeded'."""


# run_id -> config, so resume_run knows which backend to call. In-process only: the API keeps
# runs in one process. After a restart, pass `config=` to resume_run (read from meta.json).
_ACTIVE: dict[str, RunConfig] = {}
_LOCK = threading.Lock()


def new_run_id(now: Optional[datetime] = None) -> str:
    """'run_20261123_093000_a1b2' (API.md §4.3)."""
    now = now or datetime.now(FACTORY_TZ)
    return f"run_{now:%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"


def start_run(run_id: str, bundle_dir: Path, config: RunConfig, emit: Emit) -> RunResult:
    """Runs until the plan is waiting for approval (or failed). Blocks."""
    with _LOCK:
        if run_id in _ACTIVE:
            raise ValueError(f"run {run_id} already exists")
        _ACTIVE[run_id] = config

    bundle_dir = Path(bundle_dir)
    inputs_dir = bundle_dir / "inputs" if (bundle_dir / "inputs").is_dir() else bundle_dir
    _event(emit, run_id, "run_start", f"{config.system_id} on {bundle_dir.name}",
           {"bundle_id": bundle_dir.name, "config": config.model_dump(mode="json")})

    if not inputs_dir.is_dir():
        return _finish(emit, run_id, _failed(run_id, "internal", f"bundle folder not found: {inputs_dir}"))

    result = _call(run_id, emit, lambda b: b.start(run_id, inputs_dir, config, emit))
    if config.auto_approve and result.status == "awaiting_approval":
        decision = ApprovalDecision(decision="approve", notes="auto_approve (benchmark)",
                                    decided_at=datetime.now(FACTORY_TZ))
        _event(emit, run_id, "human_decision", "Auto-approved", decision.model_dump(mode="json"),
               agent="approval")
        result = _call(run_id, emit, lambda b: b.resume(run_id, decision, config, emit))
    return _finish(emit, run_id, result)


def resume_run(run_id: str, decision: ApprovalDecision, emit: Emit,
               config: Optional[RunConfig] = None) -> RunResult:
    """Approve -> completed. Reject with notes -> re-plan, then back to awaiting approval."""
    with _LOCK:
        config = config or _ACTIVE.get(run_id)
        if config is None:
            raise KeyError(f"unknown run {run_id}; pass config= when resuming after a restart")
        _ACTIVE[run_id] = config

    _event(emit, run_id, "human_decision",
           "Approved" if decision.decision == "approve" else f"Rejected: {decision.notes}",
           decision.model_dump(mode="json"), agent="approval")
    result = _call(run_id, emit, lambda b: b.resume(run_id, decision, config, emit))
    return _finish(emit, run_id, result)


# --- recorded runs (CLI, benchmark): runs/<run_id>/ with trace, plans and meta --------

def run_recorded(bundle_dir: Path, config: RunConfig, *, runs_root: Path = Path("runs"),
                 run_id: Optional[str] = None, listeners: Sequence[Emit] = (),
                 approve: bool = False) -> tuple[RunResult, Path]:
    """start_run with a TraceWriter on runs/<run_id>/, then write plan_v<N>.json and meta.json.
    `approve=True` approves the first plan (same as config.auto_approve, but traced as a CLI
    decision). Returns (result, run folder)."""
    from .tracing import TraceWriter, write_result
    run_id = run_id or new_run_id()
    run_dir = Path(runs_root) / run_id
    writer = TraceWriter(run_dir, listeners=listeners)
    bundle_dir = Path(bundle_dir)
    result = start_run(run_id, bundle_dir, config, writer)
    write_result(run_dir, result, config, bundle_id=bundle_dir.name)
    if approve and result.status == "awaiting_approval":
        result = resume_recorded(run_dir, ApprovalDecision(decision="approve", notes="approved from the CLI"),
                                 config=config, listeners=listeners)
    return result, run_dir


def resume_recorded(run_dir: Path, decision: ApprovalDecision, *, config: Optional[RunConfig] = None,
                    listeners: Sequence[Emit] = ()) -> RunResult:
    """resume_run for a recorded run: appends to its trace and rewrites meta.json."""
    from .tracing import TraceWriter, write_result
    run_dir = Path(run_dir)
    writer = TraceWriter(run_dir, listeners=listeners)
    result = resume_run(run_dir.name, decision, writer, config=config)
    write_result(run_dir, result, config)
    path = run_dir / "approval.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    decisions.append(decision.model_dump(mode="json"))
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    return result


# --- helpers -------------------------------------------------------------------

def _load_backend(mode: str) -> Backend:
    return importlib.import_module(BACKENDS[mode])  # type: ignore[return-value]


def _call(run_id: str, emit: Emit, fn: Callable[[Backend], RunResult]) -> RunResult:
    config = _ACTIVE[run_id]
    try:
        backend = _load_backend(config.mode)
    except ImportError as exc:
        return _failed(run_id, "internal", f"{config.mode} backend not available yet: {exc}")
    try:
        result = fn(backend)
    except BudgetExceeded as exc:
        return _failed(run_id, "budget_exceeded", str(exc))
    except Exception as exc:  # noqa: BLE001 - every failure must become a RunResult
        code = _error_code(exc)
        _event(emit, run_id, "error", f"{type(exc).__name__}: {exc}")
        return _failed(run_id, code, f"{type(exc).__name__}: {exc}")
    if not isinstance(result, RunResult):
        return _failed(run_id, "internal", f"backend returned {type(result).__name__}, not RunResult")
    return result


def _error_code(exc: Exception) -> str:
    """schema_invalid: no valid answer after repair; model_error: refusal, turn limit or an API
    error left after retries; internal: anything else (a bug)."""
    from .harness import HarnessError, SchemaRepairFailed
    if isinstance(exc, SchemaRepairFailed):
        return "schema_invalid"
    if isinstance(exc, HarnessError) or type(exc).__module__.split(".")[0] in ("anthropic", "httpx", "httpx2"):
        return "model_error"
    return "internal"


def _failed(run_id: str, code: str, message: str) -> RunResult:
    return RunResult(run_id=run_id, status="failed", error=RunError(code=code, message=message))  # type: ignore[arg-type]


def _finish(emit: Emit, run_id: str, result: RunResult) -> RunResult:
    if result.status in ("completed", "failed"):
        with _LOCK:
            _ACTIVE.pop(run_id, None)
        _event(emit, run_id, "run_end", f"Run {result.status}",
               {"status": result.status, "plan_version": result.plan_version,
                "error": result.error.model_dump() if result.error else None})
    return result


def _event(emit: Emit, run_id: str, type_: TraceEventType, summary: str,
           payload: Optional[dict] = None, agent: Optional[str] = None) -> None:
    emit(TraceEvent(run_id=run_id, seq=1, ts=datetime.now(FACTORY_TZ), type=type_,
                    agent=agent, summary=summary, payload=payload))  # type: ignore[arg-type]
