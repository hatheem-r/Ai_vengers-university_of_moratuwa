"""Scalability experiment orchestration for explicit 1/3/5-order bundles."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.run_benchmark import Budget, SystemSpec, execute_benchmark


@dataclass(frozen=True)
class ScalabilityCase:
    order_count: int
    bundle_dir: Path

    def __post_init__(self) -> None:
        if self.order_count not in {1, 3, 5}:
            raise ValueError("Scalability order_count must be 1, 3 or 5.")


def scalability_systems(*, include_m_s: bool = False) -> tuple[SystemSpec, ...]:
    systems = [SystemSpec("B", "baseline", "sonnet", 1), SystemSpec("M-mix", "multi_agent", "mix", 1)]
    if include_m_s:
        systems.append(SystemSpec("M-S", "multi_agent", "sonnet", 1))
    return tuple(systems)


def execute_scalability(
    cases: Sequence[ScalabilityCase],
    output_dir: Path,
    runner: Callable[[str, Path, Mapping[str, Any]], Mapping[str, Any]],
    evaluator: Callable[[Mapping[str, Any], Path], Mapping[str, Any]],
    *,
    budget: Budget = Budget(),
    include_m_s: bool = False,
    base_seed: int = 0,
) -> list[dict[str, Any]]:
    """Run the scale matrix and return flat, chart-ready measured rows."""
    if {case.order_count for case in cases} != {1, 3, 5}:
        raise ValueError("Exactly one designated case for each of 1, 3 and 5 orders is required.")
    if len(cases) != 3 or len({case.bundle_dir.name for case in cases}) != 3:
        raise ValueError("Scalability cases require three uniquely named bundle directories.")
    counts = {case.bundle_dir.name: case.order_count for case in cases}
    records = execute_benchmark(
        [case.bundle_dir for case in cases],
        output_dir,
        runner,
        evaluator=evaluator,
        budget=budget,
        base_seed=base_seed,
        systems=scalability_systems(include_m_s=include_m_s),
    )
    rows: list[dict[str, Any]] = []
    for record in records:
        trace = record.get("trace") or {}
        score = record.get("score") or {}
        rows.append(
            {
                "bundle_id": record["bundle_id"],
                "orders": counts[record["bundle_id"]],
                "system_id": record["system_id"],
                "pqs": score.get("pqs"),
                "passed": score.get("passed"),
                "success": record["status"] == "completed",
                "latency_s": trace.get("latency_s"),
                "input_tokens": trace.get("input_tokens"),
                "output_tokens": trace.get("output_tokens"),
                "tokens": trace.get("tokens"),
                "cost_usd": trace.get("cost_usd"),
                "error": record.get("error"),
            }
        )
    return sorted(rows, key=lambda row: (row["system_id"], row["orders"]))
