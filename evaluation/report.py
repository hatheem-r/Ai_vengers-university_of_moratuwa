"""Generate API-shaped benchmark reports from measured or explicitly mock rows."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any


REQUIRED_FIELDS = {"bundle_id", "system_id", "scenario", "mess_level", "success", "passed", "pqs", "latency_s", "tokens", "cost_usd", "metrics"}


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError("At least one measured row is required.")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"rows[{index}] must be a mapping.")
        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            raise ValueError(f"rows[{index}] is missing: {', '.join(missing)}.")
        if not isinstance(row["success"], bool) or not isinstance(row["passed"], bool):
            raise ValueError(f"rows[{index}] success and passed must be booleans.")
        if not isinstance(row["metrics"], Mapping):
            raise ValueError(f"rows[{index}].metrics must be a mapping.")


def _numbers(rows: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(field)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} values must be numeric or null.")
            values.append(float(value))
    return values


def _metric_means(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, value in row["metrics"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key].append(float(value))
    return {key: mean(items) for key, items in sorted(values.items()) if items}


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pqs = _numbers(rows, "pqs")
    latency = _numbers(rows, "latency_s")
    tokens = _numbers(rows, "tokens")
    cost = _numbers(rows, "cost_usd")
    return {
        "n_runs": len(rows),
        "pqs_mean": mean(pqs) if pqs else None,
        "pqs_std": pstdev(pqs) if pqs else None,
        "pass_rate": sum(row["passed"] for row in rows) / len(rows),
        "run_success_rate": sum(row["success"] for row in rows) / len(rows),
        "latency_median_s": median(latency) if latency else None,
        "latency_max_s": max(latency) if latency else None,
        "tokens_mean": mean(tokens) if tokens else None,
        "cost_mean_usd": mean(cost) if cost else None,
        "metrics": _metric_means(rows),
        "failed_runs": sum(not row["success"] for row in rows),
    }


def _grouped(rows: Sequence[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["system_id"]), str(row[field]))].append(row)
    result = []
    for (system_id, value), group in sorted(groups.items()):
        entry = {"system_id": system_id, field: value, **_summary(group)}
        result.append(entry)
    return result


def _wilcoxon(differences: list[float]) -> dict[str, Any]:
    nonzero = [difference for difference in differences if difference != 0]
    if len(nonzero) < 5:
        return {"statistic": None, "p_value": None, "status": "not_run_fewer_than_5_nonzero_pairs"}
    try:
        from scipy.stats import wilcoxon

        result = wilcoxon(nonzero)
        return {"statistic": float(result.statistic), "p_value": float(result.pvalue), "status": "completed"}
    except ImportError:
        return {"statistic": None, "p_value": None, "status": "scipy_unavailable"}


def paired_comparison(rows: Sequence[Mapping[str, Any]], a: str = "M-S", b: str = "B") -> dict[str, Any]:
    scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row["system_id"] in {a, b} and row["success"] and row["pqs"] is not None:
            scores[(str(row["bundle_id"]), str(row["system_id"]))].append(float(row["pqs"]))
    bundles = sorted({bundle for bundle, system in scores if (bundle, a) in scores and (bundle, b) in scores})
    differences = [mean(scores[(bundle, a)]) - mean(scores[(bundle, b)]) for bundle in bundles]
    wins = sum(difference > 2 for difference in differences)
    losses = sum(difference < -2 for difference in differences)
    ties = len(differences) - wins - losses
    return {
        "a": a,
        "b": b,
        "n_bundles": len(bundles),
        "mean_pqs_diff": mean(differences) if differences else None,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "tie_threshold_points": 2,
        "wilcoxon": _wilcoxon(differences),
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    metric_keys = sorted({key for row in rows for key in row["metrics"]})
    base = sorted(set().union(*(set(row) for row in rows)) - {"metrics"})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base + metric_keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{key: row.get(key) for key in base}, **{key: row["metrics"].get(key) for key in metric_keys}})


def _svg_bar_chart(path: Path, title: str, labels: list[str], values: list[float], *, unit: str = "") -> None:
    width, height = 720, 420
    maximum = max(values) if values else 1
    maximum = maximum or 1
    bars = []
    for index, (label, value) in enumerate(zip(labels, values)):
        x = 90 + index * (540 / max(len(labels), 1))
        bar_width = min(90, 420 / max(len(labels), 1))
        bar_height = 250 * value / maximum
        y = 330 - bar_height
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#356A9A"/><text x="{x + bar_width / 2:.1f}" y="350" text-anchor="middle" font-size="12">{label}</text><text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="12">{value:.2f}{unit}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><text x="40" y="35" font-family="Arial" font-size="20" font-weight="bold" fill="#17365D">{title}</text><line x1="70" y1="330" x2="680" y2="330" stroke="#8899AA"/>{"".join(bars)}</svg>'
    path.write_text(svg, encoding="utf-8")


def _human_section(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"participants": 0, "bundles": 0, "hourly_rate_lkr": None, "rate_source": None, "results": [], "summary": None}
    pqs = _numbers(rows, "pqs")
    minutes = _numbers(rows, "minutes")
    rates = {row.get("hourly_rate_lkr") for row in rows if row.get("hourly_rate_lkr") is not None}
    sources = {row.get("rate_source") for row in rows if row.get("rate_source")}
    return {
        "participants": len({row.get("participant") for row in rows}),
        "bundles": len({row.get("bundle_id") for row in rows}),
        "hourly_rate_lkr": next(iter(rates)) if len(rates) == 1 else None,
        "rate_source": next(iter(sources)) if len(sources) == 1 else None,
        "results": [dict(row) for row in rows],
        "summary": {"pqs_mean": mean(pqs) if pqs else None, "minutes_mean": mean(minutes) if minutes else None},
    }


def _scalability_section(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        order_count = row.get("n_orders", row.get("orders"))
        if order_count not in {1, 3, 5}:
            raise ValueError("Scalability rows require n_orders or orders equal to 1, 3 or 5.")
        groups[(str(row["system_id"]), int(order_count))].append(row)
    result = []
    for (system_id, order_count), group in sorted(groups.items()):
        latency = _numbers(group, "latency_s")
        tokens = _numbers(group, "tokens")
        cost = _numbers(group, "cost_usd")
        pqs = _numbers(group, "pqs")
        result.append(
            {
                "system_id": system_id,
                "n_orders": order_count,
                "n_runs": len(group),
                "latency_median_s": median(latency) if latency else None,
                "tokens_mean": mean(tokens) if tokens else None,
                "cost_mean_usd": mean(cost) if cost else None,
                "pqs_mean": mean(pqs) if pqs else None,
            }
        )
    return result


def generate_report(
    eval_id: str,
    rows: Sequence[Mapping[str, Any]],
    output_root: Path,
    *,
    config: Mapping[str, Any],
    system_metadata: Sequence[Mapping[str, Any]],
    scalability: Sequence[Mapping[str, Any]] = (),
    human: Sequence[Mapping[str, Any]] = (),
    data_label: str = "measured",
    status: str = "complete",
    limitations: Sequence[str] = (),
) -> Path:
    """Write summary.json, runs.csv and two SVG charts; return result directory."""
    if data_label not in {"measured", "mock"}:
        raise ValueError("data_label must be measured or mock.")
    if not eval_id or "/" in eval_id or ".." in eval_id:
        raise ValueError("eval_id must be a safe non-empty directory name.")
    if status not in {"running", "complete", "failed"}:
        raise ValueError("status must be running, complete or failed.")
    if not isinstance(config, Mapping):
        raise ValueError("config must be supplied from the executed benchmark.")
    _validate_rows(rows)
    result_dir = output_root / eval_id
    result_dir.mkdir(parents=True, exist_ok=True)
    overall = []
    for system_id in sorted({str(row["system_id"]) for row in rows}):
        overall.append({"system_id": system_id, **_summary([row for row in rows if row["system_id"] == system_id])})
    systems = [dict(item) for item in system_metadata]
    if {item.get("system_id") for item in systems} != {item["system_id"] for item in overall}:
        raise ValueError("system_metadata must describe every and only reported system_id.")
    comparison = paired_comparison(rows)
    compact_runs = [
        {
            key: row.get(key)
            for key in ("run_id", "bundle_id", "system_id", "repeat", "pqs", "passed", "success", "latency_s", "tokens", "cost_usd")
        }
        for row in rows
    ]
    summary = {
        "eval_id": eval_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "is_mock": data_label == "mock",
        "config": dict(config),
        "systems": systems,
        "overall": overall,
        "by_scenario": _grouped(rows, "scenario"),
        "by_mess_level": _grouped(rows, "mess_level"),
        "paired": [{**{key: value for key, value in comparison.items() if key != "wilcoxon"}, "wilcoxon_p": comparison["wilcoxon"]["p_value"], "wilcoxon_status": comparison["wilcoxon"]["status"]}],
        "scalability": _scalability_section(scalability),
        "human": _human_section(human),
        "runs": compact_runs,
        "limitations": list(limitations),
        "failed_runs": [dict(row) for row in rows if not row["success"]],
    }
    (result_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(result_dir / "runs.csv", rows)
    labels = [system["system_id"] for system in overall]
    _svg_bar_chart(result_dir / "pqs_by_system.svg", "Mean Plan Quality Score", labels, [system["pqs_mean"] or 0 for system in overall])
    _svg_bar_chart(result_dir / "cost_by_system.svg", "Mean API Cost", labels, [system["cost_mean_usd"] or 0 for system in overall], unit=" USD")
    return result_dir
