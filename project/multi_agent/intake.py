"""Intake agent: one LLM call per document turns a file into an ExtractedDocument.

`extract_document(path, model=...)` is the public entry point (Ajey's Intake model
evaluation calls it directly). The graph runs it once per file in parallel via Send.

Files are turned into text first (PDF text with pdfplumber, every sheet of a workbook
as tab-separated rows). A PDF with almost no text layer (a scan) is also attached as a
PDF document block so a vision-capable model can read it.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import json
import typing
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from core.schemas import ExtractedDocument, OtherDocument, RunConfig, parse_extracted_document

from .context import RunContext, get_ctx, null_ctx
from core.harness import SchemaRepairFailed, call_json

from .llm import ModelLike, chat_model, parse_json_reply, spec_for
from .prompts import load_prompt

SUPPORTED_SUFFIXES = {".pdf", ".xlsx", ".xlsm", ".csv", ".txt", ".eml", ".md", ".json", ".png", ".jpg", ".jpeg"}
MAX_ROWS_PER_SHEET = 500
MAX_CHARS = 60_000
SCAN_TEXT_THRESHOLD = 40  # fewer text characters than this per page = treat as a scan


# --- files -> model input ------------------------------------------------------------

def list_documents(inputs_dir: Union[str, Path]) -> list[Path]:
    """The files Intake reads, in a stable order (hidden files skipped)."""
    return sorted(p for p in Path(inputs_dir).iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED_SUFFIXES)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\t", " ").replace("\n", " ")


def _xlsx_text(path: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            rows, n = [], 0
            for row in ws.iter_rows(values_only=True):
                cells = [_cell(v) for v in row]
                while cells and cells[-1] == "":
                    cells.pop()
                if not cells:
                    continue
                n += 1
                if n > MAX_ROWS_PER_SHEET:
                    rows.append(f"... (more rows cut after {MAX_ROWS_PER_SHEET})")
                    break
                rows.append(f"r{n}\t" + "\t".join(cells))
            parts.append(f"### Sheet: {ws.title}\n" + "\n".join(rows))
    finally:
        wb.close()
    return "\n\n".join(parts)


def _pdf_text(path: Path) -> tuple[str, bool]:
    """(text, looks_scanned)."""
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            pages.append(f"### Page {i}\n{page.extract_text() or ''}")
        n_pages = max(1, len(pdf.pages))
    text = "\n\n".join(pages)
    body_chars = sum(len(p.split("\n", 1)[1]) if "\n" in p else 0 for p in pages)
    return text, body_chars / n_pages < SCAN_TEXT_THRESHOLD


def document_content(path: Union[str, Path]) -> list[dict]:
    """Content blocks for the Intake user message."""
    path = Path(path)
    suffix = path.suffix.lower()
    blocks: list[dict] = []
    if suffix in (".xlsx", ".xlsm"):
        text = _xlsx_text(path)
    elif suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            text = "\n".join(f"r{i}\t" + "\t".join(r) for i, r in enumerate(csv.reader(f), 1))
    elif suffix == ".pdf":
        text, scanned = _pdf_text(path)
        if scanned:
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                          "data": base64.b64encode(path.read_bytes()).decode()}})
            text += "\n\n(The text layer is empty or thin: read the attached PDF pages.)"
    elif suffix in (".png", ".jpg", ".jpeg"):
        media = "image/png" if suffix == ".png" else "image/jpeg"
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                   "data": base64.b64encode(path.read_bytes()).decode()}})
        text = "(Image document: read the attached image.)"
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... (cut)"
    blocks.append({"type": "text", "text": f"File: {path.name}\n\n{text}"})
    return blocks


# --- prompt: doc types and their fields, generated from core/schemas.py ----------------

def _type_name(tp: Any) -> str:
    origin, args = get_origin(tp), get_args(tp)
    if origin is typing.Annotated:
        return _type_name(args[0])
    if origin is Union or (origin is not None and str(origin) == "types.UnionType"):
        names = [_type_name(a) for a in args if a is not type(None)]
        return " | ".join(names) + (" | null" if type(None) in args else "")
    if origin is typing.Literal:
        return " | ".join(json.dumps(a) for a in args)
    if origin in (list, typing.List):
        return f"[{_type_name(args[0])}]" if args else "[any]"
    if origin in (dict, typing.Dict):
        return f"{{{_type_name(args[0])}: {_type_name(args[1])}}}" if args else "object"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return _signature(tp)
    return {str: "str", int: "int", float: "number", bool: "bool", dt.date: "date",
            dt.datetime: "datetime"}.get(tp, getattr(tp, "__name__", "any"))


def _signature(model: type[BaseModel]) -> str:
    parts = []
    for name, f in model.model_fields.items():
        opt = "" if f.is_required() else "?"
        parts.append(f"{name}{opt}: {_type_name(f.annotation)}")
    return "{" + ", ".join(parts) + "}"


def doc_type_catalogue() -> str:
    lines = []
    for member in get_args(get_args(ExtractedDocument)[0]):
        doc_type = member.model_fields["doc_type"].default
        fields_t = member.model_fields["fields"].annotation
        lines_t = member.model_fields["lines"].annotation
        lines.append(f"- {doc_type}\n    fields: {_type_name(fields_t)}\n    lines: {_type_name(lines_t)}")
    return "\n".join(lines)


def system_prompt() -> str:
    return load_prompt("intake").replace("{{DOC_TYPES}}", doc_type_catalogue())


# --- extraction -----------------------------------------------------------------------

def _validate(data: Any, name: str) -> Any:
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    data = {**data, "document": name}
    return parse_extracted_document(data)


def extract_document(path: Union[str, Path], model: ModelLike = "haiku-5-5", *,
                     ctx: Optional[RunContext] = None) -> Any:
    """Read one file with one LLM call (plus one repair call if the JSON is invalid).

    `model`: a registry key ('haiku-5-5'), a raw API model ID, a ModelSpec, or a LangChain
    chat model. Returns a typed ExtractedDocument (core/schemas.py). Never raises on bad
    model output: after a failed repair it returns an OtherDocument with `issues`.
    """
    path = Path(path)
    ctx = ctx or null_ctx()
    llm, model_id = chat_model("intake", model)
    messages = [SystemMessage(system_prompt()), HumanMessage(content=document_content(path))]
    try:
        return call_json(ctx, "intake", llm, model_id, messages, lambda r: _validate(parse_json_reply(r), path.name),
                         item=path.name, purpose="extract", repairs=1,
                         repair_hint="Return the corrected JSON object only.")
    except SchemaRepairFailed as exc:
        raw: Any = {}
        try:
            raw = parse_json_reply(exc.reply)
        except ValueError:
            pass
        raw = raw if isinstance(raw, dict) else {}
        fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
        lines = raw.get("lines") if isinstance(raw.get("lines"), list) else []
        return OtherDocument(document=path.name, fields=fields,
                             lines=[ln for ln in lines if isinstance(ln, dict)],
                             issues=[f"schema_invalid after repair ({raw.get('doc_type', '?')}): {_short_error(exc.error)}"])


def _short_error(exc: Exception, limit: int = 1500) -> str:
    text = str(exc)
    return text if len(text) <= limit else text[:limit] + " ..."


# --- graph nodes --------------------------------------------------------------------------

def intake_start(state: dict) -> dict:
    """Lists the documents and opens the Intake step; the fan-out happens on the edge."""
    ctx = get_ctx(state["run_id"])
    docs = [p.name for p in list_documents(state["inputs_dir"])]
    ctx.emit("agent_start", agent="intake", model=spec_for("intake", ctx.config).model_id,
             summary=f"Intake: reading {len(docs)} documents", payload={"documents": docs, "total": len(docs)})
    return {"documents": docs}


def intake_document(task: dict) -> dict:
    """One document (runs in parallel, one Send per file)."""
    ctx = get_ctx(task["run_id"])
    config: RunConfig = ctx.config
    path = Path(task["inputs_dir"]) / task["document"]
    spec = spec_for("intake", config)
    ctx.emit("agent_start", agent="intake", item=path.name, model=spec.model_id, summary=f"Reading {path.name}")
    doc = extract_document(path, model=spec, ctx=ctx)
    ctx.emit("agent_end", agent="intake", item=path.name, model=spec.model_id,
             summary=f"{path.name}: {doc.doc_type}, {len(doc.lines)} line(s)"
                     + (f", {len(doc.issues)} issue(s)" if doc.issues else ""),
             payload={"doc_type": doc.doc_type, "issues": doc.issues})
    return {"extractions": [doc.model_dump(mode="json", exclude_none=True)]}


def intake_join(state: dict) -> dict:
    ctx = get_ctx(state["run_id"])
    docs = sorted(state.get("extractions", []), key=lambda d: d["document"])
    issues = sum(len(d.get("issues", [])) for d in docs)
    ctx.emit("agent_end", agent="intake", summary=f"Intake done: {len(docs)} documents, {issues} issue(s)",
             payload={"done": len(docs), "total": len(state.get("documents", []))})
    ctx.handoff("intake", "planner", f"Intake → Planner: {len(docs)} extracted documents")
    return {}


