"""Fake chat models for tests and offline demos: no API key, deterministic output.

    from multi_agent import fakes
    fakes.install(extraction_key=json.load(open(".../extraction_key.json")))   # all three agents
    ...
    fakes.uninstall()

- Intake fake: answers with the extraction-key entry for the file named in the message
  (falls back to an `other` document). Tests may read the key; the system never does.
- Planner fake: reads the extractions from its prompt, calls mrp_tool, then schedule_tool,
  then returns a draft. The tool inputs come from `build_tool_inputs()`, a plain mapping
  from extractions to tool models; it is a test helper, not planning logic.
- Checker fake: returns the verdicts it was given, in order (then "pass").
"""
from __future__ import annotations

import itertools
import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

from core.models import ModelSpec
from core.schemas import (BomLine, CapacityLine, DowntimeWindow, MrpDemand, MrpToolInput, OpenSupply,
                          ScheduleJob, ScheduleOperation, ScheduleToolInput, StockPosition, WipLoad)

from . import llm as llm_mod
from .llm import message_text

Responder = Callable[[list[BaseMessage]], AIMessage]
_ids = itertools.count(1)


class FakeChatModel(BaseChatModel):
    """A chat model whose reply is computed by `responder(messages)`. Reports token usage
    as characters / 4 so budgets and traces behave like the real thing."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    responder: Any
    model: str = "fake"
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def bind_tools(self, tools, **kwargs):  # tools are described in the prompt; fakes ignore them
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        reply = self.responder(list(messages))
        n_in = sum(len(message_text(m)) for m in messages) // 4 + 1
        n_out = len(message_text(reply)) // 4 + 1 + 50 * len(reply.tool_calls or [])
        reply.usage_metadata = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _json_reply(obj: Any) -> AIMessage:
    return AIMessage(content="```json\n" + json.dumps(obj, indent=1) + "\n```")


# --- Intake ------------------------------------------------------------------------------

def intake_responder(extraction_key: Optional[dict] = None) -> Responder:
    by_name = {d["document"]: d for d in (extraction_key or {}).get("documents", [])}

    def respond(messages: list[BaseMessage]) -> AIMessage:
        m = re.search(r"File: (\S+)", message_text(messages[1]) if len(messages) > 1 else "")
        name = m.group(1) if m else "?"
        doc = by_name.get(name)
        if doc is None:
            return _json_reply({"doc_type": "other", "fields": {}, "lines": [], "issues": ["fake: no key entry"]})
        return _json_reply({k: v for k, v in doc.items() if k != "document"})
    return respond


# --- Planner ------------------------------------------------------------------------------

def _docs_from_prompt(messages: list[BaseMessage]) -> list[dict]:
    text = message_text(next(m for m in messages if isinstance(m, HumanMessage)))
    section = text.split("## Extracted documents", 1)[1].split("\n## ", 1)[0]
    return [json.loads(ln) for ln in section.splitlines() if ln.startswith("{")]


def _of(docs: list[dict], t: str) -> list[dict]:
    return [d for d in docs if d["doc_type"] == t]


def planning_start_from(docs: list[dict]) -> date:
    dates = [d["fields"].get("as_of") or d["fields"].get("date") for d in docs
             if d["doc_type"] in ("stock_report", "production_wip_report")]
    latest = max((date.fromisoformat(x) for x in dates if x), default=date.today())
    nxt = latest + timedelta(days=1)
    while nxt.weekday() == 6:
        nxt += timedelta(days=1)
    return nxt


def draft_work_orders(docs: list[dict]) -> list[dict]:
    lines = []
    for po in _of(docs, "customer_po"):
        for ln in po.get("lines") or po["fields"].get("lines", []):
            lines.append((po["fields"].get("delivery_date") or "2099-12-31", po["fields"]["po_number"], ln))
    lines.sort(key=lambda x: x[0])
    out = []
    for i, (due, po_no, ln) in enumerate(lines, 1):
        variant = {"sizes": ln["sizes"]} if ln.get("sizes") else None
        out.append({"wo_id": f"WO-{i:03d}", "item_code": ln.get("item_code") or ln.get("buyer_ref"), "qty": ln["qty"],
                    "uom": ln["uom"], "customer_po": po_no, "variant": variant, "priority": i, "due_date": due})
    return out


def _routing_for(item: str, routing: list[dict]) -> list[dict]:
    exact = [r for r in routing if r.get("item") == item]
    if exact:
        return exact
    tokens = set(item.split("-"))
    styles = sorted({r.get("style") for r in routing if r.get("style")})
    style = next((s for s in styles if tokens & set(s.split("-"))), styles[0] if styles else None)
    return [r for r in routing if r.get("style") == style]


def build_tool_inputs(docs: list[dict], wos: Optional[list[dict]] = None) -> tuple[MrpToolInput, ScheduleToolInput]:
    """Straight mapping from extractions to the two tool inputs (test helper)."""
    start = planning_start_from(docs)
    wos = wos or draft_work_orders(docs)
    bom = [BomLine.model_validate(ln) for d in _of(docs, "bill_of_materials") for ln in d["lines"]]
    stock: dict[str, StockPosition] = {}
    for d in _of(docs, "stock_report"):
        for ln in d["lines"]:
            on_hand = next((ln[k] for k in ("on_hand", "in_stock", "qty") if ln.get(k) is not None), 0)
            reserved = next((ln[k] for k in ("reserved", "committed") if ln.get(k) is not None), 0)
            hold = on_hand if "hold" in str(ln.get("status", "")).lower() else 0
            p = stock.setdefault(ln["item_code"], StockPosition(item_code=ln["item_code"], uom=ln["uom"], on_hand_qty=0))
            p.on_hand_qty += on_hand
            p.reserved_qty += reserved
            p.on_hold_qty += hold
    supply = [OpenSupply(po_number=d["fields"]["po_number"], item_code=d["fields"]["item_code"], qty=d["fields"]["qty"],
                         uom=d["fields"]["uom"], eta=d["fields"]["expected_arrival_printed"],
                         supplier_id=d["fields"].get("supplier_id"))
              for d in _of(docs, "supplier_purchase_order") if d["fields"].get("expected_arrival_printed")]
    mrp = MrpToolInput(planning_start=start, bom=bom, stock=list(stock.values()), open_supply=supply,
                       demands=[MrpDemand(wo_id=w["wo_id"], item_code=w["item_code"], qty=w["qty"], uom=w["uom"],
                                          need_by=start) for w in wos])
    routing = [ln for d in _of(docs, "routing") for ln in d["lines"]]
    jobs = [ScheduleJob(wo_id=w["wo_id"], item_code=w["item_code"], qty=w["qty"], due_date=w["due_date"],
                        priority=w["priority"],
                        operations=[ScheduleOperation(seq=r["seq"], operation=r["operation"], work_centre=r["work_centre"],
                                                      std_min_per_unit=r["std_min_per_unit"], setup_min=r.get("setup_min", 0))
                                    for r in _routing_for(w["item_code"], routing)])
            for w in wos]
    wcs = [CapacityLine.model_validate(ln) for d in _of(docs, "capacity_shift_plan") for ln in d["lines"]]
    downtime = [DowntimeWindow(work_centre=ln["work_centre"], start=ln["start"],
                               end_incl=ln.get("unavailable_until_incl") or ln.get("expected_return") or ln["start"])
                for d in _of(docs, "maintenance_log") for ln in d["lines"] if ln.get("status") == "Open"]
    wip = [WipLoad(work_centre=ln["work_centre"], load_minutes=ln["load_minutes"], order=ln.get("order"))
           for d in _of(docs, "production_wip_report") for ln in d["lines"]]
    sched = ScheduleToolInput(planning_start=start, jobs=jobs, work_centres=wcs, downtime=downtime, wip=wip)
    return mrp, sched


def planner_responder(extra_flags: Optional[list[dict]] = None) -> Responder:
    """mrp_tool -> schedule_tool (with material_ready) -> draft. Re-runs both tools on every round."""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        docs = _docs_from_prompt(messages)
        wos = draft_work_orders(docs)
        mrp_in, sched_in = build_tool_inputs(docs, wos)
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            return AIMessage(content="", tool_calls=[{"name": "mrp_tool", "id": f"call_{next(_ids)}",
                                                      "args": mrp_in.model_dump(mode="json")}])
        if len(tool_msgs) == 1:
            ready = json.loads(tool_msgs[0].content).get("material_ready", {})
            for job in sched_in.jobs:
                if job.wo_id in ready:
                    job.earliest_start = date.fromisoformat(ready[job.wo_id])
            return AIMessage(content="", tool_calls=[{"name": "schedule_tool", "id": f"call_{next(_ids)}",
                                                      "args": sched_in.model_dump(mode="json")}])
        notes = [m for m in messages if isinstance(m, HumanMessage) and "rejected an earlier plan" in message_text(m)]
        draft = {"planning_start": mrp_in.planning_start.isoformat(), "work_orders": wos,
                 "flags": extra_flags or [],
                 "assumptions": ["Fake planner: tool inputs mapped directly from the extractions."]
                                + (["Human notes applied."] if notes else []),
                 "rationale": "Deterministic fake planner used in tests."}
        return _json_reply(draft)
    return respond


# --- Checker --------------------------------------------------------------------------------

def checker_responder(verdicts: Iterable[str] = ()) -> Responder:
    queue = list(verdicts)

    def respond(messages: list[BaseMessage]) -> AIMessage:
        verdict = queue.pop(0) if queue else "pass"
        issues = ["fake checker: please re-check the shortages"] if verdict == "revise" else []
        return _json_reply({"verdict": verdict, "issues": issues})
    return respond


# --- install ---------------------------------------------------------------------------------

def install(extraction_key: Optional[dict] = None, checker_verdicts: Iterable[str] = (),
            planner: Optional[Responder] = None) -> dict[str, FakeChatModel]:
    """Route every agent to a fake model. Returns the models (their `.calls` record the prompts)."""
    fakes = {"intake": FakeChatModel(responder=intake_responder(extraction_key), model="fake-intake", calls=[]),
             "planner": FakeChatModel(responder=planner or planner_responder(), model="fake-planner", calls=[]),
             "checker": FakeChatModel(responder=checker_responder(checker_verdicts), model="fake-checker", calls=[])}

    def factory(agent: str, spec: ModelSpec) -> BaseChatModel:
        return fakes[agent]
    llm_mod.set_llm_factory(factory)
    return fakes


def uninstall() -> None:
    llm_mod.set_llm_factory(None)
