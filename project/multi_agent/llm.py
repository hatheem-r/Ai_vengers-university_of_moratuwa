"""Chat models for the agent nodes. Everything that touches the model goes through
core/harness.py (shared with the baseline): budgets, retries, prompt caching, schema repair,
the agent loop and `llm_call` trace events. This module keeps the old names the graph uses
and the helpers for reading JSON out of a reply.

Tests and mock mode swap the model with `set_llm_factory()` (see multi_agent/fakes.py),
so nothing here needs an API key until a real model is called.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from core.harness import (LlmFactory, build_chat_model, chat_model, invoke_llm,  # noqa: F401
                          resolve_spec, set_llm_factory, spec_for)
from core.models import ModelSpec
from core.schemas import AgentName

from .context import RunContext

ModelLike = Union[str, ModelSpec, BaseChatModel]


def call_llm(ctx: RunContext, agent: AgentName, llm: BaseChatModel, model_id: str,
             messages: Sequence[BaseMessage], *, tools: Optional[list[dict]] = None,
             item: Optional[str] = None, purpose: str = "") -> AIMessage:
    """One LLM call through the harness (kept for older call sites)."""
    return invoke_llm(ctx, agent, llm, model_id, messages, tools=tools, item=item, purpose=purpose)


# --- reading JSON out of a model reply -------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def parse_json_reply(msg: BaseMessage) -> Any:
    """The JSON object in a reply: fenced ```json``` block, else the outermost {...}."""
    text = message_text(msg).strip()
    for m in _FENCE.finditer(text):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contains no JSON object")
    return json.loads(text[start:end + 1])
