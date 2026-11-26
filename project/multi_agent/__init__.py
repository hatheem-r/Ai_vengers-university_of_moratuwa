"""Multi-agent Work Order Planner (systems M-mix and M-S).

Intake (one call per document, fanned out with Send) -> Planner (mrp_tool, schedule_tool)
-> Checker (rule_check + review, at most one revision) -> human approval (interrupt()).

Entry points:
    multi_agent.graph.start / resume     used by core/runner.py for mode="multi_agent"
    multi_agent.intake.extract_document  Intake on one file (Ajey's model evaluation)
"""
