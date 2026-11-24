"""Runner tests with a fake backend (the real graph and baseline plug in later)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from core import models, runner
from core.schemas import ApprovalDecision, RunConfig, RunResult, TraceEvent, WorkOrderPlan

PLAN = WorkOrderPlan.model_validate(json.loads((Path(__file__).parent / "fixtures/api_example_plan.json").read_text()))


@pytest.fixture
def fake_backend(monkeypatch):
    calls: dict = {"start": [], "resume": []}
    mod = types.ModuleType("fake_backend")

    def start(run_id, inputs_dir, config, emit):
        calls["start"].append(inputs_dir)
        emit(TraceEvent(run_id=run_id, seq=1, ts=PLAN.created_at, type="agent_start", agent="planner"))
        return RunResult(run_id=run_id, status="awaiting_approval", plan=PLAN, plan_version=1)

    def resume(run_id, decision, config, emit):
        calls["resume"].append(decision)
        if decision.decision == "approve":
            return RunResult(run_id=run_id, status="completed", plan=PLAN, plan_version=1)
        return RunResult(run_id=run_id, status="awaiting_approval", plan=PLAN, plan_version=2)

    mod.start, mod.resume = start, resume
    monkeypatch.setitem(sys.modules, "fake_backend", mod)
    monkeypatch.setitem(runner.BACKENDS, "multi_agent", "fake_backend")
    return calls


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / "B1" / "inputs").mkdir(parents=True)
    (tmp_path / "B1" / "answer_key.json").write_text("{}")
    return tmp_path / "B1"


def test_start_then_reject_then_approve(fake_backend, bundle):
    events: list[TraceEvent] = []
    rid = runner.new_run_id()
    r = runner.start_run(rid, bundle, RunConfig(mode="multi_agent"), events.append)
    assert r.status == "awaiting_approval" and r.plan_version == 1
    assert fake_backend["start"] == [bundle / "inputs"]  # only inputs/, never the answer key

    r = runner.resume_run(rid, ApprovalDecision(decision="reject", notes="No Sunday overtime."), events.append)
    assert r.plan_version == 2
    r = runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)
    assert r.status == "completed"
    types_ = [e.type for e in events]
    assert types_[0] == "run_start" and types_[-1] == "run_end"
    assert types_.count("human_decision") == 2
    with pytest.raises(KeyError):  # finished runs are forgotten
        runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)


def test_auto_approve(fake_backend, bundle):
    r = runner.start_run("run_auto", bundle, RunConfig(mode="multi_agent", auto_approve=True), lambda e: None)
    assert r.status == "completed"
    assert fake_backend["resume"][0].decision == "approve"


def test_missing_backend_fails_cleanly(bundle, monkeypatch):
    monkeypatch.setitem(runner.BACKENDS, "baseline", "no_such_module_xyz")
    r = runner.start_run("run_nob", bundle, RunConfig(mode="baseline"), lambda e: None)
    assert r.status == "failed" and r.error.code == "internal"


def test_backend_exception_becomes_failed_result(fake_backend, bundle, monkeypatch):
    def boom(*a):
        raise runner.BudgetExceeded("41 LLM calls")
    monkeypatch.setattr(sys.modules["fake_backend"], "start", boom)
    r = runner.start_run("run_boom", bundle, RunConfig(mode="multi_agent"), lambda e: None)
    assert r.status == "failed" and r.error.code == "budget_exceeded"


def test_run_id_format():
    rid = runner.new_run_id()
    assert rid.startswith("run_") and len(rid) == len("run_20261123_093000_a1b2")


def test_model_registry():
    assert models.model_for("planner", "mix").model_id == "claude-sonnet-5-5"
    assert models.model_for("intake", "mix").model_id == "claude-haiku-5-5"
    assert models.model_for("checker", "sonnet").model_id == "claude-sonnet-5-5"
    assert models.model_for("intake", "mix", mode="baseline").model_id == "claude-sonnet-5-5"
    with pytest.raises(ValueError):
        models.model_for("approval")
    with pytest.raises(KeyError):
        models.set_intake_model("not-registered")
