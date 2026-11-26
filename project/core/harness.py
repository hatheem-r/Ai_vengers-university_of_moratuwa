"""The agent harness: the one way every LLM call and agent loop runs, for both systems.

The baseline (system B) and the multi-agent system (M-S, M-mix) must use exactly the same
model settings, budgets, retries, caching, schema repair and trace events (TASKS.md §0.1),
so all of that lives here and nowhere else:

    RunContext       per-run budget counters + emit(); one per run_id (open_ctx/get_ctx)
    build_chat_model the LangChain chat model for an agent (swappable with set_llm_factory)
    invoke_llm       one call: budget check -> prompt caching -> retry with backoff ->
                     token accounting -> one `llm_call` trace event
    call_json        one call whose reply must parse; one schema-repair retry by default
    run_tool_loop    the ReAct loop: call, run tool calls, repeat until a final answer
                     that `finish()` accepts (with schema repair), within max_turns

Budgets (40 LLM calls, 1.5M tokens, 600 s of active time by default) raise
core.runner.BudgetExceeded, which the runner turns into status=failed,
error.code="budget_exceeded".

Why a hand-written loop and not LangChain's `create_agent`: the loop is ~40 lines, it gives
the Planner a hook to turn its final answer into a plan (`finish`) and to send that answer
back for repair, and every step is traced in our own event format. The middleware the plan
asked for (retry, caps, schema repair, caching, tracing) is all here.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Sequence, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from . import models
from .models import ModelSpec
from .schemas import FACTORY_TZ, AgentName, RunConfig, TraceEvent, TraceEventType

Emit = Callable[[TraceEvent], None]
T = TypeVar("T")


class HarnessError(RuntimeError):
    """A call or loop that cannot go on (refusal, retries used up, no valid answer)."""


class SchemaRepairFailed(HarnessError):
    """The reply still did not parse after the repair attempts. Carries the last reply."""

    def __init__(self, message: str, reply: AIMessage, error: Exception) -> None:
        super().__init__(message)
        self.reply = reply
        self.error = error


def _budget_exc() -> type[Exception]:
    from .runner import BudgetExceeded  # runner imports nothing from here; no cycle at runtime
    return BudgetExceeded


# =====================================================================================
# Run context: budgets and events
# =====================================================================================

@dataclass
class RunContext:
    """Everything a run needs that cannot live in (checkpointed) graph state."""
    run_id: str
    config: RunConfig
    emit_fn: Emit
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    active_seconds: float = 0.0          # time spent running (not waiting for the human)
    _segment_start: Optional[float] = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

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

    def reserve_llm_call(self) -> None:
        """Check the budget before a call (thread-safe; Intake calls run in parallel)."""
        with self._lock:
            self.check_budget()

    def record_llm(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.llm_calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    def record_tool(self) -> None:
        with self._lock:
            self.tool_calls += 1

    def remaining(self) -> dict[str, float]:
        b = self.config.budget
        return {"llm_calls": b.max_llm_calls - self.llm_calls,
                "tokens": b.max_tokens - self.input_tokens - self.output_tokens,
                "seconds": round(b.max_seconds - self.elapsed(), 1)}

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
    """Create the run's context, or on resume keep its counters and take the new emit/config."""
    with _CTX_LOCK:
        ctx = _CTX.get(run_id)
        if ctx is None:
            ctx = _CTX[run_id] = RunContext(run_id=run_id, config=config, emit_fn=emit)
        else:
            ctx.emit_fn, ctx.config = emit, config
    return ctx


def get_ctx(run_id: str) -> RunContext:
    try:
        return _CTX[run_id]
    except KeyError:
        raise RuntimeError(f"no run context for {run_id}; start the run first") from None


def close_ctx(run_id: str) -> None:
    with _CTX_LOCK:
        _CTX.pop(run_id, None)


def null_ctx(config: Optional[RunConfig] = None) -> RunContext:
    """A context that drops events; for single calls outside a run (Intake model evaluation)."""
    return RunContext(run_id="standalone", config=config or RunConfig(mode="multi_agent"),
                      emit_fn=lambda e: None)


# =====================================================================================
# Chat models
# =====================================================================================

LlmFactory = Callable[[AgentName, ModelSpec], BaseChatModel]
_FACTORY: Optional[LlmFactory] = None


def set_llm_factory(factory: Optional[LlmFactory]) -> None:
    """Replace how chat models are built (None restores the real models). Used by fakes."""
    global _FACTORY
    _FACTORY = factory


def resolve_spec(model: "str | ModelSpec") -> ModelSpec:
    """A registry key ('haiku-5-5'), a ModelSpec, or a raw API model ID."""
    if isinstance(model, ModelSpec):
        return model
    if model in models.REGISTRY:
        return models.get(model)
    for spec in models.REGISTRY.values():
        if spec.model_id == model:
            return spec
    return ModelSpec(key=model, model_id=model)


def spec_for(agent: AgentName, config: RunConfig) -> ModelSpec:
    return models.model_for(agent, config.model_profile, config.mode)


def build_chat_model(agent: AgentName, spec: ModelSpec) -> BaseChatModel:
    """The chat model for one agent. The SDK's own retries are off: invoke_llm retries, so
    every retry is visible in the trace and counts against the time budget."""
    if _FACTORY is not None:
        return _FACTORY(agent, spec)
    if spec.provider != "anthropic":
        raise ValueError(f"provider {spec.provider!r} is not wired up yet (model {spec.key})")
    from langchain_anthropic import ChatAnthropic  # lazy: tests never need it
    return ChatAnthropic(model=spec.model_id, max_tokens=spec.max_output_tokens, max_retries=0,
                         default_request_timeout=180, **spec.extra)


def chat_model(agent: AgentName, model: "str | ModelSpec | BaseChatModel") -> tuple[BaseChatModel, str]:
    """(chat model, model id for traces)."""
    if isinstance(model, BaseChatModel):
        return model, (getattr(model, "model", None) or getattr(model, "model_name", None)
                       or type(model).__name__)
    spec = resolve_spec(model)
    llm = build_chat_model(agent, spec)
    if _FACTORY is not None:  # a fake or test model: trace its own name, so it is never priced as Claude
        return llm, getattr(llm, "model", None) or spec.model_id
    return llm, spec.model_id


# =====================================================================================
# One LLM call
# =====================================================================================

@dataclass(frozen=True)
class RetryPolicy:
    """Transient API errors (rate limit, overload, 5xx, timeouts, network) are retried with
    exponential backoff and jitter; anything else fails at once."""
    max_attempts: int = 4
    base_delay: float = 2.0
    max_delay: float = 30.0
    retry_status: frozenset = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
    retry_names: frozenset = frozenset({"RateLimitError", "InternalServerError", "APIConnectionError",
                                        "APITimeoutError", "OverloadedError", "ServiceUnavailableError",
                                        "TimeoutError", "ConnectionError", "ReadTimeout", "ConnectTimeout"})

    def retryable(self, exc: BaseException) -> bool:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status in self.retry_status
        return any(c.__name__ in self.retry_names for c in type(exc).__mro__)

    def delay(self, attempt: int, exc: Optional[BaseException] = None) -> float:
        after = _retry_after(exc)
        if after is not None:
            return min(after, self.max_delay)
        return min(self.max_delay, self.base_delay * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)


def _retry_after(exc: Optional[BaseException]) -> Optional[float]:
    try:
        value = exc.response.headers.get("retry-after")  # type: ignore[union-attr]
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


DEFAULT_RETRY = RetryPolicy()
_sleep: Callable[[float], None] = time.sleep  # tests replace this


def _is_anthropic(llm: BaseChatModel) -> bool:
    return str(getattr(llm, "_llm_type", "")).startswith("anthropic")


def _with_cached_system(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Put a cache breakpoint on the system prompt, so the parallel Intake calls (same prompt,
    different document) and the Planner's turns share it."""
    out = list(messages)
    if out and isinstance(out[0], SystemMessage):
        content = out[0].content
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
        if blocks and not any("cache_control" in b for b in blocks):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        out[0] = SystemMessage(content=blocks)
    return out


def usage_of(reply: AIMessage) -> dict[str, int]:
    """Token counts from LangChain's usage_metadata. For Anthropic, `input_tokens` is the whole
    prompt (cached parts included) and the details split out cache reads and writes."""
    usage = reply.usage_metadata or {}
    details = usage.get("input_token_details") or {}
    w5 = int(details.get("ephemeral_5m_input_tokens") or 0)
    w1 = int(details.get("ephemeral_1h_input_tokens") or 0)
    if not (w5 or w1):
        w5 = int(details.get("cache_creation") or 0)
    return {"input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cached_input_tokens": int(details.get("cache_read") or 0),
            "cache_write_5m_tokens": w5, "cache_write_1h_tokens": w1}


def stop_reason(reply: AIMessage) -> Optional[str]:
    meta = reply.response_metadata or {}
    return meta.get("stop_reason") or meta.get("finish_reason")


def invoke_llm(ctx: RunContext, agent: AgentName, llm: BaseChatModel, model_id: str,
               messages: Sequence[BaseMessage], *, tools: Optional[list[dict]] = None,
               item: Optional[str] = None, purpose: str = "", cache: bool = True,
               retry: RetryPolicy = DEFAULT_RETRY) -> AIMessage:
    """One LLM call with budget check, prompt caching, retries and an `llm_call` trace event."""
    anthropic = _is_anthropic(llm)
    if cache and anthropic:
        messages = _with_cached_system(messages)
        if tools:
            tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
    runnable = llm.bind_tools(tools) if tools else llm
    kwargs: dict[str, Any] = {"cache_control": {"type": "ephemeral"}} if cache and anthropic else {}

    attempt = 0
    while True:
        attempt += 1
        ctx.reserve_llm_call()
        t0 = time.monotonic()
        try:
            reply = runnable.invoke(list(messages), **kwargs)
            break
        except Exception as exc:  # noqa: BLE001 - classified below
            latency_ms = int((time.monotonic() - t0) * 1000)
            if not retry.retryable(exc) or attempt >= retry.max_attempts:
                ctx.emit("error", agent=agent, item=item, model=model_id, latency_ms=latency_ms,
                         summary=f"{agent} {purpose or 'call'} failed: {type(exc).__name__}: {str(exc)[:300]}",
                         payload={"attempt": attempt, "retryable": retry.retryable(exc)})
                raise
            wait = retry.delay(attempt, exc)
            with ctx._lock:
                ctx.retries += 1
            ctx.emit("error", agent=agent, item=item, model=model_id, latency_ms=latency_ms,
                     summary=f"{agent} {purpose or 'call'}: {type(exc).__name__}, retry {attempt}/"
                             f"{retry.max_attempts - 1} in {wait:.1f} s",
                     payload={"attempt": attempt, "retryable": True, "wait_s": round(wait, 2)})
            _sleep(wait)
    latency_ms = int((time.monotonic() - t0) * 1000)
    if not isinstance(reply, AIMessage):  # pragma: no cover - defensive
        reply = AIMessage(content=str(getattr(reply, "content", reply)))

    u = usage_of(reply)
    ctx.record_llm(u["input_tokens"], u["output_tokens"])
    n_tools = len(reply.tool_calls or [])
    reason = stop_reason(reply)
    payload = {k: v for k, v in {"purpose": purpose or None, "stop_reason": reason, "attempts": attempt,
                                 "cache_write_5m_tokens": u["cache_write_5m_tokens"] or None,
                                 "cache_write_1h_tokens": u["cache_write_1h_tokens"] or None,
                                 "tool_calls": [tc["name"] for tc in reply.tool_calls] or None}.items()
               if v is not None}
    ctx.emit("llm_call", agent=agent, item=item, model=model_id, input_tokens=u["input_tokens"],
             cached_input_tokens=u["cached_input_tokens"], output_tokens=u["output_tokens"],
             latency_ms=latency_ms, cost_usd=None,  # filled by core/tracing.py from PRICES
             summary=f"{agent} {purpose or 'call'}: {u['input_tokens']} in / {u['output_tokens']} out tokens"
                     + (f", {n_tools} tool call(s)" if n_tools else ""),
             payload=payload)
    if reason == "refusal":
        raise HarnessError(f"{agent} model refused the request ({purpose or 'call'})")
    return reply


# =====================================================================================
# Structured replies and the agent loop
# =====================================================================================

def _repair_text(error: Exception, reply: AIMessage, hint: str) -> str:
    text = str(error)
    text = text if len(text) <= 2000 else text[:2000] + " ..."
    cut = " Your reply was cut off at the output token limit, so keep it shorter." if stop_reason(reply) == "max_tokens" else ""
    return f"Your reply could not be used: {text}{cut}\n{hint}"


def call_json(ctx: RunContext, agent: AgentName, llm: BaseChatModel, model_id: str,
              messages: Sequence[BaseMessage], parse: Callable[[AIMessage], T], *,
              item: Optional[str] = None, purpose: str = "", repairs: int = 1,
              repair_hint: str = "Return the corrected JSON object only.") -> T:
    """A call whose reply `parse()` must accept (it raises ValueError/ValidationError if not).
    On failure the error goes back to the model, up to `repairs` times. Raises
    SchemaRepairFailed (with the last reply) when every attempt fails."""
    msgs = list(messages)
    for attempt in range(repairs + 1):
        reply = invoke_llm(ctx, agent, llm, model_id, msgs, item=item,
                           purpose=purpose if attempt == 0 else "schema repair")
        try:
            return parse(reply)
        except (ValueError, ValidationError) as exc:
            if attempt == repairs:
                raise SchemaRepairFailed(f"{agent} reply invalid after {repairs} repair(s): {str(exc)[:500]}",
                                         reply, exc) from exc
            msgs += [reply, HumanMessage(_repair_text(exc, reply, repair_hint))]
    raise AssertionError("unreachable")  # pragma: no cover


ToolExecutor = Callable[[dict], ToolMessage]


@dataclass
class LoopResult:
    value: Any
    messages: list[BaseMessage]
    turns: int
    repairs: int


def run_tool_loop(ctx: RunContext, agent: AgentName, llm: BaseChatModel, model_id: str,
                  messages: Sequence[BaseMessage], *, tools: list[dict], execute: ToolExecutor,
                  finish: Callable[[AIMessage], Any], max_turns: int = 8, max_repairs: int = 2,
                  purpose: str = "", repair_hint: str = "Fix it (call the tools again if needed) and reply "
                                                       "with the corrected final answer.") -> LoopResult:
    """The ReAct loop both systems use.

    Each turn is one invoke_llm call. If the reply has tool calls, `execute(tool_call)` runs
    each one (it must return a ToolMessage, with status="error" for a failed call rather than
    raising) and the loop goes on. A reply without tool calls is the final answer: `finish()`
    turns it into the result, or raises ValueError/ValidationError, which is sent back for
    repair (at most `max_repairs` times). Raises HarnessError when turns or repairs run out."""
    msgs = list(messages)
    repairs = 0
    for turn in range(1, max_turns + 1):
        reply = invoke_llm(ctx, agent, llm, model_id, msgs, tools=tools,
                           purpose=f"{purpose} turn {turn}".strip())
        msgs.append(reply)
        if reply.tool_calls:
            for tc in reply.tool_calls:
                msgs.append(execute(tc))
            continue
        try:
            return LoopResult(finish(reply), msgs, turn, repairs)
        except (ValueError, ValidationError) as exc:
            if repairs >= max_repairs:
                raise SchemaRepairFailed(f"{agent} gave no usable final answer after {repairs} repair(s): "
                                         f"{str(exc)[:500]}", reply, exc) from exc
            repairs += 1
            msgs.append(HumanMessage(_repair_text(exc, reply, repair_hint)))
    raise HarnessError(f"{agent} gave no final answer in {max_turns} turns")
