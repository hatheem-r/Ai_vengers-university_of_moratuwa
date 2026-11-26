"""HTTP request/response contracts and error envelope (docs/API.md)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

API_VERSION = "0.1"
SCHEMA_VERSION = "1.0"
FACTORY_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")

RunMode = Literal["multi_agent", "baseline"]
ModelProfile = Literal["mix", "sonnet"]
SystemId = Literal["B", "M-S", "M-mix", "human"]
RunStatusLiteral = Literal["queued", "running", "awaiting_approval", "completed", "failed"]
StepStatusLiteral = Literal["pending", "running", "done", "failed", "skipped"]


def now_colombo() -> datetime:
    return datetime.now(FACTORY_TZ)


def derive_system_id(mode: RunMode, model_profile: ModelProfile) -> SystemId:
    if mode == "baseline":
        return "B"
    return "M-S" if model_profile == "sonnet" else "M-mix"


class ApiError(Exception):
    """Structured API error matching docs/API.md §1."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class BundleFileInfo(BaseModel):
    name: str
    type: str
    size_bytes: int
    pages: Optional[int] = None


class BundleResponse(BaseModel):
    bundle_id: str
    name: str
    source: Literal["upload", "scenario"] = "upload"
    created_at: str
    files: list[BundleFileInfo]
    has_answer_key: bool = False


class ScenarioItem(BaseModel):
    bundle_id: str
    set: str
    profile: str
    profile_name: str
    scenario: str
    scenario_name: str
    mess_level: Literal["clean", "typical", "messy"]
    n_documents: int
    n_orders: int
    has_answer_key: bool = True
    files: list[BundleFileInfo] = Field(default_factory=list)


class ScenariosResponse(BaseModel):
    scenarios: list[ScenarioItem]


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str = Field(min_length=1)
    mode: RunMode
    model_profile: ModelProfile = "mix"
    auto_approve: bool = False
    seed: Optional[int] = 0


class StartRunResponse(BaseModel):
    run_id: str
    status: RunStatusLiteral
    system_id: SystemId


class StepProgress(BaseModel):
    done: int
    total: int


class StepItem(BaseModel):
    name: str
    status: StepStatusLiteral


class RunStep(BaseModel):
    key: str
    label: str
    model: Optional[str] = None
    status: StepStatusLiteral
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    progress: Optional[StepProgress] = None
    detail: Optional[str] = None
    items: list[StepItem] = Field(default_factory=list)


class RunRevisions(BaseModel):
    checker: int = 0
    human: int = 0


class RunMetrics(BaseModel):
    elapsed_s: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class RunBudget(BaseModel):
    max_llm_calls: int = 40
    max_tokens: int = 1_500_000
    max_seconds: int = 600


class RunErrorBody(BaseModel):
    code: str
    message: str


class ApprovalState(BaseModel):
    status: Literal["pending", "approved", "rejected"] = "pending"
    decided_at: Optional[str] = None
    notes: Optional[str] = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_notes(self) -> "ApprovalRequest":
        cleaned = self.notes.strip() if self.notes else None
        self.notes = cleaned
        if self.decision == "reject" and not cleaned:
            raise ValueError("notes are required when decision is 'reject'")
        return self


class ApprovalResponse(BaseModel):
    run_id: str
    status: RunStatusLiteral
    plan_version: int


class PlanResponse(BaseModel):
    run_id: str
    plan_version: int
    approval: ApprovalState
    plan: dict[str, Any]
