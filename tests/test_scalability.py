from pathlib import Path

import pytest

from evaluation.scalability import ScalabilityCase, execute_scalability, scalability_systems


def test_only_1_3_5_orders_are_allowed():
    with pytest.raises(ValueError):
        ScalabilityCase(2, Path("scale_2"))


def test_default_systems_are_b_and_m_mix():
    assert [system.system_id for system in scalability_systems()] == ["B", "M-mix"]
    assert [system.system_id for system in scalability_systems(include_m_s=True)] == ["B", "M-mix", "M-S"]


def test_scale_execution_returns_measured_chart_rows(tmp_path):
    cases = []
    for count in (1, 3, 5):
        bundle = tmp_path / f"scale_{count}"
        bundle.mkdir()
        cases.append(ScalabilityCase(count, bundle))

    def runner(run_id, bundle_dir, config):
        count = int(bundle_dir.name.split("_")[-1])
        return {
            "status": "completed",
            "trace_events": [{"type": "llm_call", "input_tokens": count * 10, "output_tokens": count, "cost_usd": count * 0.01, "latency_ms": count * 1000}],
        }

    def evaluator(result, bundle_dir):
        return {"pqs": 90, "passed": True}

    rows = execute_scalability(cases, tmp_path / "results", runner, evaluator)
    assert len(rows) == 6
    one = next(row for row in rows if row["system_id"] == "B" and row["orders"] == 1)
    assert one == {
        "bundle_id": "scale_1", "orders": 1, "system_id": "B", "pqs": 90,
        "passed": True, "success": True, "latency_s": 1.0, "input_tokens": 10,
        "output_tokens": 1, "tokens": 11, "cost_usd": 0.01, "error": None,
    }


def test_all_three_designated_sizes_are_required(tmp_path):
    with pytest.raises(ValueError, match="1, 3 and 5"):
        execute_scalability(
            [ScalabilityCase(1, tmp_path / "one")], tmp_path / "out", lambda *args: {}, lambda *args: {}
        )
