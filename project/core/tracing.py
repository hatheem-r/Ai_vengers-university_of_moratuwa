"""Trace writing and LLM cost (API.md §6). Owner: Shazan.

Two jobs the rest of the code leaves to this module:

1. **Cost.** `cost_usd(model_id, usage)` prices one LLM call from its LangChain
   `usage_metadata`; multi_agent/llm.py `call_llm` uses it for every `llm_call` event,
   so both systems are priced the same way. Ajey's cost numbers come from these events.
2. **Sequence numbers.** Agents and the runner emit events with `seq=1` as a placeholder.
   `TraceWriter` is the `emit` callback that gives each event its real `seq` (1, 2, 3 ...
   per run, thread-safe because Intake runs in parallel), writes `runs/<run_id>/trace.jsonl`,
   and moves large payloads to `runs/<run_id>/payloads/<seq>.json` (`payload_ref`).

    writer = TraceWriter(Path("runs") / run_id, forward=print_event)
    runner.start_run(run_id, bundle_dir, config, writer)
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .schemas import TraceEvent

# ---------------------------------------------------------------------------
# Prices: USD per million tokens, Anthropic first-party list prices (Nov 2026).
# Cache writes cost 1.25x the input price (5-minute TTL) or 2x (1-hour TTL).
# Re-check before the final benchmark and state the date in the results.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    # A higher tier above this many prompt tokens (Claude Haiku 5.5).
    tier_threshold: Optional[int] = None
    input_above: Optional[float] = None
    output_above: Optional[float] = None


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10),
    # $0.10 / $0.50 up to 100K prompt tokens, $0.50 / $2.50 above. Cache-read price is
    # assumed at the usual 0.1x of input: confirm on the pricing page before the benchmark.
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01,
                              tier_threshold=100_000, input_above=0.50, output_above=2.50),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
}


def register_price(model_id: str, price: Price) -> None:
    """Add or replace a model's price (e.g. Ajey's non-Claude Intake candidates)."""
    PRICES[model_id] = price


def usage_breakdown(usage: Optional[Mapping[str, Any]]) -> dict[str, int]:
    """Token counts from LangChain `usage_metadata`. `input_tokens` there is the whole prompt,
    including cache reads and writes (langchain-anthropic adds them back in)."""
    usage = usage or {}
    details = usage.get("input_token_details") or {}
    write_5m = int(details.get("ephemeral_5m_input_tokens") or 0)
    write_1h = int(details.get("ephemeral_1h_input_tokens") or 0)
    if not (write_5m or write_1h):
        write_5m = int(details.get("cache_creation") or 0)  # TTL not split: default TTL is 5 minutes
    return {"input": int(usage.get("input_tokens") or 0), "output": int(usage.get("output_tokens") or 0),
            "cache_read": int(details.get("cache_read") or 0), "cache_write_5m": write_5m,
            "cache_write_1h": write_1h}


def cost_usd(model_id: Optional[str], usage: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Cost of one LLM call, or None for a model with no price (never a silent 0)."""
    price = PRICES.get(model_id or "")
    if price is None:
        return None
    u = usage_breakdown(usage)
    in_price, out_price, scale = price.input, price.output, 1.0
    if price.tier_threshold is not None and u["input"] > price.tier_threshold:
        in_price = price.input_above or price.input
        out_price = price.output_above or price.output
        scale = in_price / price.input
    uncached = max(u["input"] - u["cache_read"] - u["cache_write_5m"] - u["cache_write_1h"], 0)
    total = (uncached * in_price
             + u["cache_read"] * price.cache_read * scale
             + u["cache_write_5m"] * price.input * 1.25 * scale
             + u["cache_write_1h"] * price.input * 2.0 * scale
             + u["output"] * out_price)
    return round(total / 1_000_000, 6)


# ---------------------------------------------------------------------------
# The emit callback
# ---------------------------------------------------------------------------

MAX_INLINE_PAYLOAD_BYTES = 2_000


class TraceWriter:
    """An `emit` callback: numbers events, writes trace.jsonl, offloads large payloads.

    `run_dir=None` keeps events in memory only (`.events`). `forward` also receives every
    numbered event (for printing or the API's live progress). An exception in `forward`
    is swallowed: a display problem must never fail a run.
    """

    def __init__(self, run_dir: Optional[Path] = None,
                 forward: Optional[Callable[[TraceEvent], None]] = None, start_seq: int = 0):
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.forward = forward
        self.events: list[TraceEvent] = []
        self._seq = start_seq
        self._lock = threading.Lock()
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Optional[Path]:
        return self.run_dir / "trace.jsonl" if self.run_dir is not None else None

    def __call__(self, event: TraceEvent) -> None:
        with self._lock:
            self._seq += 1
            update: dict[str, Any] = {"seq": self._seq}
            if event.payload is not None and self.run_dir is not None:
                blob = json.dumps(event.payload, default=str)
                if len(blob.encode("utf-8")) > MAX_INLINE_PAYLOAD_BYTES:
                    ref = f"payloads/{self._seq}.json"
                    (self.run_dir / "payloads").mkdir(exist_ok=True)
                    (self.run_dir / ref).write_text(blob, encoding="utf-8")
                    update.update(payload=None, payload_ref=ref)
            event = event.model_copy(update=update)
            self.events.append(event)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(event.model_dump_json() + "\n")
        if self.forward is not None:
            try:
                self.forward(event)
            except Exception:  # noqa: BLE001 - display must not break the run
                pass


def read_trace(path: Path) -> list[TraceEvent]:
    with Path(path).open(encoding="utf-8") as f:
        return [TraceEvent.model_validate_json(line) for line in f if line.strip()]


def summarize(events: Iterable[TraceEvent]) -> dict[str, Any]:
    """Run totals in the shape of API.md §4.4 `metrics` (except elapsed_s)."""
    m: dict[str, Any] = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
                         "output_tokens": 0, "cost_usd": 0.0, "unpriced_llm_calls": 0}
    for e in events:
        if e.type == "llm_call":
            m["llm_calls"] += 1
            m["input_tokens"] += e.input_tokens or 0
            m["cached_input_tokens"] += e.cached_input_tokens or 0
            m["output_tokens"] += e.output_tokens or 0
            if e.cost_usd is None:
                m["unpriced_llm_calls"] += 1
            else:
                m["cost_usd"] += e.cost_usd
        elif e.type == "tool_call":
            m["tool_calls"] += 1
    m["cost_usd"] = round(m["cost_usd"], 6)
    return m
