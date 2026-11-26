"""core/harness.py: budgets, retries, prompt caching, schema repair, the tool loop. No API key:
every test uses a scripted chat model."""
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict

from core import harness, runner
from core.harness import (HarnessError, RetryPolicy, RunContext, SchemaRepairFailed, call_json, invoke_llm,
                          run_tool_loop, usage_of)
from core.schemas import Budget, RunConfig, TraceEvent
from core.tracing import MemoryTrace
from multi_agent.llm import parse_json_reply


class Scripted(BaseChatModel):
    """Replies (or exceptions) in order; records every call's messages and kwargs."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: list = []
    calls: list = []
    llm_kind: str = "scripted"
    model: str = "scripted-model"
    usage: dict = {}

    @property
    def _llm_type(self) -> str:
        return self.llm_kind

    def bind_tools(self, tools, **kwargs):
        self.calls.append(("bind_tools", tools))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append((list(messages), kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        reply = item if isinstance(item, AIMessage) else AIMessage(content=item)
        reply.usage_metadata = self.usage or {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
        return ChatResult(generations=[ChatGeneration(message=reply)])


def ctx_for(budget: Budget = Budget()) -> tuple[RunContext, MemoryTrace]:
    trace = MemoryTrace()
    return RunContext(run_id="run_h", config=RunConfig(mode="multi_agent", budget=budget), emit_fn=trace), trace


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(harness, "_sleep", slept.append)
    return slept


class StatusError(Exception):
    def __init__(self, status: int, retry_after: str | None = None):
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = type("R", (), {"headers": {"retry-after": retry_after} if retry_after else {}})()


MSGS = [SystemMessage("system prompt"), HumanMessage("hello")]


# --- invoke_llm -------------------------------------------------------------------------

def test_invoke_records_tokens_and_one_llm_call_event():
    ctx, trace = ctx_for()
    llm = Scripted(script=["hi"], calls=[])
    reply = invoke_llm(ctx, "planner", llm, "claude-sonnet-5-5", MSGS, purpose="test")
    assert reply.content == "hi"
    assert (ctx.llm_calls, ctx.input_tokens, ctx.output_tokens) == (1, 100, 10)
    [ev] = trace.events
    assert ev.type == "llm_call" and ev.agent == "planner" and ev.model == "claude-sonnet-5-5"
    assert ev.input_tokens == 100 and ev.output_tokens == 10 and ev.seq == 1
    assert ev.cost_usd == pytest.approx((100 * 2 + 10 * 10) / 1e6)   # filled by tracing.prepare_event
    assert ev.payload["purpose"] == "test" and ev.payload["attempts"] == 1


def test_retries_transient_errors_then_succeeds(no_sleep):
    ctx, trace = ctx_for()
    llm = Scripted(script=[StatusError(529), StatusError(429, retry_after="3"), "ok"], calls=[])
    assert invoke_llm(ctx, "intake", llm, "m", MSGS).content == "ok"
    assert ctx.llm_calls == 1 and ctx.retries == 2      # failed attempts are not billed calls
    assert no_sleep[1] == 3.0                            # retry-after header wins
    assert [e.type for e in trace.events] == ["error", "error", "llm_call"]
    assert "retry 1/3" in trace.events[0].summary


def test_non_retryable_error_raises_at_once():
    ctx, trace = ctx_for()
    llm = Scripted(script=[StatusError(400)], calls=[])
    with pytest.raises(StatusError):
        invoke_llm(ctx, "intake", llm, "m", MSGS)
    assert [e.type for e in trace.events] == ["error"] and trace.events[0].payload["retryable"] is False


def test_retries_give_up_after_max_attempts():
    ctx, _ = ctx_for()
    llm = Scripted(script=[StatusError(503)] * 3, calls=[])
    with pytest.raises(StatusError):
        invoke_llm(ctx, "intake", llm, "m", MSGS, retry=RetryPolicy(max_attempts=3))
    assert ctx.retries == 2


def test_retry_policy_classifies_by_name_and_status():
    p = RetryPolicy()
    RateLimitError = type("RateLimitError", (Exception,), {})
    assert p.retryable(RateLimitError()) and p.retryable(TimeoutError()) and p.retryable(StatusError(500))
    assert not p.retryable(ValueError()) and not p.retryable(StatusError(404))
    assert all(0 < p.delay(n) <= p.max_delay for n in range(1, 10))


def test_call_budget_raises_budget_exceeded_before_the_call():
    ctx, _ = ctx_for(Budget(max_llm_calls=2))
    llm = Scripted(script=["a", "b", "c"], calls=[])
    invoke_llm(ctx, "intake", llm, "m", MSGS)
    invoke_llm(ctx, "intake", llm, "m", MSGS)
    with pytest.raises(runner.BudgetExceeded, match="LLM call budget"):
        invoke_llm(ctx, "intake", llm, "m", MSGS)
    assert len(llm.script) == 1                          # third call never reached the model


def test_token_and_time_budgets():
    ctx, _ = ctx_for(Budget(max_tokens=150))
    llm = Scripted(script=["a", "b"], calls=[], usage={"input_tokens": 140, "output_tokens": 20, "total_tokens": 160})
    invoke_llm(ctx, "intake", llm, "m", MSGS)
    with pytest.raises(runner.BudgetExceeded, match="token budget"):
        invoke_llm(ctx, "intake", llm, "m", MSGS)
    ctx2, _ = ctx_for(Budget(max_seconds=5))
    ctx2.active_seconds = 6
    with pytest.raises(runner.BudgetExceeded, match="time budget"):
        ctx2.check_budget()


def test_refusal_is_a_harness_error():
    ctx, _ = ctx_for()
    refused = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
    with pytest.raises(HarnessError, match="refused"):
        invoke_llm(ctx, "planner", Scripted(script=[refused], calls=[]), "m", MSGS)


def test_prompt_caching_only_for_anthropic_models():
    ctx, _ = ctx_for()
    tools = [{"name": "a", "description": "", "input_schema": {}}, {"name": "b", "description": "", "input_schema": {}}]
    plain = Scripted(script=["x"], calls=[])
    invoke_llm(ctx, "planner", plain, "m", MSGS, tools=tools)
    msgs, kwargs = plain.calls[-1]
    assert kwargs == {} and msgs[0].content == "system prompt"

    claude = Scripted(script=["x"], calls=[], llm_kind="anthropic-chat")
    invoke_llm(ctx, "planner", claude, "m", MSGS, tools=tools)
    (_, bound), (msgs, kwargs) = claude.calls
    assert kwargs == {"cache_control": {"type": "ephemeral"}}                    # rolling prefix cache
    assert msgs[0].content[-1]["cache_control"] == {"type": "ephemeral"}       # system prompt
    assert "cache_control" in bound[-1] and "cache_control" not in bound[0]    # last tool definition
    assert "cache_control" not in tools[-1]                                     # caller's list untouched


def test_usage_reads_cache_reads_and_writes():
    msg = AIMessage(content="", usage_metadata={
        "input_tokens": 1000, "output_tokens": 5, "total_tokens": 1005,
        "input_token_details": {"cache_read": 600, "cache_creation": 0, "ephemeral_5m_input_tokens": 300}})
    assert usage_of(msg) == {"input_tokens": 1000, "output_tokens": 5, "cached_input_tokens": 600,
                             "cache_write_5m_tokens": 300, "cache_write_1h_tokens": 0}
    ctx, trace = ctx_for()
    llm = Scripted(script=["x"], calls=[], usage=msg.usage_metadata)
    invoke_llm(ctx, "planner", llm, "claude-sonnet-5-5", MSGS)
    ev = trace.events[0]
    assert ev.cached_input_tokens == 600 and ev.payload["cache_write_5m_tokens"] == 300
    assert ev.cost_usd == pytest.approx((100 * 2 + 600 * 0.10 + 300 * 2.5 + 5 * 10) / 1e6)


# --- call_json --------------------------------------------------------------------------

class Verdict(BaseModel):
    verdict: str


def parse_verdict(reply: AIMessage) -> Verdict:
    return Verdict.model_validate(parse_json_reply(reply))


def test_call_json_repairs_once():
    ctx, trace = ctx_for()
    llm = Scripted(script=["not json", '{"verdict": "pass"}'], calls=[])
    assert call_json(ctx, "checker", llm, "m", MSGS, parse_verdict, purpose="review").verdict == "pass"
    repair_msgs, _ = llm.calls[-1]
    assert "could not be used" in repair_msgs[-1].content
    assert [e.payload["purpose"] for e in trace.events] == ["review", "schema repair"]


def test_call_json_gives_up_with_the_last_reply():
    ctx, _ = ctx_for()
    llm = Scripted(script=["nope", '{"other": 1}'], calls=[])
    with pytest.raises(SchemaRepairFailed) as info:
        call_json(ctx, "checker", llm, "m", MSGS, parse_verdict)
    assert info.value.reply.content == '{"other": 1}'


# --- run_tool_loop ----------------------------------------------------------------------

def tool_call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"id_{name}"}])


def echo(tc: dict) -> ToolMessage:
    return ToolMessage(f"{tc['name']} done", tool_call_id=tc["id"])


def test_tool_loop_runs_tools_then_finishes():
    ctx, _ = ctx_for()
    llm = Scripted(script=[tool_call("mrp_tool", x=1), tool_call("schedule_tool"), '{"verdict": "pass"}'], calls=[])
    seen: list[str] = []
    result = run_tool_loop(ctx, "planner", llm, "m", MSGS, tools=[], execute=lambda tc: (seen.append(tc["name"]), echo(tc))[1],
                           finish=parse_verdict)
    assert result.value.verdict == "pass" and result.turns == 3 and result.repairs == 0
    assert seen == ["mrp_tool", "schedule_tool"]
    assert isinstance(result.messages[3], ToolMessage)


def test_tool_loop_sends_bad_final_answer_back_for_repair():
    ctx, _ = ctx_for()
    llm = Scripted(script=["{}", tool_call("mrp_tool"), '{"verdict": "pass"}'], calls=[])
    result = run_tool_loop(ctx, "planner", llm, "m", MSGS, tools=[], execute=echo, finish=parse_verdict)
    assert result.repairs == 1 and result.value.verdict == "pass"


def test_tool_loop_limits():
    ctx, _ = ctx_for()
    llm = Scripted(script=["{}", "{}", "{}"], calls=[])
    with pytest.raises(SchemaRepairFailed):
        run_tool_loop(ctx, "planner", llm, "m", MSGS, tools=[], execute=echo, finish=parse_verdict, max_repairs=2)
    ctx, _ = ctx_for()
    llm = Scripted(script=[tool_call("t")] * 3, calls=[])
    with pytest.raises(HarnessError, match="no final answer in 3 turns"):
        run_tool_loop(ctx, "planner", llm, "m", MSGS, tools=[], execute=echo, finish=parse_verdict, max_turns=3)


def test_runner_maps_harness_errors_to_error_codes():
    assert runner._error_code(SchemaRepairFailed("x", AIMessage(content=""), ValueError())) == "schema_invalid"
    assert runner._error_code(HarnessError("refused")) == "model_error"
    assert runner._error_code(KeyError("bug")) == "internal"


def test_contexts_are_per_run_and_resume_keeps_counters():
    events: list[TraceEvent] = []
    cfg = RunConfig(mode="baseline")
    ctx = harness.open_ctx("run_ctx", cfg, events.append)
    ctx.record_llm(10, 5)
    again = harness.open_ctx("run_ctx", cfg, lambda e: None)
    assert again is ctx and again.llm_calls == 1
    harness.close_ctx("run_ctx")
    with pytest.raises(RuntimeError):
        harness.get_ctx("run_ctx")
