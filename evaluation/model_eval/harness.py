"""Reusable, provider-neutral Intake extraction evaluation harness.

The live adapter should wrap Hatheem's future ``extract_document(path,
model=...)`` and return ``extraction``, ``schema_valid`` and ``cost_usd``.
No candidate identifiers are guessed here: verified provider identifiers must
be supplied explicitly in ``Candidate.provider_model_id`` before a live run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

from evaluation.metrics import field_level_f1


@dataclass(frozen=True)
class Candidate:
    name: str
    provider_model_id: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.provider_model_id.strip():
            raise ValueError("Candidate name and verified provider_model_id are required.")


@dataclass(frozen=True)
class DocumentCase:
    document_id: str
    path: Path
    extraction_key: Mapping[str, Any]
    messy: bool = False
    scanned: bool = False

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not isinstance(self.extraction_key, Mapping):
            raise ValueError("Document cases require an ID and extraction-key mapping.")


Extractor = Callable[[Path, str], Mapping[str, Any]]


def validate_development_set(cases: Sequence[DocumentCase], *, minimum_documents: int = 40, minimum_scanned: int = 10) -> None:
    if len(cases) < minimum_documents:
        raise ValueError(f"Development set requires at least {minimum_documents} documents.")
    if sum(case.scanned for case in cases) < minimum_scanned:
        raise ValueError(f"Development set requires at least {minimum_scanned} scanned documents.")
    if len({case.document_id for case in cases}) != len(cases):
        raise ValueError("Document IDs must be unique.")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_candidates(
    candidates: Sequence[Candidate],
    cases: Sequence[DocumentCase],
    extractor: Extractor,
    *,
    enforce_project_minimums: bool = True,
) -> dict[str, Any]:
    """Evaluate each candidate on the same development documents.

    Extractor results must contain an extraction mapping, schema_valid boolean
    and nonnegative cost_usd. Failures remain in per-document results and count
    as invalid zero-F1 outputs instead of disappearing from the denominator.
    """
    if enforce_project_minimums:
        validate_development_set(cases)
    elif not cases:
        raise ValueError("At least one development document is required.")
    if not candidates or len({candidate.name for candidate in candidates}) != len(candidates):
        raise ValueError("Candidates must be non-empty and uniquely named.")

    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_rows: list[dict[str, Any]] = []
        for case in cases:
            started = perf_counter()
            try:
                result = extractor(case.path, candidate.provider_model_id)
                measured_latency = perf_counter() - started
                if not isinstance(result, Mapping):
                    raise ValueError("Extractor must return a mapping.")
                extraction = result.get("extraction")
                schema_valid = result.get("schema_valid")
                cost = result.get("cost_usd")
                if not isinstance(extraction, Mapping) or not isinstance(schema_valid, bool):
                    raise ValueError("Extractor result requires extraction mapping and schema_valid boolean.")
                if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
                    raise ValueError("Extractor result requires nonnegative cost_usd.")
                field_scores = field_level_f1(extraction, case.extraction_key)
                row = {
                    "candidate": candidate.name,
                    "provider_model_id": candidate.provider_model_id,
                    "document_id": case.document_id,
                    "messy": case.messy,
                    "scanned": case.scanned,
                    "precision": field_scores["precision"],
                    "recall": field_scores["recall"],
                    "f1": field_scores["f1"],
                    "schema_valid": schema_valid,
                    "latency_s": measured_latency,
                    "cost_usd": float(cost),
                    "error": None,
                }
            except Exception as exc:  # experiment failures are measured outcomes
                row = {
                    "candidate": candidate.name,
                    "provider_model_id": candidate.provider_model_id,
                    "document_id": case.document_id,
                    "messy": case.messy,
                    "scanned": case.scanned,
                    "precision": 0.0,
                    "recall": 0.0,
                    "f1": 0.0,
                    "schema_valid": False,
                    "latency_s": perf_counter() - started,
                    "cost_usd": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            candidate_rows.append(row)
            rows.append(row)
        summaries.append(
            {
                "candidate": candidate.name,
                "provider_model_id": candidate.provider_model_id,
                "n_documents": len(candidate_rows),
                "precision": _mean([row["precision"] for row in candidate_rows]),
                "recall": _mean([row["recall"] for row in candidate_rows]),
                "f1": _mean([row["f1"] for row in candidate_rows]),
                "messy_f1": _mean([row["f1"] for row in candidate_rows if row["messy"]]),
                "scanned_f1": _mean([row["f1"] for row in candidate_rows if row["scanned"]]),
                "schema_valid_rate": _mean([float(row["schema_valid"]) for row in candidate_rows]),
                "median_latency_s": median(row["latency_s"] for row in candidate_rows),
                "cost_per_document_usd": _mean([row["cost_usd"] for row in candidate_rows]),
                "failed_documents": sum(row["error"] is not None for row in candidate_rows),
            }
        )
    return {"dataset": {"n_documents": len(cases), "n_scanned": sum(case.scanned for case in cases)}, "candidates": summaries, "documents": rows}


def select_candidate(result: Mapping[str, Any], *, tolerance: float = 0.02) -> Mapping[str, Any]:
    """Choose the cheapest candidate within tolerance of both best subgroup F1s."""
    candidates = result.get("candidates")
    if not isinstance(candidates, Sequence) or not candidates:
        raise ValueError("Measured candidate summaries are required.")
    if any(not isinstance(row, Mapping) or row.get("n_documents", 0) <= 0 for row in candidates):
        raise ValueError("Candidate summaries are incomplete.")
    best_messy = max(float(row["messy_f1"]) for row in candidates)
    best_scanned = max(float(row["scanned_f1"]) for row in candidates)
    eligible = [
        row
        for row in candidates
        if float(row["messy_f1"]) >= best_messy - tolerance
        and float(row["scanned_f1"]) >= best_scanned - tolerance
    ]
    if not eligible:
        raise ValueError("No candidate satisfies the selection rule.")
    return min(eligible, key=lambda row: (float(row["cost_per_document_usd"]), str(row["candidate"])))
