"""Restartable benchmark orchestration without importing unfinished runner code.

``execute_benchmark`` accepts an injected adapter with this temporary shape::

    runner(run_id, bundle_dir, config) -> {
        "status": "completed" | "failed",
        "trace_events": [...],
        "plan": ...,
        "error": ...,
    }

The adapter can call Hatheem's future ``start_run``. It receives the bundle
directory because that is the agreed runner contract; the runner remains
responsible for exposing only ``inputs/`` to agents. This module never reads or
passes answer keys to the runner. A separate evaluator callback may score the
completed result after execution.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class Budget:
    max_llm_calls: int = 40
    max_tokens: int = 1_500_000
    max_seconds: float = 600.0


@dataclass(frozen=True)
class SystemSpec:
    system_id: str
    mode: str
    model_profile: str
    repeats: int


SYSTEMS = (
    SystemSpec("B", "baseline", "sonnet", 3),
    SystemSpec("M-S", "multi_agent", "sonnet", 3),
    SystemSpec("M-mix", "multi_agent", "mix", 1),
)


def planned_runs(bundle_dirs: Sequence[Path], *, base_seed: int = 0, systems: Sequence[SystemSpec] = SYSTEMS) -> list[dict[str, Any]]:
    if not bundle_dirs:
        raise ValueError("At least one bundle is required.")
    if len({path.name for path in bundle_dirs}) != len(bundle_dirs):
        raise ValueError("Bundle directory names must be unique.")
    plans: list[dict[str, Any]] = []
    for bundle in sorted(bundle_dirs, key=lambda path: path.name):
        if bundle.name.startswith("dev_"):
            dataset = "development"
        else:
            dataset = "test"
        for system in systems:
            if system.repeats < 1:
                raise ValueError("System repeats must be positive.")
            for repeat in range(1, system.repeats + 1):
                seed = base_seed + repeat - 1
                run_id = f"{bundle.name}__{system.system_id}__r{repeat}__s{seed}"
                plans.append(
                    {
                        "run_id": run_id,
                        "bundle_id": bundle.name,
                        "bundle_dir": bundle,
                        "dataset": dataset,
                        "system_id": system.system_id,
                        "mode": system.mode,
                        "model_profile": system.model_profile,
                        "repeat": repeat,
                        "seed": seed,
                    }
                )
    return plans


def summarize_trace(events: object) -> dict[str, int | float]:
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ValueError("trace_events must be a sequence.")
    llm_calls = input_tokens = output_tokens = 0
    cost_usd = latency_ms = 0.0
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"trace_events[{index}] must be a mapping.")
        numeric = {}
        for field in ("input_tokens", "output_tokens", "cost_usd", "latency_ms"):
            value = event.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"trace_events[{index}].{field} must be nonnegative.")
            numeric[field] = value
        if event.get("type") in {"llm_call", "model_call"} or numeric["input_tokens"] or numeric["output_tokens"]:
            llm_calls += 1
        input_tokens += int(numeric["input_tokens"])
        output_tokens += int(numeric["output_tokens"])
        cost_usd += float(numeric["cost_usd"])
        latency_ms += float(numeric["latency_ms"])
    return {
        "llm_calls": llm_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
        "latency_s": latency_ms / 1000,
    }


def _load_record(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid benchmark record: {path}")
    return value


def _save_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute_benchmark(
    bundle_dirs: Sequence[Path],
    output_dir: Path,
    runner: Callable[[str, Path, Mapping[str, Any]], Mapping[str, Any]],
    *,
    evaluator: Callable[[Mapping[str, Any], Path], Mapping[str, Any]] | None = None,
    budget: Budget = Budget(),
    base_seed: int = 0,
    systems: Sequence[SystemSpec] = SYSTEMS,
    dry_run_only: bool = False,
) -> list[dict[str, Any]]:
    """Execute or resume benchmark plans and return one current row per run."""
    plans = planned_runs(bundle_dirs, base_seed=base_seed, systems=systems)
    if dry_run_only and any(plan["dataset"] != "development" for plan in plans):
        raise ValueError("dry_run_only accepts only dev_ bundles.")
    rows: list[dict[str, Any]] = []
    for plan in plans:
        record_path = output_dir / "runs" / f"{plan['run_id']}.json"
        record = _load_record(record_path) or {
            key: value for key, value in plan.items() if key != "bundle_dir"
        } | {"attempts": []}
        if record.get("status") == "completed":
            rows.append(record)
            continue

        config = {
            "mode": plan["mode"],
            "model_profile": plan["model_profile"],
            "auto_approve": True,
            "budgets": asdict(budget),
            "seed": plan["seed"],
        }
        started = perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = runner(plan["run_id"], plan["bundle_dir"], config)
            if not isinstance(result, Mapping):
                raise ValueError("Runner adapter must return a mapping.")
            trace = summarize_trace(result.get("trace_events"))
            elapsed = perf_counter() - started
            budget_failures = []
            if trace["llm_calls"] > budget.max_llm_calls:
                budget_failures.append("max_llm_calls")
            if trace["tokens"] > budget.max_tokens:
                budget_failures.append("max_tokens")
            if elapsed > budget.max_seconds:
                budget_failures.append("max_seconds")
            status = result.get("status")
            if status not in {"completed", "failed"}:
                raise ValueError("Runner status must be completed or failed.")
            error = result.get("error")
            if budget_failures:
                status = "failed"
                error = {"code": "budget_exceeded", "limits": budget_failures}
            score = None
            if status == "completed" and evaluator is not None:
                score = dict(evaluator(result, plan["bundle_dir"]))
            attempt = {
                "attempt": len(record["attempts"]) + 1,
                "started_at": started_at,
                "status": status,
                "error": error,
                "wall_time_s": elapsed,
                "trace": trace,
                "score": score,
            }
        except Exception as exc:
            attempt = {
                "attempt": len(record["attempts"]) + 1,
                "started_at": started_at,
                "status": "failed",
                "error": {"code": "benchmark_adapter_error", "message": f"{type(exc).__name__}: {exc}"},
                "wall_time_s": perf_counter() - started,
                "trace": None,
                "score": None,
            }
        record["attempts"].append(attempt)
        record.update({"status": attempt["status"], "error": attempt["error"], "trace": attempt["trace"], "score": attempt["score"]})
        _save_record(record_path, record)
        rows.append(record)
    return rows
