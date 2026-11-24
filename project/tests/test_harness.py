"""Prompt caching (core/harness.py) at the call_llm seam. No network: the real
ChatAnthropic request payload is built offline, or its network call is replaced."""
from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from core import harness
from core.schemas import RunConfig
from multi_agent.context import RunContext
from multi_agent.fakes import FakeChatModel
from multi_agent.llm import call_llm

EPHEMERAL = {"type": "ephemeral"}


@pytest.fixture
def claude():
    return ChatAnthropic(model="claude-sonnet-5-5", api_key="test-not-used", max_tokens=1000)


def conversation():
    return [SystemMessage("You are the planner." * 200), HumanMessage("Plan bundle X.")]


def test_payload_has_both_breakpoints(claude):
    msgs, kwargs = harness.prepare(claude, conversation())
    assert kwargs == {"cache_control": EPHEMERAL}
    payload = claude._get_request_payload(msgs, **kwargs)
    assert payload["cache_control"] == EPHEMERAL  # automatic caching for the growing conversation
    assert payload["system"][-1]["cache_control"] == EPHEMERAL  # tools + system prompt
    assert payload["system"][-1]["text"].startswith("You are the planner.")


def test_callers_history_is_never_modified(claude):
    history = conversation()
    before = [m.model_copy(deep=True) for m in history]
    harness.prepare(claude, history)
    harness.prepare(claude, history)
    assert history == before and isinstance(history[0].content, str)


def test_system_with_blocks_gets_one_breakpoint_on_last_text(claude):
    system = SystemMessage(content=[{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}])
    msgs, _ = harness.prepare(claude, [system, HumanMessage("go")])
    assert "cache_control" not in msgs[0].content[0]
    assert msgs[0].content[1]["cache_control"] == EPHEMERAL


def test_fakes_and_switch_off_get_nothing(claude, monkeypatch):
    fake = FakeChatModel(responder=lambda messages: AIMessage("{}"))
    msgs, kwargs = harness.prepare(fake, conversation())
    assert kwargs == {} and msgs == conversation()

    monkeypatch.setenv("WOP_PROMPT_CACHE", "0")
    msgs, kwargs = harness.prepare(claude, conversation())
    assert kwargs == {} and isinstance(msgs[0].content, str)


def test_call_llm_sends_caching_and_traces_cache_reads(claude, monkeypatch):
    seen = {}

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        seen["kwargs"], seen["messages"] = kwargs, messages
        reply = AIMessage("ok", usage_metadata={"input_tokens": 12000, "output_tokens": 50, "total_tokens": 12050,
                                               "input_token_details": {"cache_read": 10000}})
        return ChatResult(generations=[ChatGeneration(message=reply)])

    monkeypatch.setattr(ChatAnthropic, "_generate", fake_generate)
    events = []
    ctx = RunContext(run_id="r1", config=RunConfig(mode="baseline"), emit_fn=events.append)
    tools = [{"name": "mrp_tool", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    call_llm(ctx, "agent", claude, "claude-sonnet-5-5", conversation(), tools=tools)

    assert seen["kwargs"]["cache_control"] == EPHEMERAL
    assert seen["messages"][0].content[-1]["cache_control"] == EPHEMERAL
    (ev,) = [e for e in events if e.type == "llm_call"]
    assert ev.cached_input_tokens == 10000
    # 2000 uncached * $2 + 10000 cached * $0.10 + 50 out * $10, per million
    assert ev.cost_usd == pytest.approx((2000 * 2 + 10000 * 0.10 + 50 * 10) / 1e6)
