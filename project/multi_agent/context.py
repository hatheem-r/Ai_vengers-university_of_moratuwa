"""Per-run context: the emit callback, budget counters and helpers for trace events.

Graph state must stay JSON-like (it is checkpointed), so the emit callback and the
counters live here, keyed by run_id, and nodes look them up with `get_ctx(run_id)`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from core.schemas import FACTORY_TZ, AgentName, RunConfig, TraceEvent, TraceEventType

Emit = Callable[[TraceEvent], None]


class BudgetError(RuntimeError):
    """Fallback when core.runner.BudgetExceeded cannot be imported."""


def _budget_exc() -> type[Exception]:
    try:
        from core.runner import BudgetExceeded
        return BudgetExceeded
    except ImportError:  # pragma: no cover
        return BudgetError


@dataclass
class RunContext:
    run_id: str
    config: RunConfig
    emit_fn: Emit
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    active_seconds: float = 0.0          # time spent running (not waiting for the human)
    _segment_start: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- time segments: start()/resume() each open one ---------------------------
    def begin_segment(self) -> None:
        self._segment_start = time.monotonic()

    def end_segment(self) -> None:
        if self._segment_start is not None:
            self.active_seconds += time.monotonic() - self._segment_start
            self._segment_start = None

    def elapsed(self) -> float:
        running = time.monotonic() - self._segment_start if self._segment_start else 0.0
        return self.active_seconds + running

    # --- budget -------------------------------------------------------------------
    def check_budget(self) -> None:
        b = self.config.budget
        if self.llm_calls >= b.max_llm_calls:
            raise _budget_exc()(f"LLM call budget reached ({b.max_llm_calls} calls)")
        if self.input_tokens + self.output_tokens >= b.max_tokens:
            raise _budget_exc()(f"token budget reached ({b.max_tokens} tokens)")
        if self.elapsed() >= b.max_seconds:
            raise _budget_exc()(f"time budget reached ({b.max_seconds:.0f} s)")

    def record_llm(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.llm_calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    def reserve_llm_call(self) -> None:
        """Check the budget before a call (thread-safe; Intake calls run in parallel)."""
        with self._lock:
            self.check_budget()

    # --- events -------------------------------------------------------------------
    def emit(self, type_: TraceEventType, agent: Optional[AgentName] = None, summary: str = "",
             **fields: Any) -> None:
        event = TraceEvent(run_id=self.run_id, seq=1, ts=datetime.now(FACTORY_TZ), type=type_,
                           agent=agent, summary=summary, **fields)
        with self._lock:  # emit() may not be thread-safe; Intake nodes run concurrently
            self.emit_fn(event)

    def handoff(self, from_agent: AgentName, to_agent: AgentName, summary: str,
                payload: Optional[dict] = None) -> None:
        self.emit("handoff", agent=from_agent, from_agent=from_agent, to_agent=to_agent,
                  summary=summary, payload=payload)


_CTX: dict[str, RunContext] = {}
_CTX_LOCK = threading.Lock()


def open_ctx(run_id: str, config: RunConfig, emit: Emit) -> RunContext:
    with _CTX_LOCK:
        ctx = _CTX.get(run_id)
        if ctx is None:
            ctx = _CTX[run_id] = RunContext(run_id=run_id, config=config, emit_fn=emit)
        else:  # resume: keep counters, take the caller's current emit and config
            ctx.emit_fn, ctx.config = emit, config
    return ctx


def get_ctx(run_id: str) -> RunContext:
    try:
        return _CTX[run_id]
    except KeyError:
        raise RuntimeError(f"no run context for {run_id}; call graph.start() first") from None


def close_ctx(run_id: str) -> None:
    with _CTX_LOCK:
        _CTX.pop(run_id, None)


def null_ctx(config: Optional[RunConfig] = None) -> RunContext:
    """A context that drops events; used by extract_document() outside a run."""
    return RunContext(run_id="standalone", config=config or RunConfig(mode="multi_agent"),
                      emit_fn=lambda e: None)
