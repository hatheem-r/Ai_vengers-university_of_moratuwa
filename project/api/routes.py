"""HTTP endpoints under `/api/v1` implementing `docs/API.md` §4.1–§4.11."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse

from .models import (
    API_VERSION,
    SCHEMA_VERSION,
    ApprovalRequest,
    ApprovalResponse,
    BundleResponse,
    PlanResponse,
    ScenariosResponse,
    StartRunRequest,
    StartRunResponse,
)

router = APIRouter(prefix="/api/v1")


def _backend(request: Request) -> Any:
    return request.app.state.store


@router.get("/health")
async def get_health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "mock": bool(request.app.state.is_mock),
        "api_version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


@router.post("/bundles", response_model=BundleResponse, status_code=201)
async def upload_bundle(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    name: Optional[str] = Form(default=None),
) -> BundleResponse:
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        payloads.append((f.filename or "", content))
    return _backend(request).save_upload(payloads, name=name)


@router.get("/bundles/{bundle_id}", response_model=BundleResponse)
async def get_bundle(bundle_id: str, request: Request) -> BundleResponse:
    return _backend(request).get_bundle(bundle_id)


@router.get("/scenarios", response_model=ScenariosResponse)
async def list_scenarios(
    request: Request,
    set: str = Query(default="test", description="test | dev | scale | all"),
) -> ScenariosResponse:
    scenarios = _backend(request).list_scenarios(set_filter=set)
    return ScenariosResponse(scenarios=scenarios)


@router.post("/runs", response_model=StartRunResponse, status_code=202)
async def start_run(body: StartRunRequest, request: Request) -> StartRunResponse:
    res = _backend(request).start_run(body)
    return StartRunResponse.model_validate(res)


@router.get("/runs")
async def list_runs(
    request: Request,
    bundle_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {"runs": _backend(request).list_runs(bundle_id=bundle_id, limit=limit)}


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str, request: Request) -> dict[str, Any]:
    return _backend(request).get_run(run_id)


@router.get("/runs/{run_id}/plan", response_model=PlanResponse)
async def get_run_plan(
    run_id: str,
    request: Request,
    version: Optional[int] = Query(default=None, ge=1),
) -> PlanResponse:
    res = _backend(request).get_plan(run_id, version=version)
    return PlanResponse.model_validate(res)


@router.post("/runs/{run_id}/approval", response_model=ApprovalResponse, status_code=202)
async def submit_approval(
    run_id: str,
    body: ApprovalRequest,
    request: Request,
) -> ApprovalResponse:
    res = _backend(request).approve_or_reject(run_id, decision=body.decision, notes=body.notes)
    return ApprovalResponse.model_validate(res)


@router.get("/runs/{run_id}/trace", response_model=None)
async def get_run_trace(
    run_id: str,
    request: Request,
    format: str = Query(default="json", pattern="^(json|jsonl)$"),
    after_seq: int = Query(default=0, ge=0),
) -> Response | dict[str, Any]:
    if format == "jsonl":
        raw_jsonl = _backend(request).get_trace_jsonl(run_id)
        return PlainTextResponse(
            content=raw_jsonl,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{run_id}_trace.jsonl"'},
        )
    events = _backend(request).get_trace_events(run_id, after_seq=after_seq)
    return {"run_id": run_id, "events": events}


@router.get("/runs/{run_id}/score")
async def get_run_score(run_id: str, request: Request) -> dict[str, Any]:
    return _backend(request).score_run(run_id)


@router.get("/evaluations")
async def list_evaluations(request: Request) -> dict[str, Any]:
    return {"evaluations": _backend(request).list_evaluations()}


@router.get("/evaluations/{eval_id}")
async def get_evaluation(eval_id: str, request: Request) -> dict[str, Any]:
    return _backend(request).get_evaluation(eval_id)
