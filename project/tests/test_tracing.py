"""core/tracing.py: JSONL writer, prices, payload spill, summaries, run folder."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core.schemas import FACTORY_TZ, RunConfig, RunResult, TraceEvent
from core.tracing import (MemoryTrace, TraceWriter, cost_usd, load_payload, read_trace, summarize,
                          write_result)

T0 = datetime(2026, 11, 23, 9, 30, tzinfo=FACTORY_TZ)


def ev(type_: str, secs: float = 0, **kw) -> TraceEvent:
    return TraceEvent(run_id="run_t", seq=1, ts=T0 + timedelta(seconds=secs), type=type_, **kw)


def test_prices():
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert cost_usd("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(10.0)
    assert cost_usd("claude-haiku-5-5", 10_000, 1_000) == pytest.approx((10_000 * 0.10 + 1_000 * 0.50) / 1e6)
    # cached tokens are part of input_tokens and priced at the cache-read rate
    assert cost_usd("claude-sonnet-5-5", 1000, 0, cached_input_tokens=1000) == pytest.approx(1000 * 0.10 / 1e6)
    assert cost_usd("claude-sonnet-5-5", 1000, 0, cache_write_5m_tokens=1000) == pytest.approx(1000 * 2.5 / 1e6)
    # Haiku 5.5 long-prompt tier
    assert cost_usd("claude-haiku-5-5", 200_000, 1000) == pytest.approx((200_000 * 0.50 + 1000 * 2.50) / 1e6)
    assert cost_usd("fake-model", 10, 10) is None and cost_usd(None, 10, 10) is None


def test_writer_numbers_prices_and_appends(tmp_path):
    seen = []
    w = TraceWriter(tmp_path / "run_t", listeners=[seen.append])
    w(ev("run_start", summary="start"))
    w(ev("llm_call", 1, agent="planner", model="claude-sonnet-5-5", input_tokens=1000, output_tokens=100))
    w(ev("tool_call", 2, agent="planner", tool="mrp_tool", cost_usd=0))
    events = read_trace(tmp_path / "run_t")
    assert [e.seq for e in events] == [1, 2, 3] and [e.seq for e in seen] == [1, 2, 3]
    assert events[1].cost_usd == pytest.approx((1000 * 2 + 100 * 10) / 1e6)
    assert events[2].cost_usd == 0
    assert [e.seq for e in read_trace(tmp_path / "run_t", after_seq=2)] == [3]
    # a new writer on the same folder (resume after restart) continues the numbering
    TraceWriter(tmp_path / "run_t")(ev("human_decision", 3))
    assert read_trace(tmp_path / "run_t")[-1].seq == 4


def test_large_payload_is_spilled(tmp_path):
    w = TraceWriter(tmp_path, max_inline_bytes=100)
    w(ev("tool_call", payload={"rows": list(range(200))}))
    w(ev("tool_call", payload={"small": 1}))
    big, small = read_trace(tmp_path)
    assert big.payload is None and big.payload_ref == "payloads/1.json"
    assert load_payload(tmp_path, big) == {"rows": list(range(200))}
    assert small.payload == {"small": 1} and small.payload_ref is None


def test_every_line_is_a_full_trace_event(tmp_path):
    w = TraceWriter(tmp_path)
    w(ev("handoff", agent="planner", from_agent="planner", to_agent="checker", summary="Planner → Checker"))
    line = json.loads((tmp_path / "trace.jsonl").read_text().splitlines()[0])
    assert set(line) == set(TraceEvent.model_fields)       # API.md §6: every field present, nulls included
    assert line["ts"] == "2026-11-23T09:30:00+05:30"


def test_summarize_leaves_out_human_wait():
    events = MemoryTrace()
    for e in [ev("run_start"),
              ev("llm_call", 1, agent="intake", model="claude-haiku-5-5", input_tokens=1000, output_tokens=100,
                 cached_input_tokens=200),
              ev("llm_call", 2, agent="planner", model="fake", input_tokens=10, output_tokens=10),
              ev("tool_call", 3, agent="planner", tool="mrp_tool"),
              ev("handoff", 4, agent="checker", from_agent="checker", to_agent="planner"),
              ev("interrupt", 10, agent="approval"),
              ev("human_decision", 70, agent="approval"),
              ev("run_end", 71, payload={"status": "completed"})]:
        events(e)
    m = summarize(events.events)
    assert m["llm_calls"] == 2 and m["tool_calls"] == 1 and m["tokens"] == 1120
    assert m["cached_input_tokens"] == 200 and m["revisions"] == 1
    assert m["cost_complete"] is False                     # the fake model has no price
    assert m["wall_seconds"] == 71 and m["active_seconds"] == 11
    assert m["by_agent"]["intake"]["llm_calls"] == 1 and m["status"] == "completed"


def test_write_result_writes_meta(tmp_path):
    TraceWriter(tmp_path)(ev("run_start"))
    meta = write_result(tmp_path, RunResult(run_id="run_t", status="failed",
                                            error={"code": "budget_exceeded", "message": "x"}),
                        RunConfig(mode="baseline"), bundle_id="dev_A_S1_clean")
    saved = json.loads((tmp_path / "meta.json").read_text())
    assert saved == json.loads(json.dumps(meta, default=str))
    assert saved["system_id"] == "B" and saved["error"]["code"] == "budget_exceeded"
    assert saved["metrics"]["events"] == 1 and saved["bundle_id"] == "dev_A_S1_clean"
