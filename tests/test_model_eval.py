from pathlib import Path

import pytest

from evaluation.model_eval.harness import Candidate, DocumentCase, evaluate_candidates, select_candidate, validate_development_set


def cases(count=40, scanned=10):
    return [DocumentCase(f"doc-{index}", Path(f"doc-{index}.pdf"), {"value": index}, messy=index % 2 == 0, scanned=index < scanned) for index in range(count)]


def test_dataset_minimums_are_enforced():
    validate_development_set(cases())
    with pytest.raises(ValueError, match="40"):
        validate_development_set(cases(39, 10))
    with pytest.raises(ValueError, match="10 scanned"):
        validate_development_set(cases(40, 9))


def test_candidate_requires_verified_identifier():
    with pytest.raises(ValueError):
        Candidate("Claude Haiku 5.5", "")


def test_harness_measures_outputs_and_keeps_failures():
    candidates = [Candidate("A", "provider/a")]
    dataset = cases(2, 1)

    def extractor(path, model):
        index = int(path.stem.split("-")[-1])
        if index == 1:
            raise RuntimeError("provider failed")
        return {"extraction": {"value": index}, "schema_valid": True, "cost_usd": 0.01}

    result = evaluate_candidates(candidates, dataset, extractor, enforce_project_minimums=False)
    summary = result["candidates"][0]
    assert summary["f1"] == 0.5
    assert summary["precision"] == 0.5
    assert summary["recall"] == 0.5
    assert summary["schema_valid_rate"] == 0.5
    assert summary["failed_documents"] == 1
    assert result["documents"][1]["error"].startswith("RuntimeError")


def test_selection_uses_both_subgroups_then_lowest_cost():
    result = {
        "candidates": [
            {"candidate": "best", "n_documents": 40, "messy_f1": 0.95, "scanned_f1": 0.92, "cost_per_document_usd": 0.05},
            {"candidate": "cheap-near", "n_documents": 40, "messy_f1": 0.93, "scanned_f1": 0.90, "cost_per_document_usd": 0.01},
            {"candidate": "cheap-but-weak-scan", "n_documents": 40, "messy_f1": 0.95, "scanned_f1": 0.89, "cost_per_document_usd": 0.001},
        ]
    }
    assert select_candidate(result)["candidate"] == "cheap-near"


def test_no_live_calls_or_winner_are_created_by_import():
    assert Path("results/model_eval/decision.md").read_text().endswith("Pending.\n")
