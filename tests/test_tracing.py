import json
import threading

import pytest

from project.core.schemas import TraceEvent
from project.core.tracing import (
    MAX_INLINE_PAYLOAD_BYTES,
    JsonlTraceWriter,
    Tracer,
    cost_usd,
    read_trace,
    summarize,
    usage_from_message,
)
from tests.conftest import ai


def test_cost_sonnet_plain():
    # 1M in at $2 + 1M out at $10
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)


def test_cost_counts_cache_reads_at_cache_price():
    # 1M prompt tokens, 800k of them cache reads: 200k * $2 + 800k * $0.10 = $0.48
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 0, cache_read_tokens=800_000) == pytest.approx(0.48)


def test_cost_cache_writes_at_premium():
    # 100k written to 5m cache at 1.25 * $2 = $0.25
    assert cost_usd("claude-sonnet-5-5", 100_000, 0, cache_write_5m_tokens=100_000) == pytest.approx(0.25)


def test_cost_haiku_upper_tier():
    assert cost_usd("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    assert cost_usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)


def test_cost_unknown_model_is_none_not_zero():
    assert cost_usd("some-other-model", 1000, 1000) is None
    assert cost_usd(None, 1000, 1000) is None


def test_usage_from_message_reads_langchain_usage():
    u = usage_from_message(ai(input_tokens=5000, output_tokens=300, cache_read=4000))
    assert u == {"input_tokens": 5000, "output_tokens": 300, "cache_read": 4000, "cache_write_5m": 0, "cache_write_1h": 0}


def test_tracer_llm_call_event_has_tokens_latency_cost(events, tracer):
    e = tracer.llm_call(agent="agent", model="claude-sonnet-5-5", message=ai(input_tokens=10_000, output_tokens=1_000), latency_ms=420)
    assert e.type == "llm_call" and e.seq == 1
    assert (e.input_tokens, e.cached_input_tokens, e.output_tokens, e.latency_ms) == (10_000, 0, 1_000, 420)
    assert e.cost_usd == pytest.approx(0.03)
    assert e.ts.utcoffset().total_seconds() == 5.5 * 3600


def test_large_payload_goes_to_file(events, tracer, tmp_path):
    big = {"rows": ["x" * 100] * (MAX_INLINE_PAYLOAD_BYTES // 50)}
    e = tracer.emit("tool_call", agent="agent", tool="mrp_tool", payload=big)
    assert e.payload is None and e.payload_ref == f"payloads/{e.seq}.json"
    assert json.loads((tmp_path / e.payload_ref).read_text()) == big
    small = tracer.emit("tool_call", agent="agent", tool="mrp_tool", payload={"ok": True})
    assert small.payload == {"ok": True} and small.payload_ref is None


def test_seq_is_unique_across_threads(events, tracer):
    def work():
        for _ in range(200):
            tracer.emit("tool_call", agent="intake")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(e.seq for e in events) == list(range(1, 1601))


def test_jsonl_roundtrip_and_summary(tmp_path):
    path = tmp_path / "trace.jsonl"
    t = Tracer("run_x", emit=JsonlTraceWriter(path))
    t.emit("run_start", summary="start")
    t.llm_call(agent="agent", model="claude-sonnet-5-5", message=ai(input_tokens=2000, output_tokens=100, cache_read=1500), latency_ms=10)
    t.emit("tool_call", agent="agent", tool="mrp_tool", cost_usd=0.0)
    t.emit("run_end")
    events = read_trace(path)
    assert [e.type for e in events] == ["run_start", "llm_call", "tool_call", "run_end"]
    assert all(isinstance(e, TraceEvent) for e in events)
    m = summarize(events)
    assert m["llm_calls"] == 1 and m["tool_calls"] == 1
    assert (m["input_tokens"], m["cached_input_tokens"], m["output_tokens"]) == (2000, 1500, 100)
    assert m["cost_usd"] == pytest.approx((500 * 2 + 1500 * 0.10 + 100 * 10) / 1e6)
