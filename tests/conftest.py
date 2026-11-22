"""Shared test helpers.

``ScriptedChatModel`` replays a fixed list of AIMessages (or raises exceptions),
so agents built by the real harness can be tested end to end without an API key.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from project.core.tracing import Tracer


class ScriptedChatModel(BaseChatModel):
    script: list[Any]
    model: str = "claude-sonnet-5-5"
    calls: int = 0
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # tools are only described to a real model
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        if self.calls >= len(self.script):
            raise AssertionError(f"Model called {self.calls + 1} times; script has {len(self.script)} steps")
        step = self.script[self.calls]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return ChatResult(generations=[ChatGeneration(message=step)])


def ai(content: str = "", tool_calls: list[tuple[str, dict]] | None = None,
       input_tokens: int = 1000, output_tokens: int = 100, cache_read: int = 0) -> AIMessage:
    """An AIMessage with usage metadata and optional tool calls ``[(name, args), ...]``."""
    calls = [{"name": n, "args": a, "id": f"call_{i}_{n}", "type": "tool_call"} for i, (n, a) in enumerate(tool_calls or [])]
    return AIMessage(
        content=content,
        tool_calls=calls,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read},
        },
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def tracer(events, tmp_path):
    return Tracer("run_test", emit=events.append, run_dir=tmp_path)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Keep retry backoff from slowing the tests."""
    import langchain.agents.middleware.model_retry as mr

    monkeypatch.setattr(mr.time, "sleep", lambda _s: None)
