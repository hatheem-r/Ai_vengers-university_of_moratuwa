"""Mock mode engine (`MOCK=1`) serving realistic sample data from `project/api/mocks/` (docs/API.md §2).

Features:
- Steps newly started runs smoothly through `run_status_sequence.json` over ~16 seconds
  (`queued -> intake -> planner -> checker (revise) -> planner -> checker (pass) -> awaiting_approval`).
- Adapts steps automatically for `baseline` (`agent -> approval`) and `M-S` (`claude-sonnet-5-5`).
- Supports `approve` (-> `completed`) and `reject` with notes (-> 4s re-plan -> `plan_version + 1`).
- Pre-seeds two completed comparison runs on `A_S3_typical` (`M-mix` and `B`) so the Compare
  and Trace views work immediately out of the box during a live demo.
"""
from __future__ import annotations

import copy
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from .models import (
    ApiError,
    ApprovalState,
    BundleResponse,
    ScenarioItem,
    StartRunRequest,
    derive_system_id,
    now_colombo,
)
from .store import LiveStore

MOCKS_DIR = Path(__file__).resolve().parent / "mocks"


def _load_json(filename: str) -> Any:
    return json.loads((MOCKS_DIR / filename).read_text(encoding="utf-8"))


def _load_jsonl(filename: str) -> list[dict[str, Any]]:
    lines = (MOCKS_DIR / filename).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class MockStore:
    """In-memory mock store backed by `project/api/mocks/`."""

    def __init__(self) -> None:
        self._live_uploads = LiveStore()
        self._runs: dict[str, dict[str, Any]] = {}
        self._seed_demo_runs()

    def _seed_demo_runs(self) -> None:
        base_plan = _load_json("plan.json")
        base_trace = _load_jsonl("trace.jsonl")
        seq_frames = _load_json("run_status_sequence.json")
        final_steps = copy.deepcopy(seq_frames[-1]["steps"])
        for st in final_steps:
            if st["key"] == "approval":
                st["status"] = "done"
                st["ended_at"] = "2026-11-24T09:32:10+05:30"
                st["detail"] = "Approved by production planner"

        # Seeded Multi-Agent M-mix run
        mmix_id = "run_20261124_093000_mmix"
        mmix_plan = copy.deepcopy(base_plan)
        mmix_plan["system_id"] = "M-mix"
        mmix_plan["bundle_id"] = "A_S3_typical"
        self._runs[mmix_id] = {
            "run_id": mmix_id,
            "bundle_id": "A_S3_typical",
            "has_answer_key": True,
            "mode": "multi_agent",
            "model_profile": "mix",
            "system_id": "M-mix",
            "created_ts": time.monotonic() - 120,
            "replan_ts": None,
            "auto_approve": False,
            "status_override": "completed",
            "created_at": "2026-11-24T09:30:00+05:30",
            "started_at": "2026-11-24T09:30:01+05:30",
            "ended_at": "2026-11-24T09:32:10+05:30",
            "plan_version": 2,
            "revisions": {"checker": 1, "human": 0},
            "steps_override": final_steps,
            "metrics_override": {
                "elapsed_s": 71.4,
                "llm_calls": 15,
                "tool_calls": 6,
                "input_tokens": 142300,
                "cached_input_tokens": 38000,
                "output_tokens": 21450,
                "cost_usd": 0.31,
            },
            "plans": {1: mmix_plan, 2: mmix_plan},
            "approval": ApprovalState(
                status="approved",
                decided_at="2026-11-24T09:32:10+05:30",
                notes="Expedited PO to SUP-03 approved; 1-day slip cleared with buyer.",
            ).model_dump(),
            "trace": [{**ev, "run_id": mmix_id} for ev in base_trace],
        }

        # Seeded Single-Agent Baseline B run on same bundle for Compare page
        base_id = "run_20261124_092500_base"
        b_plan = copy.deepcopy(base_plan)
        b_plan["system_id"] = "B"
        b_plan["bundle_id"] = "A_S3_typical"
        b_plan["checker"] = None
        b_plan["work_orders"][0]["planned_start"] = "2026-11-25T08:00:00+05:30"
        b_plan["work_orders"][0]["operations"][0]["start"] = "2026-11-25T08:00:00+05:30"
        b_plan["work_orders"][0]["operations"][0]["end"] = "2026-11-26T12:00:00+05:30"
        b_plan["flags"] = [b_plan["flags"][0]]  # Missed due_date_risk flag
        b_plan["rationale"] = (
            "Scheduled PO-4471 immediately on CUT-01 and raised an expedited PO for the fabric shortfall, "
            "without accounting for the 2-day supplier lead time and receiving lag before cutting starts."
        )

        self._runs[base_id] = {
            "run_id": base_id,
            "bundle_id": "A_S3_typical",
            "has_answer_key": True,
            "mode": "baseline",
            "model_profile": "sonnet",
            "system_id": "B",
            "created_ts": time.monotonic() - 300,
            "replan_ts": None,
            "auto_approve": False,
            "status_override": "completed",
            "created_at": "2026-11-24T09:25:00+05:30",
            "started_at": "2026-11-24T09:25:01+05:30",
            "ended_at": "2026-11-24T09:26:55+05:30",
            "plan_version": 1,
            "revisions": {"checker": 0, "human": 0},
            "steps_override": [
                {
                    "key": "agent",
                    "label": "Single Agent (Sonnet 5.5)",
                    "model": "claude-sonnet-5-5",
                    "status": "done",
                    "started_at": "2026-11-24T09:25:01+05:30",
                    "ended_at": "2026-11-24T09:26:50+05:30",
                    "progress": {"done": 11, "total": 11},
                    "detail": "Read 11 documents, called mrp_tool & schedule_tool, submitted plan v1",
                    "items": [],
                },
                {
                    "key": "approval",
                    "label": "Human approval",
                    "model": None,
                    "status": "done",
                    "started_at": "2026-11-24T09:26:50+05:30",
                    "ended_at": "2026-11-24T09:26:55+05:30",
                    "progress": None,
                    "detail": "Approved for baseline comparison",
                    "items": [],
                },
            ],
            "metrics_override": {
                "elapsed_s": 114.2,
                "llm_calls": 18,
                "tool_calls": 14,
                "input_tokens": 368400,
                "cached_input_tokens": 62000,
                "output_tokens": 24100,
                "cost_usd": 0.84,
            },
            "plans": {1: b_plan},
            "approval": ApprovalState(
                status="approved",
                decided_at="2026-11-24T09:26:55+05:30",
                notes="Baseline run recorded.",
            ).model_dump(),
            "trace": [
                {
                    "run_id": base_id,
                    "seq": 1,
                    "ts": "2026-11-24T09:25:01+05:30",
                    "type": "run_start",
                    "agent": None,
                    "item": None,
                    "model": None,
                    "tool": None,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "latency_ms": None,
                    "cost_usd": None,
                    "from_agent": None,
                    "to_agent": None,
                    "summary": "B on A_S3_typical",
                    "payload": {"bundle_id": "A_S3_typical", "mode": "baseline"},
                    "payload_ref": None,
                },
                {
                    "run_id": base_id,
                    "seq": 2,
                    "ts": "2026-11-24T09:25:02+05:30",
                    "type": "agent_start",
                    "agent": "agent",
                    "item": None,
                    "model": "claude-sonnet-5-5",
                    "tool": None,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "latency_ms": None,
                    "cost_usd": None,
                    "from_agent": None,
                    "to_agent": None,
                    "summary": "Single ReAct agent started with all raw bundle documents in one context",
                    "payload": None,
                    "payload_ref": None,
                },
                {
                    "run_id": base_id,
                    "seq": 3,
                    "ts": "2026-11-24T09:26:12+05:30",
                    "type": "llm_call",
                    "agent": "agent",
                    "item": None,
                    "model": "claude-sonnet-5-5",
                    "tool": None,
                    "input_tokens": 368400,
                    "cached_input_tokens": 62000,
                    "output_tokens": 24100,
                    "latency_ms": 98200,
                    "cost_usd": 0.84,
                    "from_agent": None,
                    "to_agent": None,
                    "summary": "18 sequential ReAct turns reading PDFs/XLSX and calling mrp_tool + schedule_tool",
                    "payload": {"turns": 18, "note": "No independent Checker review"},
                    "payload_ref": None,
                },
                {
                    "run_id": base_id,
                    "seq": 4,
                    "ts": "2026-11-24T09:26:50+05:30",
                    "type": "interrupt",
                    "agent": "approval",
                    "item": None,
                    "model": None,
                    "tool": None,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "latency_ms": None,
                    "cost_usd": None,
                    "from_agent": "agent",
                    "to_agent": "approval",
                    "summary": "Plan v1 awaiting human planner review",
                    "payload": {"plan_version": 1},
                    "payload_ref": None,
                },
            ],
        }

    # --- Bundles & Scenarios ---------------------------------------------------

    def save_upload(self, uploads: list[tuple[str, bytes]], name: Optional[str] = None) -> BundleResponse:
        return self._live_uploads.save_upload(uploads, name=name)

    def get_bundle(self, bundle_id: str) -> BundleResponse:
        if "/" in bundle_id or "\\" in bundle_id or ".." in bundle_id or bundle_id.endswith(".json"):
            raise ApiError(404, "not_found", f"Bundle '{bundle_id}' not found.")

        for sc in self.list_scenarios("all"):
            if sc.bundle_id == bundle_id:
                return BundleResponse(
                    bundle_id=sc.bundle_id,
                    name=f"{sc.profile_name} · {sc.scenario_name}",
                    source="scenario",
                    created_at="2026-11-24T09:00:00+05:30",
                    files=sc.files,
                    has_answer_key=sc.has_answer_key,
                )
        return self._live_uploads.get_bundle(bundle_id)

    def list_scenarios(self, set_filter: str = "test") -> list[ScenarioItem]:
        if set_filter not in ("test", "dev", "scale", "all"):
            raise ApiError(400, "invalid_request", f"Invalid scenario set '{set_filter}'.")
        raw_list = _load_json("scenarios.json")["scenarios"]
        items = [ScenarioItem.model_validate(item) for item in raw_list]
        if set_filter == "all":
            return items
        return [s for s in items if s.set == set_filter]

    # --- Runs ------------------------------------------------------------------

    def start_run(self, req: StartRunRequest) -> dict[str, Any]:
        # Verify bundle exists in scenarios or uploads
        bundle = self.get_bundle(req.bundle_id)
        now = now_colombo()
        run_id = f"run_{now:%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"
        system_id = derive_system_id(req.mode, req.model_profile)

        base_plan = copy.deepcopy(_load_json("plan.json"))
        base_plan["bundle_id"] = req.bundle_id
        base_plan["system_id"] = system_id
        base_plan["created_at"] = (now + timedelta(seconds=16)).isoformat(timespec="seconds")
        if req.mode == "baseline":
            base_plan["checker"] = None

        base_trace = [
            {**ev, "run_id": run_id}
            for ev in _load_jsonl("trace.jsonl")
        ]

        self._runs[run_id] = {
            "run_id": run_id,
            "bundle_id": req.bundle_id,
            "has_answer_key": bundle.has_answer_key,
            "mode": req.mode,
            "model_profile": req.model_profile,
            "system_id": system_id,
            "created_ts": time.monotonic(),
            "replan_ts": None,
            "auto_approve": req.auto_approve,
            "status_override": None,
            "created_at": now.isoformat(timespec="seconds"),
            "started_at": (now + timedelta(seconds=1)).isoformat(timespec="seconds"),
            "ended_at": None,
            "plan_version": 2 if req.mode == "multi_agent" else 1,
            "revisions": {"checker": 1 if req.mode == "multi_agent" else 0, "human": 0},
            "steps_override": None,
            "metrics_override": None,
            "plans": {1: base_plan, 2: base_plan},
            "approval": ApprovalState(status="pending").model_dump(),
            "trace": base_trace,
        }
        return {"run_id": run_id, "status": "queued", "system_id": system_id}

    def _compute_live_frame(self, rec: dict[str, Any]) -> dict[str, Any]:
        budget = {"max_llm_calls": 40, "max_tokens": 1_500_000, "max_seconds": 600}

        if rec["status_override"] in ("completed", "failed"):
            return {
                "run_id": rec["run_id"],
                "bundle_id": rec["bundle_id"],
                "mode": rec["mode"],
                "model_profile": rec["model_profile"],
                "system_id": rec["system_id"],
                "status": rec["status_override"],
                "created_at": rec["created_at"],
                "started_at": rec["started_at"],
                "ended_at": rec["ended_at"],
                "current_step": "approval",
                "plan_version": rec["plan_version"],
                "revisions": rec["revisions"],
                "steps": rec["steps_override"] or _load_json("run_status_sequence.json")[-1]["steps"],
                "metrics": rec["metrics_override"] or _load_json("run_status_sequence.json")[-1]["metrics"],
                "budget": budget,
                "error": None,
            }

        # Re-planning after human rejection (4 seconds)
        if rec["replan_ts"] is not None:
            dt = time.monotonic() - rec["replan_ts"]
            frames = _load_json("run_status_sequence.json")
            last = copy.deepcopy(frames[-1])
            if dt < 4.0:
                for st in last["steps"]:
                    if st["key"] == "planner":
                        st["status"] = "running"
                        st["detail"] = "Re-planning with human rejection constraints..."
                    elif st["key"] == "checker":
                        st["status"] = "pending"
                        st["detail"] = "Waiting for revised plan"
                    elif st["key"] == "approval":
                        st["status"] = "pending"
                return {
                    "run_id": rec["run_id"],
                    "bundle_id": rec["bundle_id"],
                    "mode": rec["mode"],
                    "model_profile": rec["model_profile"],
                    "system_id": rec["system_id"],
                    "status": "running",
                    "created_at": rec["created_at"],
                    "started_at": rec["started_at"],
                    "ended_at": None,
                    "current_step": "planner" if rec["mode"] == "multi_agent" else "agent",
                    "plan_version": rec["plan_version"],
                    "revisions": rec["revisions"],
                    "steps": self._adapt_steps(last["steps"], rec),
                    "metrics": last["metrics"],
                    "budget": budget,
                    "error": None,
                }
            rec["replan_ts"] = None

        elapsed = time.monotonic() - rec["created_ts"]
        frames = _load_json("run_status_sequence.json")
        # Pick frame based on elapsed seconds (0..16s across sequence)
        selected = frames[0]
        for frame in frames:
            if elapsed >= float(frame.get("at_second", 0)):
                selected = frame
        frame = copy.deepcopy(selected)

        status = frame["status"]
        ended_at = None
        if status == "awaiting_approval" and rec.get("auto_approve"):
            status = "completed"
            ended_at = now_colombo().isoformat(timespec="seconds")
            rec["approval"] = ApprovalState(
                status="approved",
                decided_at=ended_at,
                notes="auto_approve (mock)",
            ).model_dump()

        steps = self._adapt_steps(frame["steps"], rec)
        current_step = frame["current_step"]
        if rec["mode"] == "baseline" and current_step in ("intake", "planner", "checker"):
            current_step = "agent"

        return {
            "run_id": rec["run_id"],
            "bundle_id": rec["bundle_id"],
            "mode": rec["mode"],
            "model_profile": rec["model_profile"],
            "system_id": rec["system_id"],
            "status": status,
            "created_at": rec["created_at"],
            "started_at": rec["started_at"] if status != "queued" else None,
            "ended_at": ended_at,
            "current_step": current_step,
            "plan_version": frame["plan_version"] if rec["revisions"]["human"] == 0 else rec["plan_version"],
            "revisions": {
                "checker": frame["revisions"]["checker"] if rec["mode"] == "multi_agent" else 0,
                "human": rec["revisions"]["human"],
            },
            "steps": steps,
            "metrics": frame["metrics"],
            "budget": budget,
            "error": None,
        }

    def _adapt_steps(self, steps: list[dict[str, Any]], rec: dict[str, Any]) -> list[dict[str, Any]]:
        if rec["mode"] == "baseline":
            approval_step = next((s for s in steps if s["key"] == "approval"), None)
            any_running = any(s["status"] == "running" for s in steps if s["key"] != "approval")
            all_done = all(s["status"] == "done" for s in steps if s["key"] != "approval")
            intake = next((s for s in steps if s["key"] == "intake"), {})
            active = next((s for s in steps if s["status"] == "running"), {})
            return [
                {
                    "key": "agent",
                    "label": "Single Agent (Sonnet 5.5)",
                    "model": "claude-sonnet-5-5",
                    "status": "done" if all_done else ("running" if any_running else "pending"),
                    "started_at": rec["started_at"],
                    "ended_at": rec["ended_at"],
                    "progress": intake.get("progress") or {"done": 9, "total": 9},
                    "detail": active.get("detail") or "Completed single-context planning pass",
                    "items": [],
                },
                approval_step
                or {
                    "key": "approval",
                    "label": "Human approval",
                    "model": None,
                    "status": "pending",
                    "started_at": None,
                    "ended_at": None,
                    "progress": None,
                    "detail": None,
                    "items": [],
                },
            ]

        adapted = copy.deepcopy(steps)
        if rec["model_profile"] == "sonnet":
            for st in adapted:
                if st["key"] in ("intake", "planner", "checker"):
                    st["model"] = "claude-sonnet-5-5"
        return adapted

    def get_run(self, run_id: str) -> dict[str, Any]:
        rec = self._runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        return self._compute_live_frame(rec)

    def list_runs(self, bundle_id: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rec in self._runs.values():
            if bundle_id and rec["bundle_id"] != bundle_id:
                continue
            frame = self._compute_live_frame(rec)
            frame.pop("steps", None)
            out.append(frame)
        out.sort(key=lambda r: r["created_at"], reverse=True)
        return out[: max(1, min(limit, 100))]

    def get_plan(self, run_id: str, version: Optional[int] = None) -> dict[str, Any]:
        rec = self._runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        frame = self._compute_live_frame(rec)
        if frame["status"] in ("queued", "running") and int(frame["plan_version"]) == 0:
            raise ApiError(409, "plan_not_ready", f"Run {run_id} is still running.")

        latest_v = int(rec["plan_version"])
        target_v = version if version is not None else latest_v
        plan = rec["plans"].get(target_v)
        if not plan:
            raise ApiError(404, "not_found", f"Plan version {target_v} not found for run {run_id}.")

        return {
            "run_id": run_id,
            "plan_version": target_v,
            "approval": rec["approval"],
            "plan": plan,
        }

    def approve_or_reject(self, run_id: str, decision: str, notes: Optional[str]) -> dict[str, Any]:
        rec = self._runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        frame = self._compute_live_frame(rec)
        if frame["status"] != "awaiting_approval":
            raise ApiError(
                409,
                "not_awaiting_approval",
                f"Run {run_id} is '{frame['status']}', not 'awaiting_approval'.",
            )

        now_iso = now_colombo().isoformat(timespec="seconds")
        if decision == "approve":
            rec["status_override"] = "completed"
            rec["ended_at"] = now_iso
            steps = copy.deepcopy(frame["steps"])
            for st in steps:
                if st["key"] == "approval":
                    st["status"] = "done"
                    st["ended_at"] = now_iso
                    st["detail"] = "Approved by planner"
            rec["steps_override"] = steps
            rec["metrics_override"] = frame["metrics"]
            rec["approval"] = ApprovalState(status="approved", decided_at=now_iso, notes=notes).model_dump()
            rec["trace"].append(
                {
                    "run_id": run_id,
                    "seq": len(rec["trace"]) + 1,
                    "ts": now_iso,
                    "type": "human_decision",
                    "agent": "approval",
                    "item": None,
                    "model": None,
                    "tool": None,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "latency_ms": None,
                    "cost_usd": None,
                    "from_agent": "approval",
                    "to_agent": None,
                    "summary": f"Approved plan v{rec['plan_version']}" + (f" — {notes}" if notes else ""),
                    "payload": {"decision": "approve", "notes": notes},
                    "payload_ref": None,
                }
            )
            return {"run_id": run_id, "status": "completed", "plan_version": rec["plan_version"]}

        # Reject -> produce a revised version incorporating human notes
        new_version = int(rec["plan_version"]) + 1
        rec["plan_version"] = new_version
        rec["revisions"]["human"] = int(rec["revisions"]["human"]) + 1
        rec["replan_ts"] = time.monotonic()
        rec["approval"] = ApprovalState(status="pending", decided_at=now_iso, notes=notes).model_dump()

        revised = copy.deepcopy(rec["plans"][new_version - 1])
        revised["created_at"] = now_iso
        revised["assumptions"] = [
            *revised.get("assumptions", []),
            f"Human constraint applied (v{new_version}): {notes}",
        ]
        revised["rationale"] = (
            f"[Revised v{new_version} per planner note: \"{notes}\"] " + revised.get("rationale", "")
        )
        rec["plans"][new_version] = revised

        rec["trace"].append(
            {
                "run_id": run_id,
                "seq": len(rec["trace"]) + 1,
                "ts": now_iso,
                "type": "human_decision",
                "agent": "approval",
                "item": None,
                "model": None,
                "tool": None,
                "input_tokens": None,
                "cached_input_tokens": None,
                "output_tokens": None,
                "latency_ms": None,
                "cost_usd": None,
                "from_agent": "approval",
                "to_agent": "planner" if rec["mode"] == "multi_agent" else "agent",
                "summary": f"Approval → Planner: rejected with notes ({notes})",
                "payload": {"decision": "reject", "notes": notes, "next_version": new_version},
                "payload_ref": None,
            }
        )
        return {"run_id": run_id, "status": "running", "plan_version": new_version}

    def get_trace_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        rec = self._runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        frame = self._compute_live_frame(rec)
        all_events = rec["trace"]
        if frame["status"] in ("queued", "running") and rec["replan_ts"] is None:
            elapsed = max(0.0, time.monotonic() - rec["created_ts"])
            visible_count = max(1, int((elapsed / 16.0) * len(all_events)))
            visible = all_events[:visible_count]
        else:
            visible = all_events
        return [ev for ev in visible if int(ev.get("seq", 0)) > after_seq]

    def get_trace_jsonl(self, run_id: str) -> str:
        events = self.get_trace_events(run_id, after_seq=0)
        return "\n".join(json.dumps(ev) for ev in events) + ("\n" if events else "")

    def score_run(self, run_id: str) -> dict[str, Any]:
        rec = self._runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        if not rec.get("has_answer_key"):
            raise ApiError(404, "no_answer_key", f"Bundle '{rec['bundle_id']}' has no answer key.")

        score = copy.deepcopy(_load_json("score.json"))
        score["run_id"] = run_id
        score["bundle_id"] = rec["bundle_id"]
        score["plan_version"] = rec["plan_version"]

        if rec["system_id"] == "B":
            score["pqs"] = 71.5
            score["passed"] = False
            score["fail_reasons"] = ["Hard schedule violation: cutting starts before expedited fabric PO arrives."]
            score["metrics"].update(
                {
                    "q1_extraction_f1": 0.88,
                    "q5_schedule_feasibility": 0.75,
                    "q6_due_dates": 0.50,
                    "q7_flag_recall": 0.50,
                    "q8_false_flags": 1,
                    "q8_penalty": -2,
                }
            )
            score["schedule_violations"] = [
                "WO-001 seq 10 (Cutting on CUT-01) starts 2026-11-25 before FAB-PIQ-NVY is usable on 2026-11-27."
            ]
            score["missed_flags"] = [
                {"type": "due_date_risk", "description": "WO-001 finishes 1 working day after customer ex-factory date"},
            ]
        elif rec["system_id"] == "M-S":
            score["pqs"] = 88.9
            score["metrics"]["q1_extraction_f1"] = 0.97

        return score

    def list_evaluations(self) -> list[dict[str, Any]]:
        summary = _load_json("evaluation_summary.json")
        return [
            {
                "eval_id": summary["eval_id"],
                "created_at": summary["created_at"],
                "status": summary["status"],
                "is_mock": True,
                "n_runs": len(summary.get("runs", [])) or 126,
            }
        ]

    def get_evaluation(self, eval_id: str) -> dict[str, Any]:
        summary = _load_json("evaluation_summary.json")
        if eval_id in ("latest", summary["eval_id"]):
            return summary
        raise ApiError(404, "not_found", f"Evaluation '{eval_id}' not found.")
