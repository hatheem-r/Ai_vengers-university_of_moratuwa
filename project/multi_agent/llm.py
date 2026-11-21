"""Chat models for the agent nodes, and the one place every LLM call goes through.

`call_llm()` is the seam where Shazan's core/harness.py plugs in later (retry, caps,
prompt caching, schema repair). Today it does the budget check and the `llm_call`
trace event itself, so traces already have the API.md §6 shape.

Tests and mock mode swap the model with `set_llm_factory()` (see multi_agent/fakes.py),
so nothing here needs an API key until a real model is called.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional, Sequence, Union

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from core import models
from core.models import ModelSpec
from core.schemas import AgentName, RunConfig

from .context import RunContext

ModelLike = Union[str, ModelSpec, BaseChatModel]
LlmFactory = Callable[[AgentName, ModelSpec], BaseChatModel]

_FACTORY: Optional[LlmFactory] = None


def set_llm_factory(factory: Optional[LlmFactory]) -> None:
    """Replace how chat models are built (None restores the real Anthropic models)."""
    global _FACTORY
    _FACTORY = factory


def resolve_spec(model: Union[str, ModelSpec]) -> ModelSpec:
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
    if _FACTORY is not None:
        return _FACTORY(agent, spec)
    if spec.provider != "anthropic":
        raise ValueError(f"provider {spec.provider!r} is not wired up yet (model {spec.key})")
    from langchain_anthropic import ChatAnthropic  # imported lazily: tests never need it
    return ChatAnthropic(model=spec.model_id, max_tokens=spec.max_output_tokens, max_retries=3,
                         **spec.extra)


def chat_model(agent: AgentName, model: ModelLike) -> tuple[BaseChatModel, str]:
    """(chat model, model id for traces)."""
    if isinstance(model, BaseChatModel):
        return model, getattr(model, "model", None) or getattr(model, "model_name", None) or type(model).__name__
    spec = resolve_spec(model)
    return build_chat_model(agent, spec), spec.model_id


def call_llm(ctx: RunContext, agent: AgentName, llm: BaseChatModel, model_id: str,
             messages: Sequence[BaseMessage], *, tools: Optional[list[dict]] = None,
             item: Optional[str] = None, purpose: str = "") -> AIMessage:
    """One LLM call with budget check and an `llm_call` trace event."""
    ctx.reserve_llm_call()
    runnable = llm.bind_tools(tools) if tools else llm
    t0 = time.monotonic()
    reply = runnable.invoke(list(messages))
    latency_ms = int((time.monotonic() - t0) * 1000)
    if not isinstance(reply, AIMessage):  # pragma: no cover - defensive
        reply = AIMessage(content=str(getattr(reply, "content", reply)))

    usage = reply.usage_metadata or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    cached = (usage.get("input_token_details") or {}).get("cache_read")
    ctx.record_llm(in_tok, out_tok)
    n_tools = len(reply.tool_calls or [])
    ctx.emit("llm_call", agent=agent, item=item, model=model_id, input_tokens=in_tok,
             cached_input_tokens=cached, output_tokens=out_tok, latency_ms=latency_ms,
             cost_usd=None,  # filled from token prices by core/tracing.py (Shazan)
             summary=f"{agent} {purpose or 'call'}: {in_tok} in / {out_tok} out tokens"
                     + (f", {n_tools} tool call(s)" if n_tools else ""))
    return reply


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
