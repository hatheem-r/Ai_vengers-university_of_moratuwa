"""Fake model for the single agent: tests and offline demos, no API key.

    from baseline import fakes
    fakes.install(extraction_key=json.load(open(".../extraction_key.json")))
    ...
    fakes.uninstall()

The fake reads every document, then calls mrp_tool, schedule_tool (with mrp_tool's
material_ready), rule_check, and gives the final draft. Tool inputs come from the
extraction key through multi_agent/fakes.py's mapping helpers: a test helper, not
planning logic. Tests may read the key; the system never does.
"""
from __future__ import annotations

import itertools
import json
import re
from datetime import date
from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from core.schemas import CapacityLine, CustomerPOLine, FactoryPolicyFields
from multi_agent import fakes as ma_fakes
from multi_agent import llm as llm_mod
from multi_agent.fakes import FakeChatModel, Responder, build_tool_inputs, draft_work_orders
from multi_agent.llm import message_text

_ids = itertools.count(1)


def _call(name: str, args: dict) -> dict:
    return {"name": name, "id": f"call_b{next(_ids)}", "args": args}


def _results(messages: list[BaseMessage], tool: str) -> list[ToolMessage]:
    names = {}
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            names[tc["id"]] = tc["name"]
    return [m for m in messages if isinstance(m, ToolMessage) and names.get(m.tool_call_id) == tool]


def agent_responder(extraction_key: dict, extra_flags: Optional[list[dict]] = None) -> Responder:
    # Sorted by file name, as the multi-agent Planner sees its extractions (planner._context_message).
    docs = sorted(extraction_key.get("documents", []), key=lambda d: d["document"])

    def facts() -> dict:
        _, sched_in = build_tool_inputs(docs)
        policy = next((FactoryPolicyFields.model_validate(d["fields"]) for d in docs
                       if d["doc_type"] == "factory_policy"), FactoryPolicyFields())
        lines = [CustomerPOLine.model_validate(ln) for d in docs if d["doc_type"] == "customer_po"
                 for ln in d.get("lines", [])]
        return {"policy": policy.model_dump(mode="json"),
                "work_centres": [CapacityLine.model_validate(w).model_dump(mode="json")
                                 for w in [x.model_dump() for x in sched_in.work_centres]],
                "downtime": [d.model_dump(mode="json") for d in sched_in.downtime],
                "order_lines": [ln.model_dump(mode="json") for ln in lines]}

    def respond(messages: list[BaseMessage]) -> AIMessage:
        listed = re.findall(r"^- (\S+)$", message_text(messages[1]), re.M)
        wos = draft_work_orders(docs)
        mrp_in, sched_in = build_tool_inputs(docs, wos)
        notes = [m for m in messages if isinstance(m, HumanMessage) and "rejected an earlier plan" in message_text(m)]
        draft = {"planning_start": mrp_in.planning_start.isoformat(), "work_orders": wos,
                 "flags": extra_flags or [],
                 "assumptions": ["Fake agent: tool inputs mapped directly from the extraction key."]
                                + ([f"Human notes applied ({len(notes)})."] if notes else []),
                 "rationale": "Deterministic fake single agent used in tests."}
        if not _results(messages, "read_document"):
            return AIMessage(content="", tool_calls=[_call("read_document", {"file_name": n}) for n in listed])
        mrp = _results(messages, "mrp_tool")
        if not mrp or (notes and len(mrp) < len(notes) + 1):
            return AIMessage(content="", tool_calls=[_call("mrp_tool", mrp_in.model_dump(mode="json"))])
        sched = _results(messages, "schedule_tool")
        if len(sched) < len(mrp):
            ready = json.loads(mrp[-1].content).get("material_ready", {})
            for job in sched_in.jobs:
                if job.wo_id in ready:
                    job.earliest_start = date.fromisoformat(ready[job.wo_id])
            return AIMessage(content="", tool_calls=[_call("schedule_tool", sched_in.model_dump(mode="json"))])
        if len(_results(messages, "rule_check")) < len(sched):
            return AIMessage(content="", tool_calls=[_call("rule_check", {"draft": draft, **facts()})])
        return ma_fakes._json_reply(draft)
    return respond


def install(extraction_key: dict, responder: Optional[Responder] = None) -> FakeChatModel:
    model = FakeChatModel(responder=responder or agent_responder(extraction_key), model="fake-agent", calls=[])
    llm_mod.set_llm_factory(lambda agent, spec: model)
    return model


def uninstall() -> None:
    llm_mod.set_llm_factory(None)
