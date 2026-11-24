"""Mock-mode contract tests for `project/api` (docs/API.md)."""
from __future__ import annotations

import io
from fastapi.testclient import TestClient

from project.api.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(mock=True))


def test_health_and_mock_header() -> None:
    client = _client()
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.headers.get("X-Mock") == "true"
    body = res.json()
    assert body["status"] == "ok"
    assert body["mock"] is True
    assert body["api_version"] == "0.1"
    assert body["schema_version"] == "1.0"


def test_scenarios_and_hidden_files_never_exposed() -> None:
    client = _client()
    res = client.get("/api/v1/scenarios?set=test")
    assert res.status_code == 200
    scenarios = res.json()["scenarios"]
    assert len(scenarios) > 0
    for sc in scenarios:
        assert "answer_key" not in sc
        assert "planted_conflicts" not in sc
        assert "defects" not in sc

    # Attempting to fetch hidden filenames via bundle endpoint must 404
    for forbidden in ("state.json", "manifest.json", "answer_key.json"):
        r = client.get(f"/api/v1/bundles/{forbidden}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_bundle_upload_and_forbidden_filename_guard() -> None:
    client = _client()
    res = client.post(
        "/api/v1/bundles",
        data={"name": "Test Polo Bundle"},
        files=[("files", ("bom_test.csv", io.BytesIO(b"parent,component,qty\nA,B,2\n"), "text/csv"))],
    )
    assert res.status_code == 201
    bundle = res.json()
    assert bundle["bundle_id"].startswith("up_")
    assert bundle["has_answer_key"] is False

    # Forbidden answer key upload rejected
    bad = client.post(
        "/api/v1/bundles",
        files=[("files", ("answer_key.json", io.BytesIO(b"{}"), "application/json"))],
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_request"


def test_seeded_runs_plan_trace_score_and_approval_flow() -> None:
    client = _client()
    runs_res = client.get("/api/v1/runs?bundle_id=A_S3_typical")
    assert runs_res.status_code == 200
    runs = runs_res.json()["runs"]
    assert len(runs) >= 2

    run_id = "run_20261124_093000_mmix"
    plan_res = client.get(f"/api/v1/runs/{run_id}/plan")
    assert plan_res.status_code == 200
    assert plan_res.json()["plan"]["schema_version"] == "1.0"

    trace_json = client.get(f"/api/v1/runs/{run_id}/trace?format=json")
    assert trace_json.status_code == 200
    assert len(trace_json.json()["events"]) > 5

    trace_jsonl = client.get(f"/api/v1/runs/{run_id}/trace?format=jsonl")
    assert trace_jsonl.status_code == 200
    assert "application/x-ndjson" in trace_jsonl.headers["content-type"]

    score_res = client.get(f"/api/v1/runs/{run_id}/score")
    assert score_res.status_code == 200
    assert "pqs" in score_res.json()

    # Start a new auto-approved run
    start_res = client.post(
        "/api/v1/runs",
        json={"bundle_id": "A_S3_typical", "mode": "multi_agent", "model_profile": "mix", "auto_approve": False},
    )
    assert start_res.status_code == 202
    new_run_id = start_res.json()["run_id"]
    status_res = client.get(f"/api/v1/runs/{new_run_id}")
    assert status_res.status_code == 200
    assert status_res.json()["system_id"] == "M-mix"


def test_evaluations_endpoint() -> None:
    client = _client()
    list_res = client.get("/api/v1/evaluations")
    assert list_res.status_code == 200
    assert list_res.json()["evaluations"][0]["is_mock"] is True

    latest = client.get("/api/v1/evaluations/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["is_mock"] is True
    assert len(body["overall"]) == 3
    assert len(body["by_scenario"]) > 0
