import csv
import json

import pytest

from evaluation.report import generate_report, paired_comparison


def mock_rows():
    rows = []
    for bundle, scenario, mess in (("A_S1_clean", "S1", "clean"), ("A_S3_messy", "S3", "messy")):
        for system, pqs, success in (("B", 80, True), ("M-S", 84, True), ("M-mix", None, False)):
            rows.append({
                "bundle_id": bundle, "system_id": system, "scenario": scenario, "mess_level": mess,
                "success": success, "passed": success and pqs >= 80, "pqs": pqs,
                "latency_s": 10 if success else None, "tokens": 100 if success else None,
                "cost_usd": 0.1 if success else None, "metrics": {"q1": 0.9 if success else 0},
                "error": None if success else "model_error",
            })
    return rows


CONFIG = {"bundles": 2, "repeats": {"B": 1, "M-S": 1, "M-mix": 1}, "budgets": {}}
SYSTEMS = [
    {"system_id": "B", "label": "Baseline fixture", "mode": "baseline", "models": {"agent": "fixture"}},
    {"system_id": "M-S", "label": "Multi fixture", "mode": "multi_agent", "models": {"intake": "fixture"}},
    {"system_id": "M-mix", "label": "Mix fixture", "mode": "multi_agent", "models": {"intake": "fixture"}},
]


def test_paired_comparison_averages_by_bundle_and_uses_two_point_ties():
    comparison = paired_comparison(mock_rows())
    assert comparison["n_bundles"] == 2
    assert comparison["mean_pqs_diff"] == 4
    assert (comparison["wins"], comparison["ties"], comparison["losses"]) == (2, 0, 0)
    assert comparison["wilcoxon"]["status"] == "not_run_fewer_than_5_nonzero_pairs"


def test_report_writes_api_shaped_json_csv_and_charts(tmp_path):
    result_dir = generate_report(
        "mock_eval", mock_rows(), tmp_path, data_label="mock", config=CONFIG, system_metadata=SYSTEMS,
        limitations=["Synthetic fixture; not benchmark evidence."],
    )
    assert {path.name for path in result_dir.iterdir()} == {"summary.json", "runs.csv", "pqs_by_system.svg", "cost_by_system.svg"}
    summary = json.loads((result_dir / "summary.json").read_text())
    assert summary["is_mock"] is True
    m_mix = next(system for system in summary["overall"] if system["system_id"] == "M-mix")
    assert m_mix["run_success_rate"] == 0
    assert m_mix["pqs_mean"] is None
    assert len(summary["failed_runs"]) == 2
    with (result_dir / "runs.csv").open() as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 6
    assert "q1" in csv_rows[0]


def test_failed_runs_remain_in_pass_and_success_denominators(tmp_path):
    result_dir = generate_report("mock", mock_rows(), tmp_path, data_label="mock", config=CONFIG, system_metadata=SYSTEMS)
    summary = json.loads((result_dir / "summary.json").read_text())
    assert sum(system["n_runs"] for system in summary["overall"]) == 6


def test_report_rejects_unlabelled_or_incomplete_data(tmp_path):
    with pytest.raises(ValueError, match="measured or mock"):
        generate_report("x", mock_rows(), tmp_path, data_label="estimated", config=CONFIG, system_metadata=SYSTEMS)
    with pytest.raises(ValueError, match="missing"):
        generate_report("x", [{"bundle_id": "x"}], tmp_path, config=CONFIG, system_metadata=SYSTEMS)


def test_human_and_scalability_sections_follow_api_shape(tmp_path):
    human = [{"participant": "H1", "bundle_id": "A", "pqs": 80, "minutes": 90, "cost_lkr": 1500, "hourly_rate_lkr": 1000, "rate_source": "fixture"}]
    scale = [{"system_id": "B", "orders": 1, "pqs": 80, "latency_s": 4, "tokens": 100, "cost_usd": 0.1}]
    result = generate_report("mock", mock_rows(), tmp_path, data_label="mock", config=CONFIG, system_metadata=SYSTEMS, human=human, scalability=scale)
    summary = json.loads((result / "summary.json").read_text())
    assert summary["human"]["summary"] == {"minutes_mean": 90.0, "pqs_mean": 80.0}
    assert summary["scalability"][0]["n_orders"] == 1
