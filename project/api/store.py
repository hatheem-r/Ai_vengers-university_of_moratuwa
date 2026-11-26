"""Real-mode (MOCK=0) filesystem store and runner.py bridge (docs/API.md §4 & §7).

Security rule (TASKS.md §0.3 & API.md §4.2):
`state.json`, `manifest.json`, `answer_key.json`, and `extraction_key.json` are NEVER
exposed through the API. Only safe display fields from `manifest.json` are read to
populate `GET /scenarios`.
"""
from __future__ import annotations

import importlib
import io
import json
import secrets
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Optional

from .models import (
    ApiError,
    ApprovalState,
    BundleFileInfo,
    BundleResponse,
    RunBudget,
    RunMetrics,
    RunRevisions,
    RunStep,
    ScenarioItem,
    StartRunRequest,
    StepItem,
    StepProgress,
    derive_system_id,
    now_colombo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(__file__).resolve().parents[1]

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
FORBIDDEN_FILENAMES = {"state.json", "manifest.json", "answer_key.json", "extraction_key.json"}
MAX_FILES = 25
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

PROFILE_NAMES = {
    "A": "Apparel export (cut-make-trim)",
    "B": "Light assembly (steel furniture)",
    "C": "Food packing (tea packer)",
}
SCENARIO_NAMES = {
    "S1": "Normal order",
    "S2": "Rush order",
    "S3": "Material shortage",
    "S4": "Machine breakdown",
    "S5": "Supplier delay",
    "S6": "Conflicting documents",
    "S7": "Capacity overload",
    "S8": "Combined shock",
    "S9": "Compliance breach",
}


def _ensure_project_on_path() -> None:
    for candidate in (str(PROJECT_DIR), str(REPO_ROOT)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def _bundles_root() -> Optional[Path]:
    for candidate in (PROJECT_DIR / "data" / "bundles", REPO_ROOT / "data" / "bundles"):
        if candidate.is_dir():
            return candidate
    return None


def _results_root() -> Path:
    for candidate in (REPO_ROOT / "results", PROJECT_DIR / "results"):
        if candidate.is_dir():
            return candidate
    return REPO_ROOT / "results"


def _runs_root() -> Path:
    root = REPO_ROOT / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _pdf_page_count(raw: bytes) -> Optional[int]:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def _file_info(path: Path) -> BundleFileInfo:
    ext = path.suffix.lower().lstrip(".")
    pages: Optional[int] = None
    if ext == "pdf":
        try:
            pages = _pdf_page_count(path.read_bytes())
        except Exception:
            pages = None
    return BundleFileInfo(name=path.name, type=ext or "file", size_bytes=path.stat().st_size, pages=pages)


def _infer_set(bundle_id: str, manifest: dict[str, Any]) -> str:
    if "set" in manifest and manifest["set"] in ("test", "dev", "scale"):
        return str(manifest["set"])
    if bundle_id.startswith("dev_"):
        return "dev"
    if bundle_id.startswith("scale_"):
        return "scale"
    return "test"


class LiveStore:
    """Manages uploaded bundles, synthetic scenario discovery, and runner.py execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # --- Bundles & Scenarios ---------------------------------------------------

    def save_upload(self, uploads: list[tuple[str, bytes]], name: Optional[str] = None) -> BundleResponse:
        if not uploads:
            raise ApiError(400, "invalid_request", "No files were uploaded.")

        extracted: list[tuple[str, bytes]] = []
        if len(uploads) == 1 and uploads[0][0].lower().endswith(".zip"):
            zip_name, zip_bytes = uploads[0]
            if len(zip_bytes) > MAX_TOTAL_BYTES:
                raise ApiError(413, "too_large", "Uploaded archive exceeds 100 MB limit.")
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        fname = Path(info.filename).name
                        if not fname or fname.startswith(".") or "__MACOSX" in info.filename:
                            continue
                        extracted.append((fname, zf.read(info)))
            except zipfile.BadZipFile as exc:
                raise ApiError(400, "invalid_request", f"Invalid zip archive {zip_name}: {exc}") from exc
        else:
            extracted = [(Path(fname).name, data) for fname, data in uploads if fname]

        if not extracted:
            raise ApiError(400, "invalid_request", "Upload contained no valid document files.")
        if len(extracted) > MAX_FILES:
            raise ApiError(413, "too_large", f"Too many files ({len(extracted)}); maximum is {MAX_FILES}.")

        total_bytes = 0
        for fname, raw in extracted:
            if fname.lower() in FORBIDDEN_FILENAMES:
                raise ApiError(400, "invalid_request", f"Reserved file name is not allowed: {fname}")
            ext = Path(fname).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise ApiError(
                    400,
                    "invalid_request",
                    f"Unsupported file extension '{ext}' on '{fname}'.",
                )
            if len(raw) > MAX_FILE_BYTES:
                raise ApiError(413, "too_large", f"File '{fname}' exceeds the 20 MB per-file limit.")
            total_bytes += len(raw)

        if total_bytes > MAX_TOTAL_BYTES:
            raise ApiError(413, "too_large", "Total upload size exceeds 100 MB limit.")

        bundle_id = f"up_{secrets.token_hex(3)}"
        bundle_dir = _runs_root() / "uploads" / bundle_id
        inputs_dir = bundle_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        files_meta: list[BundleFileInfo] = []
        for fname, raw in extracted:
            dest = inputs_dir / fname
            dest.write_bytes(raw)
            ext = dest.suffix.lower().lstrip(".")
            pages = _pdf_page_count(raw) if ext == "pdf" else None
            files_meta.append(BundleFileInfo(name=fname, type=ext, size_bytes=len(raw), pages=pages))

        created_at = now_colombo().isoformat(timespec="seconds")
        bundle_obj = BundleResponse(
            bundle_id=bundle_id,
            name=(name or "").strip() or f"Upload {bundle_id}",
            source="upload",
            created_at=created_at,
            files=files_meta,
            has_answer_key=False,
        )
        (bundle_dir / "bundle_meta.json").write_text(
            json.dumps(bundle_obj.model_dump(), indent=2), encoding="utf-8"
        )
        return bundle_obj

    def get_bundle(self, bundle_id: str) -> BundleResponse:
        if "/" in bundle_id or "\\" in bundle_id or ".." in bundle_id or bundle_id.endswith(".json"):
            raise ApiError(404, "not_found", f"Bundle '{bundle_id}' not found.")

        upload_meta = _runs_root() / "uploads" / bundle_id / "bundle_meta.json"
        if upload_meta.is_file():
            data = json.loads(upload_meta.read_text(encoding="utf-8"))
            return BundleResponse.model_validate(data)

        for sc in self.list_scenarios("all"):
            if sc.bundle_id == bundle_id:
                return BundleResponse(
                    bundle_id=sc.bundle_id,
                    name=f"{sc.profile_name} · {sc.scenario_name}",
                    source="scenario",
                    created_at=now_colombo().isoformat(timespec="seconds"),
                    files=sc.files,
                    has_answer_key=sc.has_answer_key,
                )

        raise ApiError(404, "not_found", f"Bundle '{bundle_id}' not found.")

    def list_scenarios(self, set_filter: str = "test") -> list[ScenarioItem]:
        if set_filter not in ("test", "dev", "scale", "all"):
            raise ApiError(400, "invalid_request", f"Invalid scenario set '{set_filter}'.")

        root = _bundles_root()
        if root is None:
            return []

        items: list[ScenarioItem] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            inputs_dir = entry / "inputs"
            if not inputs_dir.is_dir():
                continue

            manifest: dict[str, Any] = {}
            manifest_path = entry / "manifest.json"
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}

            bundle_id = entry.name
            b_set = _infer_set(bundle_id, manifest)
            if set_filter != "all" and b_set != set_filter:
                continue

            parts = bundle_id.replace("dev_", "").replace("scale_", "").split("_")
            profile = str(manifest.get("profile") or (parts[0] if parts else "A"))
            scenario = str(manifest.get("scenario") or (parts[1] if len(parts) > 1 else "S1"))
            mess_raw = str(manifest.get("mess_level") or (parts[2] if len(parts) > 2 else "typical"))
            mess_level = mess_raw if mess_raw in ("clean", "typical", "messy") else "typical"

            input_files = [
                _file_info(f)
                for f in sorted(inputs_dir.iterdir())
                if f.is_file() and not f.name.startswith(".") and f.name.lower() not in FORBIDDEN_FILENAMES
            ]
            n_orders = int(
                manifest.get("n_orders")
                or sum(1 for f in input_files if f.name.startswith("customer_po_"))
                or 1
            )
            has_answer_key = (entry / "answer_key.json").is_file()

            items.append(
                ScenarioItem(
                    bundle_id=bundle_id,
                    set=b_set,
                    profile=profile,
                    profile_name=str(manifest.get("profile_name") or PROFILE_NAMES.get(profile, profile)),
                    scenario=scenario,
                    scenario_name=str(manifest.get("scenario_name") or SCENARIO_NAMES.get(scenario, scenario)),
                    mess_level=mess_level,  # type: ignore[arg-type]
                    n_documents=len(input_files),
                    n_orders=n_orders,
                    has_answer_key=has_answer_key,
                    files=input_files,
                )
            )
        return items

    def _resolve_bundle_dir(self, bundle_id: str) -> tuple[Path, bool]:
        if "/" in bundle_id or "\\" in bundle_id or ".." in bundle_id:
            raise ApiError(404, "not_found", f"Unknown bundle '{bundle_id}'.")

        upload_dir = _runs_root() / "uploads" / bundle_id
        if (upload_dir / "inputs").is_dir():
            return upload_dir, False

        root = _bundles_root()
        if root is not None:
            cand = root / bundle_id
            if (cand / "inputs").is_dir():
                return cand, (cand / "answer_key.json").is_file()

        raise ApiError(404, "not_found", f"Bundle '{bundle_id}' not found.")

    # --- Runs ------------------------------------------------------------------

    def start_run(self, req: StartRunRequest) -> dict[str, Any]:
        bundle_dir, has_answer_key = self._resolve_bundle_dir(req.bundle_id)
        now = now_colombo()
        run_id = f"run_{now:%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"
        system_id = derive_system_id(req.mode, req.model_profile)

        input_files = sorted(
            f.name
            for f in (bundle_dir / "inputs").iterdir()
            if f.is_file() and not f.name.startswith(".") and f.name.lower() not in FORBIDDEN_FILENAMES
        )

        steps = self._initial_steps(req.mode, req.model_profile, input_files)
        meta: dict[str, Any] = {
            "run_id": run_id,
            "bundle_id": req.bundle_id,
            "has_answer_key": has_answer_key,
            "mode": req.mode,
            "model_profile": req.model_profile,
            "auto_approve": req.auto_approve,
            "seed": req.seed,
            "system_id": system_id,
            "status": "queued",
            "created_at": now.isoformat(timespec="seconds"),
            "started_at": None,
            "ended_at": None,
            "current_step": steps[0]["key"] if steps else None,
            "plan_version": 0,
            "revisions": RunRevisions().model_dump(),
            "steps": steps,
            "metrics": RunMetrics().model_dump(),
            "budget": RunBudget().model_dump(),
            "error": None,
        }

        run_dir = _runs_root() / run_id
        (run_dir / "payloads").mkdir(parents=True, exist_ok=True)
        (run_dir / "trace.jsonl").write_text("", encoding="utf-8")
        self._write_meta(run_id, meta)

        t = threading.Thread(
            target=self._execute_start,
            args=(run_id, bundle_dir, req),
            daemon=True,
        )
        t.start()
        return {"run_id": run_id, "status": "queued", "system_id": system_id}

    def _initial_steps(self, mode: str, model_profile: str, input_files: list[str]) -> list[dict[str, Any]]:
        if mode == "baseline":
            return [
                RunStep(
                    key="agent",
                    label="Single Agent (Baseline)",
                    model="claude-sonnet-5-5",
                    status="pending",
                    progress=StepProgress(done=0, total=len(input_files)),
                ).model_dump(),
                RunStep(key="approval", label="Human approval", model=None, status="pending").model_dump(),
            ]

        intake_model = "claude-sonnet-5-5" if model_profile == "sonnet" else "claude-haiku-5-5"
        checker_model = "claude-sonnet-5-5" if model_profile == "sonnet" else "claude-haiku-5-5"
        return [
            RunStep(
                key="intake",
                label="Intake Agent",
                model=intake_model,
                status="pending",
                progress=StepProgress(done=0, total=len(input_files)),
                items=[StepItem(name=f, status="pending") for f in input_files],
            ).model_dump(),
            RunStep(key="planner", label="Planner Agent", model="claude-sonnet-5-5", status="pending").model_dump(),
            RunStep(key="checker", label="Checker Agent", model=checker_model, status="pending").model_dump(),
            RunStep(key="approval", label="Human approval", model=None, status="pending").model_dump(),
        ]

    def _execute_start(self, run_id: str, bundle_dir: Path, req: StartRunRequest) -> None:
        _ensure_project_on_path()
        started = now_colombo()
        with self._lock:
            meta = self._read_meta(run_id)
            meta["status"] = "running"
            meta["started_at"] = started.isoformat(timespec="seconds")
            self._write_meta(run_id, meta)

        try:
            runner_mod = self._import_runner()
            schemas_mod = self._import_schemas()
            config = schemas_mod.RunConfig(
                mode=req.mode,
                model_profile=req.model_profile,
                auto_approve=req.auto_approve,
                seed=req.seed,
            )
            result = runner_mod.start_run(run_id, bundle_dir, config, lambda ev: self._on_trace_event(run_id, ev))
            self._apply_run_result(run_id, result)
        except Exception as exc:
            self._mark_failed(run_id, "internal", f"{type(exc).__name__}: {exc}")

    def approve_or_reject(self, run_id: str, decision: str, notes: Optional[str]) -> dict[str, Any]:
        meta = self._read_meta(run_id)
        if meta["status"] != "awaiting_approval":
            raise ApiError(
                409,
                "not_awaiting_approval",
                f"Run {run_id} is in status '{meta['status']}', not 'awaiting_approval'.",
            )

        decided_at = now_colombo().isoformat(timespec="seconds")
        approval_obj = ApprovalState(
            status="approved" if decision == "approve" else "rejected",
            decided_at=decided_at,
            notes=notes,
        )
        run_dir = _runs_root() / run_id
        (run_dir / "approval.json").write_text(
            json.dumps(approval_obj.model_dump(), indent=2), encoding="utf-8"
        )

        if decision == "approve":
            with self._lock:
                meta = self._read_meta(run_id)
                meta["status"] = "completed"
                meta["ended_at"] = decided_at
                for st in meta["steps"]:
                    if st["key"] == "approval":
                        st["status"] = "done"
                        st["ended_at"] = decided_at
                        st["detail"] = "Approved by planner"
                self._write_meta(run_id, meta)
            # Also notify runner in background if needed
            threading.Thread(
                target=self._execute_resume,
                args=(run_id, decision, notes, decided_at),
                daemon=True,
            ).start()
            return {"run_id": run_id, "status": "completed", "plan_version": meta["plan_version"]}

        with self._lock:
            meta = self._read_meta(run_id)
            meta["status"] = "running"
            meta["revisions"]["human"] = int(meta["revisions"].get("human", 0)) + 1
            meta["current_step"] = "planner" if meta["mode"] == "multi_agent" else "agent"
            for st in meta["steps"]:
                if st["key"] in ("planner", "agent"):
                    st["status"] = "running"
                    st["detail"] = f"Re-planning with human notes: {notes}"
                elif st["key"] in ("checker", "approval"):
                    st["status"] = "pending"
            self._write_meta(run_id, meta)

        threading.Thread(
            target=self._execute_resume,
            args=(run_id, decision, notes, decided_at),
            daemon=True,
        ).start()
        return {"run_id": run_id, "status": "running", "plan_version": int(meta["plan_version"]) + 1}

    def _execute_resume(self, run_id: str, decision: str, notes: Optional[str], decided_at: str) -> None:
        _ensure_project_on_path()
        try:
            runner_mod = self._import_runner()
            schemas_mod = self._import_schemas()
            meta = self._read_meta(run_id)
            config = schemas_mod.RunConfig(
                mode=meta["mode"],
                model_profile=meta["model_profile"],
                auto_approve=False,
                seed=meta.get("seed"),
            )
            dec_obj = schemas_mod.ApprovalDecision(decision=decision, notes=notes, decided_at=decided_at)
            result = runner_mod.resume_run(
                run_id,
                dec_obj,
                lambda ev: self._on_trace_event(run_id, ev),
                config=config,
            )
            self._apply_run_result(run_id, result)
        except Exception as exc:
            if decision != "approve":
                self._mark_failed(run_id, "internal", f"{type(exc).__name__}: {exc}")

    def _on_trace_event(self, run_id: str, event: Any) -> None:
        run_dir = _runs_root() / run_id
        trace_path = run_dir / "trace.jsonl"
        raw = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)

        with self._lock:
            existing_lines = (
                trace_path.read_text(encoding="utf-8").splitlines() if trace_path.is_file() else []
            )
            seq = len(existing_lines) + 1
            raw["seq"] = seq
            with trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(raw) + "\n")

            meta = self._read_meta(run_id)
            ev_type = raw.get("type")
            agent = raw.get("agent")
            ts = raw.get("ts") or now_colombo().isoformat(timespec="seconds")

            if ev_type == "llm_call":
                meta["metrics"]["llm_calls"] += 1
                meta["metrics"]["input_tokens"] += int(raw.get("input_tokens") or 0)
                meta["metrics"]["cached_input_tokens"] += int(raw.get("cached_input_tokens") or 0)
                meta["metrics"]["output_tokens"] += int(raw.get("output_tokens") or 0)
                meta["metrics"]["cost_usd"] = round(
                    float(meta["metrics"]["cost_usd"]) + float(raw.get("cost_usd") or 0.0), 4
                )
            elif ev_type == "tool_call":
                meta["metrics"]["tool_calls"] += 1

            if meta.get("started_at"):
                try:
                    t0 = now_colombo().fromisoformat(meta["started_at"])
                    meta["metrics"]["elapsed_s"] = round((now_colombo() - t0).total_seconds(), 1)
                except Exception:
                    pass

            for st in meta["steps"]:
                if st["key"] == agent:
                    if ev_type == "agent_start":
                        st["status"] = "running"
                        st["started_at"] = st["started_at"] or ts
                        meta["current_step"] = agent
                    elif ev_type == "agent_end":
                        st["status"] = "done"
                        st["ended_at"] = ts
                        if raw.get("summary"):
                            st["detail"] = raw["summary"]
                    elif raw.get("summary"):
                        st["detail"] = raw["summary"]

                    if agent == "intake" and raw.get("item"):
                        for item in st.get("items", []):
                            if item["name"] == raw["item"] and ev_type in ("agent_end", "llm_call"):
                                item["status"] = "done"
                        done_cnt = sum(1 for i in st.get("items", []) if i["status"] == "done")
                        if st.get("progress"):
                            st["progress"]["done"] = done_cnt

            if ev_type == "checker_verdict":
                verdict = (raw.get("payload") or {}).get("verdict")
                if verdict == "revise":
                    meta["revisions"]["checker"] = int(meta["revisions"].get("checker", 0)) + 1

            self._write_meta(run_id, meta)

    def _apply_run_result(self, run_id: str, result: Any) -> None:
        run_dir = _runs_root() / run_id
        with self._lock:
            meta = self._read_meta(run_id)
            status = getattr(result, "status", "failed")
            meta["status"] = status
            plan = getattr(result, "plan", None)
            version = int(getattr(result, "plan_version", 0) or (meta["plan_version"] + 1 if plan else 0))

            if plan is not None:
                plan_dict = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
                (run_dir / f"plan_v{version}.json").write_text(
                    json.dumps(plan_dict, indent=2), encoding="utf-8"
                )
                meta["plan_version"] = version
                approval_file = run_dir / "approval.json"
                if status == "awaiting_approval":
                    approval_file.write_text(
                        json.dumps(ApprovalState(status="pending").model_dump(), indent=2),
                        encoding="utf-8",
                    )

            now_iso = now_colombo().isoformat(timespec="seconds")
            if status == "awaiting_approval":
                meta["current_step"] = "approval"
                for st in meta["steps"]:
                    if st["key"] == "approval":
                        st["status"] = "running"
                        st["started_at"] = now_iso
                        st["detail"] = "Awaiting human planner review"
                    elif st["status"] == "running":
                        st["status"] = "done"
                        st["ended_at"] = now_iso
            elif status in ("completed", "failed"):
                meta["ended_at"] = now_iso
                if status == "failed":
                    err = getattr(result, "error", None)
                    meta["error"] = {
                        "code": getattr(err, "code", "internal") if err else "internal",
                        "message": getattr(err, "message", "Run failed.") if err else "Run failed.",
                    }

            self._write_meta(run_id, meta)

    def _mark_failed(self, run_id: str, code: str, message: str) -> None:
        with self._lock:
            meta = self._read_meta(run_id)
            meta["status"] = "failed"
            meta["ended_at"] = now_colombo().isoformat(timespec="seconds")
            meta["error"] = {"code": code, "message": message}
            for st in meta["steps"]:
                if st["status"] == "running":
                    st["status"] = "failed"
            self._write_meta(run_id, meta)

    def get_run(self, run_id: str) -> dict[str, Any]:
        meta = self._read_meta(run_id)
        return {k: v for k, v in meta.items() if k not in ("has_answer_key", "auto_approve", "seed")}

    def list_runs(self, bundle_id: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
        root = _runs_root()
        runs: list[dict[str, Any]] = []
        for entry in root.iterdir():
            if not entry.is_dir() or entry.name == "uploads":
                continue
            meta_file = entry / "meta.json"
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if bundle_id and meta.get("bundle_id") != bundle_id:
                continue
            summary = {
                k: v
                for k, v in meta.items()
                if k not in ("steps", "has_answer_key", "auto_approve", "seed")
            }
            runs.append(summary)
        runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return runs[: max(1, min(limit, 100))]

    def get_plan(self, run_id: str, version: Optional[int] = None) -> dict[str, Any]:
        meta = self._read_meta(run_id)
        latest = int(meta.get("plan_version") or 0)
        if latest == 0:
            raise ApiError(409, "plan_not_ready", f"Run {run_id} is still {meta['status']}.")
        target_v = version if version is not None else latest
        plan_path = _runs_root() / run_id / f"plan_v{target_v}.json"
        if not plan_path.is_file():
            raise ApiError(404, "not_found", f"Plan version {target_v} not found for run {run_id}.")

        approval_path = _runs_root() / run_id / "approval.json"
        approval = (
             json.loads(approval_path.read_text(encoding="utf-8"))
            if approval_path.is_file()
            else ApprovalState(status="pending").model_dump()
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return {
            "run_id": run_id,
            "plan_version": target_v,
            "approval": approval,
            "plan": plan,
        }

    def get_trace_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self._read_meta(run_id)
        trace_path = _runs_root() / run_id / "trace.jsonl"
        if not trace_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if int(ev.get("seq", 0)) > after_seq:
                events.append(ev)
        return events

    def get_trace_jsonl(self, run_id: str) -> str:
        self._read_meta(run_id)
        trace_path = _runs_root() / run_id / "trace.jsonl"
        return trace_path.read_text(encoding="utf-8") if trace_path.is_file() else ""

    def score_run(self, run_id: str) -> dict[str, Any]:
        meta = self._read_meta(run_id)
        if not meta.get("has_answer_key"):
            raise ApiError(404, "no_answer_key", f"Bundle '{meta['bundle_id']}' has no answer key.")
        plan_resp = self.get_plan(run_id)
        bundle_dir, _ = self._resolve_bundle_dir(meta["bundle_id"])
        _ensure_project_on_path()
        try:
            score_mod = importlib.import_module("evaluation.score")
            result = score_mod.score_run(plan_resp["plan"], bundle_dir)
            out = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
            out.setdefault("run_id", run_id)
            out.setdefault("bundle_id", meta["bundle_id"])
            out.setdefault("plan_version", plan_resp["plan_version"])
            return out
        except ImportError as exc:
            raise ApiError(500, "internal", f"Scoring module not merged yet: {exc}") from exc
        except Exception as exc:
            raise ApiError(500, "internal", f"Scoring failed: {exc}") from exc

    def list_evaluations(self) -> list[dict[str, Any]]:
        root = _results_root()
        if not root.is_dir():
            return []
        evals: list[dict[str, Any]] = []
        for entry in sorted(root.iterdir(), reverse=True):
            summary_file = entry / "summary.json"
            if not entry.is_dir() or not summary_file.is_file():
                continue
            try:
                data = json.loads(summary_file.read_text(encoding="utf-8"))
                evals.append(
                    {
                        "eval_id": data.get("eval_id", entry.name),
                        "created_at": data.get("created_at"),
                        "status": data.get("status", "complete"),
                        "is_mock": bool(data.get("is_mock", False)),
                        "n_runs": len(data.get("runs", [])) or data.get("n_runs", 0),
                    }
                )
            except Exception:
                continue
        return evals

    def get_evaluation(self, eval_id: str) -> dict[str, Any]:
        root = _results_root()
        if eval_id == "latest":
            evals = self.list_evaluations()
            if not evals:
                raise ApiError(404, "not_found", "No benchmark evaluation results found yet.")
            eval_id = evals[0]["eval_id"]

        if "/" in eval_id or "\\" in eval_id or ".." in eval_id:
            raise ApiError(404, "not_found", f"Evaluation '{eval_id}' not found.")

        summary_file = root / eval_id / "summary.json"
        if not summary_file.is_file():
            raise ApiError(404, "not_found", f"Evaluation '{eval_id}' not found.")
        return json.loads(summary_file.read_text(encoding="utf-8"))

    # --- Helpers ---------------------------------------------------------------

    def _read_meta(self, run_id: str) -> dict[str, Any]:
        if "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        meta_file = _runs_root() / run_id / "meta.json"
        if not meta_file.is_file():
            raise ApiError(404, "not_found", f"Run '{run_id}' not found.")
        return json.loads(meta_file.read_text(encoding="utf-8"))

    def _write_meta(self, run_id: str, meta: dict[str, Any]) -> None:
        meta_file = _runs_root() / run_id / "meta.json"
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _import_runner() -> Any:
        for mod_name in ("project.core.runner", "core.runner"):
            try:
                return importlib.import_module(mod_name)
            except ImportError:
                continue
        raise ImportError("project/core/runner.py is not available yet (awaiting Hatheem's PR).")

    @staticmethod
    def _import_schemas() -> Any:
        for mod_name in ("project.core.schemas", "core.schemas"):
            try:
                return importlib.import_module(mod_name)
            except ImportError:
                continue
        raise ImportError("project/core/schemas.py is not available yet (awaiting Hatheem's PR).")
