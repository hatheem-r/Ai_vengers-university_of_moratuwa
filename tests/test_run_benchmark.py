from pathlib import Path

import pytest

from evaluation.run_benchmark import Budget, SystemSpec, execute_benchmark, planned_runs, summarize_trace


def test_project_plan_has_126_runs_for_18_bundles():
    bundles = [Path(f"A_S{index}_clean") for index in range(18)]
    plans = planned_runs(bundles, base_seed=7)
    assert len(plans) == 126
    assert {(plan["system_id"], plan["repeat"]) for plan in plans} >= {("B", 3), ("M-S", 3), ("M-mix", 1)}


def test_trace_summary_uses_actual_trace_fields():
    summary = summarize_trace([
        {"type": "llm_call", "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01, "latency_ms": 500},
        {"type": "tool_call", "latency_ms": 100},
    ])
    assert summary == {"llm_calls": 1, "input_tokens": 100, "output_tokens": 20, "tokens": 120, "cost_usd": 0.01, "latency_s": 0.6}


def test_benchmark_is_restartable_and_skips_completed_runs(tmp_path):
    calls = []
    bundle = tmp_path / "dev_A_S1_clean"
    bundle.mkdir()

    def runner(run_id, bundle_dir, config):
        calls.append((run_id, bundle_dir, config))
        return {"status": "completed", "trace_events": [], "plan": {}}

    systems = [SystemSpec("B", "baseline", "sonnet", 1)]
    first = execute_benchmark([bundle], tmp_path / "out", runner, systems=systems, dry_run_only=True)
    second = execute_benchmark([bundle], tmp_path / "out", runner, systems=systems, dry_run_only=True)
    assert len(calls) == 1
    assert first == second
    assert calls[0][2]["auto_approve"] is True
    assert "answer_key" not in calls[0][2]


def test_failed_run_is_recorded_and_retried(tmp_path):
    bundle = tmp_path / "dev_A"
    bundle.mkdir()
    attempts = 0

    def runner(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("crash")
        return {"status": "completed", "trace_events": []}

    systems = [SystemSpec("B", "baseline", "sonnet", 1)]
    first = execute_benchmark([bundle], tmp_path / "out", runner, systems=systems)
    second = execute_benchmark([bundle], tmp_path / "out", runner, systems=systems)
    assert first[0]["status"] == "failed"
    assert second[0]["status"] == "completed"
    assert len(second[0]["attempts"]) == 2


def test_budget_overrun_is_a_visible_failure(tmp_path):
    bundle = tmp_path / "dev_A"
    bundle.mkdir()

    def runner(*args):
        return {"status": "completed", "trace_events": [{"type": "llm_call", "input_tokens": 11}]}

    row = execute_benchmark(
        [bundle], tmp_path / "out", runner,
        systems=[SystemSpec("B", "baseline", "sonnet", 1)],
        budget=Budget(max_tokens=10),
    )[0]
    assert row["status"] == "failed"
    assert row["error"] == {"code": "budget_exceeded", "limits": ["max_tokens"]}


def test_test_bundles_cannot_be_used_in_development_dry_run(tmp_path):
    with pytest.raises(ValueError, match="dev_"):
        execute_benchmark([tmp_path / "A_S1_clean"], tmp_path / "out", lambda *args: {}, dry_run_only=True)
