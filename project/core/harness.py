"""Agent harness shared by the multi-agent system and the single-agent baseline.

Every agent in both systems is built with :func:`create_harness_agent` and run
with :func:`run_agent`, so they get exactly the same:

* model settings (:data:`MODEL_SETTINGS`)
* retry policy: up to :data:`MAX_RETRIES` retries with exponential backoff,
  only on temporary API errors (rate limit, overload, 5xx, timeouts, network)
* run budget: one :class:`RunBudget` per run, shared by every agent in it
  (40 LLM calls, 1.5M tokens, 10 minutes; TASKS.md §0.1)
* schema repair: at most one repair retry when the final output does not
  validate, or the agent stops without submitting it
* prompt caching (Anthropic ``cache_control``)
* tracing: one ``llm_call`` event per model call and one ``tool_call`` event per
  tool call, with tokens, latency and cost (see ``tracing.py``)

Only the architecture differs between the two systems, never the harness.
"""

from __future__ import annotations

import time
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRetryMiddleware, hook_config
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from project.core.schemas import Budgets, RunError
from project.core.tracing import Tracer, usage_from_message

# ---------------------------------------------------------------------------
# Shared settings (identical for every agent in every system)
# ---------------------------------------------------------------------------

MODEL_SETTINGS: dict[str, Any] = {
    "max_tokens": 16_000,
    "default_request_timeout": 180.0,
    # The SDK's own retries are off; ModelRetryMiddleware is the single retry layer,
    # so every attempt is visible and counted the same way in both systems.
    "max_retries": 0,
}
MAX_RETRIES = 3
MAX_SCHEMA_REPAIRS = 1
TOOL_RESULT_SUMMARY_CHARS = 160


def make_chat_model(model_id: str) -> BaseChatModel:
    """Build a Claude chat model with the shared settings."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model_id, **MODEL_SETTINGS)


def model_name_of(model: BaseChatModel) -> str | None:
    return getattr(model, "model", None) or getattr(model, "model_name", None)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class BudgetExceeded(Exception):
    """Raised when a run hits its call, token or time limit. Maps to ``budget_exceeded``."""

    code = "budget_exceeded"


class RunBudget:
    """Call, token and wall-clock limits for one run, shared across all its agents.

    Thread-safe, because Intake agents run in parallel.
    """

    def __init__(self, budgets: Budgets | None = None, clock: Callable[[], float] = time.monotonic):
        self.budgets = budgets or Budgets()
        self._clock = clock
        self._lock = threading.Lock()
        self.started_at: float | None = None
        self.llm_calls = 0
        self.tokens = 0

    def start(self) -> None:
        with self._lock:
            if self.started_at is None:
                self.started_at = self._clock()

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self.started_at is None else self._clock() - self.started_at

    def check(self) -> None:
        """Raise :class:`BudgetExceeded` if another LLM call is not allowed."""
        self.start()
        b = self.budgets
        with self._lock:
            if self.llm_calls >= b.max_llm_calls:
                raise BudgetExceeded(f"LLM call limit reached ({self.llm_calls}/{b.max_llm_calls})")
            if self.tokens >= b.max_tokens:
                raise BudgetExceeded(f"Token limit reached ({self.tokens}/{b.max_tokens})")
        if self.elapsed_s >= b.max_seconds:
            raise BudgetExceeded(f"Time limit reached ({self.elapsed_s:.0f}s/{b.max_seconds}s)")

    def record_llm_call(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.llm_calls += 1
            self.tokens += input_tokens + output_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ai_message(response: Any) -> AIMessage | None:
    """The AIMessage inside a ModelResponse (or the message itself)."""
    if isinstance(response, AIMessage):
        return response
    for msg in reversed(getattr(response, "result", None) or []):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _content_text(content: Any) -> str:
    """Tool result content as text for the trace; images become a short placeholder."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, dict) and block.get("type") == "image":
            parts.append(f"[image {block.get('mime_type', '')}]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


def is_transient_error(exc: BaseException) -> bool:
    """True for errors worth retrying: rate limits, overload, 5xx, timeouts, network."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return False
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError)):
        return True  # APITimeoutError is a subclass of APIConnectionError
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in (408, 409, 429, 529) or exc.status_code >= 500
    return False


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class BudgetMiddleware(AgentMiddleware):
    """Checks the shared run budget before every model call and records usage after it."""

    def __init__(self, budget: RunBudget):
        super().__init__()
        self.budget = budget

    def wrap_model_call(self, request, handler):
        self.budget.check()
        try:
            response = handler(request)
        except StructuredOutputError as exc:
            self._record(exc.ai_message)
            raise
        self._record(_ai_message(response))
        return response

    def _record(self, message: AIMessage | None) -> None:
        u = usage_from_message(message)
        self.budget.record_llm_call(u["input_tokens"], u["output_tokens"])


class TracingMiddleware(AgentMiddleware):
    """Emits one ``llm_call`` event per model call and one ``tool_call`` event per tool call."""

    def __init__(self, tracer: Tracer, agent: str, model_name: str | None, item: str | None = None):
        super().__init__()
        self.tracer = tracer
        self.agent = agent
        self.model_name = model_name
        self.item = item

    def wrap_model_call(self, request, handler):
        t0 = time.perf_counter()
        try:
            response = handler(request)
        except StructuredOutputError as exc:
            # The call happened and cost tokens even though its output was rejected.
            self._log(exc.ai_message, t0)
            raise
        self._log(_ai_message(response), t0)
        return response

    def _log(self, message: AIMessage | None, t0: float) -> None:
        if message is None:
            return
        self.tracer.llm_call(
            agent=self.agent, model=self.model_name, message=message,
            latency_ms=int((time.perf_counter() - t0) * 1000), item=self.item,
        )

    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        args = request.tool_call.get("args", {})
        t0 = time.perf_counter()
        result = handler(request)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        content = result.content if isinstance(result, ToolMessage) else str(result)
        text = _content_text(content)
        is_error = isinstance(result, ToolMessage) and result.status == "error"
        short = text.replace("\n", " ")[:TOOL_RESULT_SUMMARY_CHARS]
        self.tracer.emit(
            "tool_call", agent=self.agent, item=self.item, tool=name, latency_ms=latency_ms, cost_usd=0.0,
            summary=f"{name}{' (error)' if is_error else ''}: {short}",
            payload={"args": args, "result": text, "is_error": is_error},
        )
        return result


class SchemaRepair:
    """Allows at most ``max_repairs`` repair retries for the final structured output.

    Used two ways, sharing one counter:
    * as ``ToolStrategy(handle_errors=...)`` when the output fails validation;
    * by :class:`SubmitNudgeMiddleware` when the agent stops without submitting.
    After the allowance is used, the next failure raises and the run fails with
    ``schema_invalid``.
    """

    def __init__(self, max_repairs: int = MAX_SCHEMA_REPAIRS):
        self.max_repairs = max_repairs
        self.used = 0

    def reset(self) -> None:
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.max_repairs:
            return False
        self.used += 1
        return True

    def __call__(self, exc: Exception) -> str:
        if not self.take():
            raise exc
        return (
            "Your output did not match the required schema. Errors:\n"
            f"{exc}\n"
            "Fix every error listed and submit the complete, corrected output again."
        )


class NoStructuredOutput(Exception):
    """The agent finished without submitting its structured output."""


class SubmitNudgeMiddleware(AgentMiddleware):
    """If the agent stops without submitting its output, ask once (using the repair allowance)."""

    def __init__(self, repair: SchemaRepair, output_tool_name: str):
        super().__init__()
        self.repair = repair
        self.output_tool_name = output_tool_name

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        if state.get("structured_response") is not None:
            return None
        last = state["messages"][-1] if state.get("messages") else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if not self.repair.take():
            raise NoStructuredOutput(f"Agent stopped without calling {self.output_tool_name}")
        return {
            "messages": [HumanMessage(
                f"You stopped without submitting your result. Call the {self.output_tool_name} "
                "tool now with the complete output."
            )],
            "jump_to": "model",
        }


# ---------------------------------------------------------------------------
# Factory and runner
# ---------------------------------------------------------------------------


@dataclass
class AgentHandle:
    """A built agent plus the per-run objects its middleware uses."""

    agent: Any  # CompiledStateGraph
    name: str
    model_name: str | None
    tracer: Tracer
    budget: RunBudget
    repair: SchemaRepair
    output_schema: type | None
    item: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def create_harness_agent(
    *,
    name: str,
    model: str | BaseChatModel,
    tools: Sequence[Any],
    system_prompt: str,
    tracer: Tracer,
    budget: RunBudget,
    output_schema: type | None = None,
    item: str | None = None,
    checkpointer: Any = None,
    extra_middleware: Sequence[AgentMiddleware] = (),
) -> AgentHandle:
    """Create an agent with the shared middleware stack.

    Args:
        name: Trace agent name: ``intake``, ``planner``, ``checker`` or ``agent`` (baseline).
        model: A model id such as ``"claude-sonnet-5-5"``, or a chat model (tests).
        tools: The agent's tools. Use the shared tools in ``project/tools`` unchanged.
        system_prompt: The agent's prompt.
        tracer, budget: Shared by every agent in the run.
        output_schema: Pydantic model of the final output (submitted as a tool call).
        item: Document name, for Intake agents.
        checkpointer: LangGraph checkpointer, needed for reject-and-resume.
        extra_middleware: Appended innermost. Keep it empty in both systems for fairness.
    """
    chat_model = make_chat_model(model) if isinstance(model, str) else model
    model_name = model if isinstance(model, str) else model_name_of(chat_model)
    repair = SchemaRepair()

    middleware: list[AgentMiddleware] = [
        BudgetMiddleware(budget),  # outermost: checked before every call, never retried
        TracingMiddleware(tracer, name, model_name, item=item),
    ]
    response_format = None
    if output_schema is not None:
        response_format = ToolStrategy(output_schema, handle_errors=repair)
        middleware.append(SubmitNudgeMiddleware(repair, output_schema.__name__))
    middleware.append(
        ModelRetryMiddleware(
            max_retries=MAX_RETRIES, retry_on=is_transient_error, on_failure="error",
            backoff_factor=2.0, initial_delay=1.0, max_delay=30.0,
        )
    )
    try:
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

        middleware.append(AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"))
    except ImportError:  # pragma: no cover
        pass
    middleware.extend(extra_middleware)

    agent = create_agent(
        chat_model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=response_format,
        checkpointer=checkpointer,
        name=name,
    )
    return AgentHandle(
        agent=agent, name=name, model_name=model_name, tracer=tracer, budget=budget,
        repair=repair, output_schema=output_schema, item=item,
    )


@dataclass
class AgentRunResult:
    output: Any  # validated output_schema instance, or None
    messages: list[Any]
    error: RunError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def error_from_exception(exc: BaseException) -> RunError:
    """Map an exception to the error codes in API.md §4.4."""
    if isinstance(exc, BudgetExceeded):
        return RunError(code="budget_exceeded", message=str(exc))
    if isinstance(exc, (StructuredOutputError, NoStructuredOutput)):
        return RunError(code="schema_invalid", message=str(exc))
    try:
        import anthropic

        if isinstance(exc, anthropic.APIError):
            return RunError(code="model_error", message=f"{type(exc).__name__}: {exc}")
    except ImportError:  # pragma: no cover
        pass
    return RunError(code="internal", message=f"{type(exc).__name__}: {exc}")


def run_agent(
    handle: AgentHandle,
    messages: list[Any] | str,
    *,
    thread_id: str | None = None,
) -> AgentRunResult:
    """Invoke an agent once, with ``agent_start`` / ``agent_end`` events.

    Never raises for agent failures: the error comes back in the result, already
    mapped to a :class:`RunError` code, and an ``error`` event is traced.
    Each call gets a fresh schema-repair allowance (a resume after a human
    rejection is a new attempt).
    """
    if isinstance(messages, str):
        messages = [HumanMessage(messages)]
    handle.repair.reset()
    handle.budget.start()
    tracer = handle.tracer
    tracer.emit("agent_start", agent=handle.name, item=handle.item, model=handle.model_name,
                summary=f"{handle.name} started")
    config: dict[str, Any] = {"recursion_limit": 200}
    if thread_id is not None:
        config["configurable"] = {"thread_id": thread_id}
    t0 = time.perf_counter()
    try:
        state = handle.agent.invoke({"messages": messages}, config=config)
    except Exception as exc:  # noqa: BLE001 - every failure must end the run cleanly
        err = error_from_exception(exc)
        tracer.emit("error", agent=handle.name, item=handle.item, summary=f"{err.code}: {err.message}",
                    payload={"code": err.code, "message": err.message})
        # Duration goes in the payload, not latency_ms: per-call latencies are summed from
        # llm_call / tool_call events, and an agent-level total there would be counted twice.
        tracer.emit("agent_end", agent=handle.name, item=handle.item,
                    summary=f"{handle.name} failed ({err.code})",
                    payload={"status": "failed", "schema_repairs": handle.repair.used,
                             "duration_ms": int((time.perf_counter() - t0) * 1000)})
        return AgentRunResult(output=None, messages=list(messages), error=err)

    output = state.get("structured_response")
    tracer.emit("agent_end", agent=handle.name, item=handle.item,
                summary=f"{handle.name} done" + (f", {handle.repair.used} repair retry" if handle.repair.used else ""),
                payload={"status": "done", "schema_repairs": handle.repair.used,
                         "duration_ms": int((time.perf_counter() - t0) * 1000)})
    return AgentRunResult(output=output, messages=state.get("messages", []))
