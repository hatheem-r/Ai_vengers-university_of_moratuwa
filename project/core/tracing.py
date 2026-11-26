"""Traces: the JSONL writer every run uses, the token price table, and trace summaries.

Both systems send every event through one `emit(TraceEvent)` callback (core/runner.py).
`TraceWriter` is that callback for a run folder (API.md §7):

    runs/<run_id>/
      trace.jsonl      one TraceEvent per line (API.md §6)
      payloads/        large payloads, referenced by `payload_ref`
      plan_v<N>.json   one file per plan version          (write_result)
      meta.json        config, status, timestamps, metrics (write_result)

    writer = TraceWriter(Path("runs") / run_id)
    result = runner.start_run(run_id, bundle_dir, config, writer)

What the writer does to each event, so backends don't have to:
- renumbers `seq` 1, 2, 3 ... per run (backends send seq=1 as a placeholder);
- fills `cost_usd` on `llm_call` events from the token counts and PRICES;
- moves a payload bigger than `max_inline_bytes` to payloads/<seq>.json and sets `payload_ref`;
- appends and flushes one line, under a lock (Intake calls run in parallel);
- forwards the final event to any listeners (the API's progress view, a console printer).

Costs come only from here (Ajey's benchmark reads `cost_usd` straight from traces).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from .schemas import FACTORY_TZ, RunConfig, RunResult, TraceEvent

Emit = Callable[[TraceEvent], None]


# --- prices ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Price:
    """US dollars per million tokens (Anthropic first-party list prices)."""
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float
    # Haiku 5.5 charges more once a prompt is over 100K tokens.
    long_threshold: Optional[int] = None
    long_input: Optional[float] = None
    long_output: Optional[float] = None


def _price(inp: float, out: float, cache_read: Optional[float] = None, **kw: Any) -> Price:
    return Price(input=inp, output=out, cache_read=cache_read if cache_read is not None else inp * 0.1,
                 cache_write_5m=inp * 1.25, cache_write_1h=inp * 2.0, **kw)


# Checked 22 Nov 2026. Cache writes are 1.25x input (5-minute TTL) and 2x input (1-hour TTL).
PRICES: dict[str, Price] = {
    "claude-haiku-5-5": _price(0.10, 0.50, long_threshold=100_000, long_input=0.50, long_output=2.50),
    "claude-sonnet-5-5": _price(2.00, 10.00, cache_read=0.10),
    "claude-opus-5-5": _price(4.00, 20.00, cache_read=0.20),
    "claude-sonnet-5": _price(2.00, 10.00),
    "claude-haiku-4-5": _price(1.00, 5.00),
}


def register_price(model_id: str, price: Price) -> None:
    """Add a price for a model under evaluation (Ajey's Intake candidates)."""
    PRICES[model_id] = price


def cost_usd(model_id: Optional[str], input_tokens: int, output_tokens: int, *,
             cached_input_tokens: int = 0, cache_write_5m_tokens: int = 0,
             cache_write_1h_tokens: int = 0) -> Optional[float]:
    """Dollar cost of one call, or None for a model without a price (fake models, unknown IDs).

    `input_tokens` is the *total* prompt size, cached parts included (that is what LangChain's
    usage_metadata reports for Anthropic models); the cached and cache-write parts are priced
    separately and the rest at the normal input rate."""
    price = PRICES.get(model_id or "")
    if price is None:
        return None
    inp, out = price.input, price.output
    if price.long_threshold is not None and input_tokens > price.long_threshold:
        inp, out = price.long_input or inp, price.long_output or out
        scale = inp / price.input
    else:
        scale = 1.0
    plain = max(0, input_tokens - cached_input_tokens - cache_write_5m_tokens - cache_write_1h_tokens)
    dollars = (plain * inp + output_tokens * out
               + cached_input_tokens * price.cache_read * scale
               + cache_write_5m_tokens * price.cache_write_5m * scale
               + cache_write_1h_tokens * price.cache_write_1h * scale) / 1_000_000
    return round(dollars, 6)


def event_cost(event: TraceEvent) -> Optional[float]:
    """Cost of an `llm_call` event from its token fields (cache writes are read from the payload)."""
    if event.type != "llm_call" or event.input_tokens is None:
        return None
    p = event.payload or {}
    return cost_usd(event.model, event.input_tokens, event.output_tokens or 0,
                    cached_input_tokens=event.cached_input_tokens or 0,
                    cache_write_5m_tokens=int(p.get("cache_write_5m_tokens") or 0),
                    cache_write_1h_tokens=int(p.get("cache_write_1h_tokens") or 0))


# --- the writer ------------------------------------------------------------------------

class TraceWriter:
    """The `emit` callback for one run: writes runs/<run_id>/trace.jsonl (see module doc).

    Re-opening a folder (resume after a restart) continues the seq numbers already in the file."""

    def __init__(self, run_dir: Union[str, Path], *, listeners: Iterable[Emit] = (),
                 max_inline_bytes: int = 4096) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"
        self.max_inline_bytes = max_inline_bytes
        self.listeners: list[Emit] = list(listeners)
        self._lock = threading.Lock()
        self._seq = _last_seq(self.path)

    def __call__(self, event: TraceEvent) -> None:
        with self._lock:
            self._seq += 1
            event = prepare_event(event, self._seq, self.run_dir, self.max_inline_bytes)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        for listener in self.listeners:
            listener(event)

    @property
    def last_seq(self) -> int:
        return self._seq


class MemoryTrace:
    """An `emit` callback that keeps events in a list (tests, the benchmark's dry runs).
    Applies the same seq numbering and cost filling as TraceWriter."""

    def __init__(self, *, listeners: Iterable[Emit] = ()) -> None:
        self.events: list[TraceEvent] = []
        self.listeners = list(listeners)
        self._lock = threading.Lock()

    def __call__(self, event: TraceEvent) -> None:
        with self._lock:
            event = prepare_event(event, len(self.events) + 1, None, None)
            self.events.append(event)
        for listener in self.listeners:
            listener(event)


def prepare_event(event: TraceEvent, seq: int, run_dir: Optional[Path],
                  max_inline_bytes: Optional[int]) -> TraceEvent:
    """Renumber, fill cost, spill a large payload. Returns a new event."""
    update: dict[str, Any] = {"seq": seq}
    if event.type == "llm_call" and event.cost_usd is None:
        update["cost_usd"] = event_cost(event)
    if run_dir is not None and max_inline_bytes is not None and event.payload is not None:
        blob = json.dumps(event.payload, default=str, separators=(",", ":"))
        if len(blob.encode()) > max_inline_bytes:
            ref = f"payloads/{seq}.json"
            (run_dir / "payloads").mkdir(exist_ok=True)
            (run_dir / ref).write_text(blob, encoding="utf-8")
            update.update(payload=None, payload_ref=ref)
    return event.model_copy(update=update)


def _last_seq(path: Path) -> int:
    if not path.exists():
        return 0
    last = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    last = max(last, int(json.loads(line).get("seq", 0)))
                except (ValueError, AttributeError):
                    continue
    return last


def console_printer(e: TraceEvent) -> None:
    """A listener that prints one line per event (CLI)."""
    extra = ""
    if e.type == "llm_call":
        cost = f", ${e.cost_usd:.4f}" if e.cost_usd is not None else ""
        extra = f"  [{e.input_tokens} in / {e.output_tokens} out, {e.latency_ms} ms{cost}]"
    print(f"{e.seq:>4} {e.ts:%H:%M:%S} {e.type:<15} {e.agent or '':<8} {e.item or ''} {e.summary}{extra}",
          flush=True)


# --- reading and summarising -----------------------------------------------------------

def read_trace(path: Union[str, Path], *, after_seq: int = 0) -> list[TraceEvent]:
    """Events from a trace.jsonl (or a run folder), optionally only those after `after_seq`."""
    path = Path(path)
    if path.is_dir():
        path = path / "trace.jsonl"
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ev = TraceEvent.model_validate_json(line)
                if ev.seq > after_seq:
                    events.append(ev)
    return events


def load_payload(run_dir: Union[str, Path], event: TraceEvent) -> Optional[dict]:
    """An event's payload, whether inline or spilled to payloads/<seq>.json."""
    if event.payload is not None:
        return event.payload
    if event.payload_ref:
        return json.loads((Path(run_dir) / event.payload_ref).read_text(encoding="utf-8"))
    return None


def summarize(events: Iterable[TraceEvent]) -> dict[str, Any]:
    """Run metrics from a trace: what meta.json and the benchmark report use.

    `wall_seconds` is first to last event; `active_seconds` leaves out time spent waiting
    for the human (interrupt -> human_decision)."""
    events = sorted(events, key=lambda e: e.seq)
    llm = [e for e in events if e.type == "llm_call"]
    tools = [e for e in events if e.type == "tool_call"]
    by_agent: dict[str, dict[str, Any]] = {}
    for e in llm:
        a = by_agent.setdefault(e.agent or "?", {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                 "cost_usd": 0.0})
        a["llm_calls"] += 1
        a["input_tokens"] += e.input_tokens or 0
        a["output_tokens"] += e.output_tokens or 0
        a["cost_usd"] = round(a["cost_usd"] + (e.cost_usd or 0.0), 6)
    tool_counts: dict[str, int] = {}
    for e in tools:
        tool_counts[e.tool or "?"] = tool_counts.get(e.tool or "?", 0) + 1

    wall = (events[-1].ts - events[0].ts).total_seconds() if events else 0.0
    waiting, paused_at = 0.0, None
    for e in events:
        if e.type == "interrupt":
            paused_at = e.ts
        elif e.type == "human_decision" and paused_at is not None:
            waiting += (e.ts - paused_at).total_seconds()
            paused_at = None
    costs = [e.cost_usd for e in llm]
    in_tok = sum(e.input_tokens or 0 for e in llm)
    out_tok = sum(e.output_tokens or 0 for e in llm)
    end = next((e for e in reversed(events) if e.type == "run_end"), None)
    return {
        "events": len(events),
        "llm_calls": len(llm),
        "tool_calls": len(tools),
        "tool_calls_by_tool": tool_counts,
        "input_tokens": in_tok,
        "cached_input_tokens": sum(e.cached_input_tokens or 0 for e in llm),
        "output_tokens": out_tok,
        "tokens": in_tok + out_tok,
        "cost_usd": round(sum(c for c in costs if c is not None), 6),
        "cost_complete": all(c is not None for c in costs),
        "llm_latency_ms": sum(e.latency_ms or 0 for e in llm),
        "wall_seconds": round(wall, 3),
        "active_seconds": round(max(0.0, wall - waiting), 3),
        "handoffs": sum(e.type == "handoff" for e in events),
        "revisions": sum(e.type == "handoff" and e.from_agent == "checker" and e.to_agent == "planner"
                         for e in events),
        "errors": sum(e.type == "error" for e in events),
        "by_agent": by_agent,
        "status": (end.payload or {}).get("status") if end else None,
    }


# --- run folder ------------------------------------------------------------------------

def write_result(run_dir: Union[str, Path], result: RunResult, config: Optional[RunConfig] = None,
                 *, bundle_id: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """Write plan_v<N>.json (if there is a plan) and meta.json with metrics from the trace.
    Safe to call after every start/resume; meta.json is rewritten each time."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if result.plan is not None:
        (run_dir / f"plan_v{result.plan_version}.json").write_text(result.plan.model_dump_json(indent=2),
                                                                   encoding="utf-8")
    meta_path = run_dir / "meta.json"
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    events = read_trace(run_dir) if (run_dir / "trace.jsonl").exists() else []
    meta.update({
        "run_id": result.run_id,
        "status": result.status,
        "plan_version": result.plan_version,
        "error": result.error.model_dump() if result.error else None,
        "updated_at": datetime.now(FACTORY_TZ).isoformat(),
        "metrics": summarize(events),
    })
    if config is not None:
        meta["config"] = config.model_dump(mode="json")
        meta["system_id"] = config.system_id
    if bundle_id is not None:
        meta["bundle_id"] = bundle_id
    if events and "started_at" not in meta:
        meta["started_at"] = events[0].ts.isoformat()
    meta.update(extra or {})
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta
