"""Prompt caching for every LLM call (Shazan).

Plugged in at the one seam every LLM call goes through, ``multi_agent/llm.py``
``call_llm()``, so the multi-agent system and the single-agent baseline get
exactly the same caching (fairness, TASKS.md §2).

Two cache breakpoints, both Anthropic ``ephemeral`` (5-minute TTL):

1. **On the system message.** The request prefix is tools -> system ->
   messages, so this caches the tool definitions and the system prompt. Calls
   that share them but differ after them reuse it: every Intake call (one per
   document) and every schema-repair call.
2. **Top-level ``cache_control`` (automatic caching).** The API puts the
   breakpoint on the last cacheable block and moves it forward as the
   conversation grows, so each turn of the baseline's single conversation and
   of the Planner's tool loop reads the previous turns from cache.

Cache reads cost 0.05x-0.1x the input price; writes 1.25x. Prefixes shorter
than the model's minimum (512 tokens on the current Claude models) are simply
not cached. ``core/tracing.py`` already prices cache reads and writes, and
``llm_call`` events carry ``cached_input_tokens``, so the effect shows in traces.

Only real Anthropic chat models get the parameters; test fakes and other
providers get nothing, so their behaviour is unchanged. Set ``WOP_PROMPT_CACHE=0``
to switch caching off (for example to measure what it saves).
"""
from __future__ import annotations

import os
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage

CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}


def caching_enabled() -> bool:
    return os.getenv("WOP_PROMPT_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def supports_caching(llm: BaseChatModel) -> bool:
    """True for a direct-API Anthropic chat model (the only kind that accepts these parameters)."""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:  # pragma: no cover - langchain-anthropic is a project dependency
        return False
    return isinstance(llm, ChatAnthropic) and getattr(llm, "_llm_type", None) == "anthropic-chat"


def _cached_system(msg: SystemMessage) -> SystemMessage:
    """The same system message with a cache breakpoint on its last text block."""
    content = msg.content
    if isinstance(content, str):
        blocks: list[Any] = [{"type": "text", "text": content}]
    else:
        blocks = [dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
    for block in reversed(blocks):
        if block.get("type") == "text" and block.get("text"):
            block["cache_control"] = dict(CACHE_CONTROL)
            break
    else:
        return msg
    return SystemMessage(content=blocks, id=msg.id, name=msg.name)


def prepare(llm: BaseChatModel, messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], dict[str, Any]]:
    """Messages and ``invoke`` kwargs for one call.

    Never changes the caller's message list: agents keep their own history and
    must not accumulate breakpoints (the API allows at most four per request).
    """
    msgs = list(messages)
    if not (caching_enabled() and supports_caching(llm)):
        return msgs, {}
    if msgs and isinstance(msgs[0], SystemMessage):
        msgs[0] = _cached_system(msgs[0])
    return msgs, {"cache_control": dict(CACHE_CONTROL)}
