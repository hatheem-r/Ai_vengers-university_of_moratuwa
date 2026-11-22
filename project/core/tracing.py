"""Run tracing: one TraceEvent per JSONL line, with tokens, latency and cost.

Both systems (multi-agent and baseline) trace through this module, so their
traces are directly comparable (API.md §6). Ajey's cost and latency numbers
come from these events.

Usage::

    writer = JsonlTraceWriter(run_dir / "trace.jsonl")
    tracer = Tracer(run_id, emit=writer, run_dir=run_dir)
    tracer.emit("run_start", summary="Run started")
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from project.core.schemas import TraceEvent

# Sri Lanka time, as required by API.md §1.
SL_TZ = timezone(timedelta(hours=5, minutes=30), name="+05:30")

# Payloads bigger than this (serialized bytes) go to runs/<run_id>/payloads/<seq>.json.
MAX_INLINE_PAYLOAD_BYTES = 2_000


def now() -> datetime:
    return datetime.now(SL_TZ)


# ---------------------------------------------------------------------------
# Price table (USD per million tokens, Anthropic first-party list prices)
# ---------------------------------------------------------------------------
# Source: Anthropic model pricing as of Nov 2026. Cache writes cost 1.25x the
# input price (5-minute TTL) or 2x (1-hour TTL). Re-check before the final
# benchmark and note the date in the results write-up.


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    # Above this many prompt tokens a higher tier applies (Haiku 5.5 only).
    tier_threshold: int | None = None
    input_above: float | None = None
    output_above: float | None = None

    @property
    def cache_write_5m(self) -> float:
        return self.input * 1.25

    @property
    def cache_write_1h(self) -> float:
        return self.input * 2.0


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10),
    # Haiku 5.5: $0.10 / $0.50 up to 100K prompt tokens, $0.50 / $2.50 beyond.
    # Cache-read price assumed at the standard 0.1x of input; confirm on the pricing page.
    "claude-haiku-5-5": Price(
        input=0.10, output=0.50, cache_read=0.01,
        tier_threshold=100_000, input_above=0.50, output_above=2.50,
    ),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
}


def cost_usd(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float | None:
    """Cost of one LLM call.

    ``input_tokens`` is the total prompt size, including cache reads and writes
    (the LangChain ``usage_metadata`` convention). Returns ``None`` for a model
    not in the price table, so an unknown model never shows up as free.
    """
    if model is None or model not in PRICES:
        return None
    p = PRICES[model]
    in_price, out_price = p.input, p.output
    if p.tier_threshold is not None and input_tokens > p.tier_threshold:
        in_price, out_price = p.input_above or in_price, p.output_above or out_price
        # Keep the same ratios for cache pricing in the upper tier.
        scale = in_price / p.input
    else:
        scale = 1.0
    uncached = max(input_tokens - cache_read_tokens - cache_write_5m_tokens - cache_write_1h_tokens, 0)
    total = (
        uncached * in_price
        + cache_read_tokens * p.cache_read * scale
        + cache_write_5m_tokens * p.cache_write_5m * scale
        + cache_write_1h_tokens * p.cache_write_1h * scale
        + output_tokens * out_price
    )
    return round(total / 1_000_000, 6)


def usage_from_message(message: Any) -> dict[str, int]:
    """Pull token counts out of an AIMessage's ``usage_metadata``.

    Returns keys: input_tokens, output_tokens, cache_read, cache_write_5m,
    cache_write_1h. Missing values are 0.
    """
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    write_5m = details.get("ephemeral_5m_input_tokens") or 0
    write_1h = details.get("ephemeral_1h_input_tokens") or 0
    if not (write_5m or write_1h):
        # Generic key when the API did not split writes by TTL; default TTL is 5m.
        write_5m = details.get("cache_creation") or 0
    return {
        "input_tokens": usage.get("input_tokens") or 0,
        "output_tokens": usage.get("output_tokens") or 0,
        "cache_read": details.get("cache_read") or 0,
        "cache_write_5m": write_5m,
        "cache_write_1h": write_1h,
    }


# ---------------------------------------------------------------------------
# Writers and the tracer
# ---------------------------------------------------------------------------

Emit = Callable[[TraceEvent], None]


class JsonlTraceWriter:
    """An ``emit`` callable that appends each event as one JSON line."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, event: TraceEvent) -> None:
        line = event.model_dump_json()
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_trace(path: Path | str) -> list[TraceEvent]:
    with Path(path).open(encoding="utf-8") as f:
        return [TraceEvent.model_validate_json(line) for line in f if line.strip()]


class Tracer:
    """Builds TraceEvents for one run and hands them to ``emit``.

    Thread-safe: Intake calls run in parallel and share one tracer, so ``seq``
    is assigned under a lock and stays strictly increasing.
    """

    def __init__(self, run_id: str, emit: Emit | None = None, run_dir: Path | str | None = None):
        self.run_id = run_id
        self._emit = emit or (lambda _e: None)
        self.run_dir = Path(run_dir) if run_dir else None
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, type: str, *, summary: str = "", payload: dict[str, Any] | None = None, **fields: Any) -> TraceEvent:
        with self._lock:
            self._seq += 1
            seq = self._seq
        payload_ref = None
        if payload is not None and self.run_dir is not None:
            blob = json.dumps(payload, default=str)
            if len(blob.encode("utf-8")) > MAX_INLINE_PAYLOAD_BYTES:
                ref = Path("payloads") / f"{seq}.json"
                out = self.run_dir / ref
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(blob, encoding="utf-8")
                payload, payload_ref = None, ref.as_posix()
        event = TraceEvent(
            run_id=self.run_id, seq=seq, ts=now(), type=type, summary=summary,
            payload=payload, payload_ref=payload_ref, **fields,
        )
        self._emit(event)
        return event

    # Convenience wrappers for the event types every system emits.

    def llm_call(self, *, agent: str, model: str | None, message: Any, latency_ms: int, item: str | None = None) -> TraceEvent:
        u = usage_from_message(message)
        cost = cost_usd(
            model, u["input_tokens"], u["output_tokens"], u["cache_read"], u["cache_write_5m"], u["cache_write_1h"],
        )
        tool_calls = [tc["name"] for tc in (getattr(message, "tool_calls", None) or [])]
        summary = f"{model}: {u['input_tokens']} in / {u['output_tokens']} out"
        if tool_calls:
            summary += f", calls {', '.join(tool_calls)}"
        return self.emit(
            "llm_call", agent=agent, item=item, model=model, summary=summary,
            input_tokens=u["input_tokens"], cached_input_tokens=u["cache_read"],
            output_tokens=u["output_tokens"], latency_ms=latency_ms, cost_usd=cost,
        )

    def handoff(self, from_agent: str, to_agent: str, summary: str, payload: dict[str, Any] | None = None) -> TraceEvent:
        return self.emit(
            "handoff", agent=from_agent, from_agent=from_agent, to_agent=to_agent,
            summary=summary, payload=payload,
        )


# ---------------------------------------------------------------------------
# Totals (for RunStatus.metrics and the benchmark)
# ---------------------------------------------------------------------------


def summarize(events: Iterable[TraceEvent]) -> dict[str, Any]:
    """Totals in the shape of API.md §4.4 ``metrics`` (except elapsed_s)."""
    m = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for e in events:
        if e.type == "llm_call":
            m["llm_calls"] += 1
            m["input_tokens"] += e.input_tokens or 0
            m["cached_input_tokens"] += e.cached_input_tokens or 0
            m["output_tokens"] += e.output_tokens or 0
            m["cost_usd"] += e.cost_usd or 0.0
        elif e.type == "tool_call":
            m["tool_calls"] += 1
    m["cost_usd"] = round(m["cost_usd"], 6)
    return m
