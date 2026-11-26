"""FastAPI application entry point (`uvicorn project.api.app:app --reload --port 8000`)."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .mock import MockStore
from .models import API_VERSION, ApiError
from .routes import router
from .store import LiveStore


def _env_mock_enabled() -> bool:
    return os.getenv("MOCK", "0").strip().lower() in ("1", "true", "yes", "on")


def create_app(mock: Optional[bool] = None) -> FastAPI:
    is_mock = _env_mock_enabled() if mock is None else mock

    application = FastAPI(
        title="Work Order Planner API",
        description="Multi-agent manufacturing work order planning API (Team Ai vengers, University of Moratuwa).",
        version=API_VERSION,
    )
    application.state.is_mock = is_mock
    application.state.store = MockStore() if is_mock else LiveStore()

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:8501",
            "http://127.0.0.1:8501",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Mock", "Content-Disposition"],
    )

    @application.middleware("http")
    async def attach_mock_header(request: Request, call_next):
        response = await call_next(request)
        if application.state.is_mock:
            response.headers["X-Mock"] = "true"
        return response

    @application.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        headers = {"X-Mock": "true"} if application.state.is_mock else {}
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "Invalid request payload.")
        detail = f"{loc}: {msg}" if loc else msg
        headers = {"X-Mock": "true"} if application.state.is_mock else {}
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_request", "message": detail}},
            headers=headers,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        headers = {"X-Mock": "true"} if application.state.is_mock else {}
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}},
            headers=headers,
        )

    application.include_router(router)
    return application


app = create_app()
