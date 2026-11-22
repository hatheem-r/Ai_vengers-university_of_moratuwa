"""Harness tests with a dummy tool and a scripted model (no API calls)."""

import anthropic
import httpx2
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from project.core.harness import RunBudget, create_harness_agent, run_agent
from project.core.schemas import Budgets
from tests.conftest import ScriptedChatModel, ai


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


class DummyOutput(BaseModel):
    total: int
    note: str


def build(script, tracer, budget=None, schema=DummyOutput):
    model = ScriptedChatModel(script=script, seen=[])
    handle = create_harness_agent(
        name="agent", model=model, tools=[add_numbers], system_prompt="Add numbers.",
        tracer=tracer, budget=budget or RunBudget(), output_schema=schema,
    )
    return handle, model


SUBMIT_OK = ("DummyOutput", {"total": 5, "note": "done"})
SUBMIT_BAD = ("DummyOutput", {"total": "five"})  # wrong type and missing field


def test_happy_path_runs_tool_and_returns_validated_output(events, tracer):
    handle, model = build([ai(tool_calls=[("add_numbers", {"a": 2, "b": 3})]), ai(tool_calls=[SUBMIT_OK])], tracer)
    result = run_agent(handle, "Add 2 and 3")
    assert result.ok, result.error
    assert result.output == DummyOutput(total=5, note="done")
    assert [e.type for e in events] == ["agent_start", "llm_call", "tool_call", "llm_call", "agent_end"]
    tool_ev = events[2]
    assert tool_ev.tool == "add_numbers" and tool_ev.payload["result"] == "5"
    llm = [e for e in events if e.type == "llm_call"]
    assert all(e.model == "claude-sonnet-5-5" and e.cost_usd > 0 and e.latency_ms is not None for e in llm)
    assert [e.seq for e in events] == list(range(1, 6))
    assert handle.budget.llm_calls == 2 and handle.budget.tokens == 2 * 1100


def test_one_schema_repair_is_allowed(events, tracer):
    handle, model = build([ai(tool_calls=[SUBMIT_BAD]), ai(tool_calls=[SUBMIT_OK])], tracer)
    result = run_agent(handle, "go")
    assert result.ok and result.output.total == 5
    assert handle.repair.used == 1
    assert "did not match the required schema" in str(model.seen[1][-1].content)
    assert events[-1].payload["schema_repairs"] == 1


def test_second_schema_failure_fails_run_as_schema_invalid(events, tracer):
    handle, _ = build([ai(tool_calls=[SUBMIT_BAD]), ai(tool_calls=[SUBMIT_BAD])], tracer)
    result = run_agent(handle, "go")
    assert result.error.code == "schema_invalid"
    # Both calls cost tokens and both are traced and counted.
    assert len([e for e in events if e.type == "llm_call"]) == 2
    assert handle.budget.llm_calls == 2
    assert [e.type for e in events][-2:] == ["error", "agent_end"]


def test_stopping_without_submitting_gets_one_nudge(events, tracer):
    handle, model = build([ai("I think the answer is 5."), ai(tool_calls=[SUBMIT_OK])], tracer)
    result = run_agent(handle, "go")
    assert result.ok and result.output.note == "done"
    assert isinstance(model.seen[1][-1], HumanMessage) and "DummyOutput" in model.seen[1][-1].content


def test_nudge_and_repair_share_one_allowance(events, tracer):
    handle, _ = build([ai("no tool"), ai(tool_calls=[SUBMIT_BAD])], tracer)
    assert run_agent(handle, "go").error.code == "schema_invalid"


def test_call_budget_ends_run_cleanly(events, tracer):
    budget = RunBudget(Budgets(max_llm_calls=1))
    handle, model = build([ai(tool_calls=[("add_numbers", {"a": 1, "b": 1})]), ai(tool_calls=[SUBMIT_OK])], tracer, budget)
    result = run_agent(handle, "go")
    assert result.error.code == "budget_exceeded"
    assert model.calls == 1  # the second call was never sent


def test_token_budget(events, tracer):
    budget = RunBudget(Budgets(max_tokens=1500))
    handle, _ = build([ai(tool_calls=[("add_numbers", {"a": 1, "b": 1})], input_tokens=1400), ai(tool_calls=[SUBMIT_OK])], tracer, budget)
    assert run_agent(handle, "go").error.code == "budget_exceeded"


def test_time_budget(events, tracer):
    clock = {"now": 0.0}

    @tool
    def slow_step() -> str:
        """A step that takes 11 minutes."""
        clock["now"] += 660.0
        return "ok"

    budget = RunBudget(Budgets(max_seconds=600), clock=lambda: clock["now"])
    model = ScriptedChatModel(script=[ai(tool_calls=[("slow_step", {})]), ai(tool_calls=[SUBMIT_OK])], seen=[])
    handle = create_harness_agent(name="agent", model=model, tools=[slow_step], system_prompt="x",
                                  tracer=tracer, budget=budget, output_schema=DummyOutput)
    result = run_agent(handle, "go")
    assert result.error.code == "budget_exceeded" and "Time limit" in result.error.message
    assert model.calls == 1


def test_budget_is_shared_between_agents(events, tracer):
    budget = RunBudget(Budgets(max_llm_calls=2))
    a, _ = build([ai(tool_calls=[SUBMIT_OK])], tracer, budget)
    b, _ = build([ai(tool_calls=[("add_numbers", {"a": 1, "b": 1})]), ai(tool_calls=[SUBMIT_OK])], tracer, budget)
    assert run_agent(a, "go").ok
    assert run_agent(b, "go").error.code == "budget_exceeded"


def test_transient_api_error_is_retried(events, tracer):
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    handle, model = build([err, ai(tool_calls=[SUBMIT_OK])], tracer)
    assert run_agent(handle, "go").ok
    assert model.calls == 2


def test_non_transient_error_is_not_retried(events, tracer):
    handle, model = build([ValueError("bug"), ai(tool_calls=[SUBMIT_OK])], tracer)
    result = run_agent(handle, "go")
    assert result.error.code == "internal" and model.calls == 1


def test_retries_exhausted_is_model_error(events, tracer):
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    handle, model = build([err] * 4, tracer)
    result = run_agent(handle, "go")
    assert result.error.code == "model_error" and model.calls == 4  # 1 try + 3 retries
