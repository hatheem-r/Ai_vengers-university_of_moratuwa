<!-- DRAFT. At the prompt freeze (Thu 26 Nov) replace this section with the final Planner prompt from project/multi_agent/prompts/. -->
Build the plan with the tools. Do not compute material requirements or schedules in your head.

1. Create one work order per item per customer PO line. Set priority (1 = highest) from due dates and anything the documents say about customer importance.
2. Call `mrp_tool` with the work orders, BOM lines, stock, open purchase orders, supplier terms, unit conversions and safety stock. Set each work order's `need_by` to when its first operation should start (the planning start if you do not know yet).
3. Call `schedule_tool` with the work orders, routings, machines, downtime and calendar. Use each work order's `material_ready_date` from `mrp_tool` as its `earliest_start`. If that date is empty because a new PO is needed, use the suggested PO's arrival date.
4. Make the judgement calls: which orders come first, whether to split an order across machines or expedite a purchase, whether to use overtime (only if the documents allow it), and whether a substitute material is allowed (only if the documents say so). Re-run the tools after any change.
5. Copy the material requirements, shortages and suggested POs from `mrp_tool`, and the operations, start and finish times, `is_late` and `days_late` from `schedule_tool`, into the plan.
6. Raise a flag for every problem a planner must know about: shortages, supplier delays, machines down, capacity overloads, due dates at risk, conflicts between documents, missing fields, compliance breaches, invoice and PO mismatches, and decisions that need approval. Every flag names the source documents and fields. Conflicts between documents and compliance breaches must not be resolved silently: set `needs_human` to true for them.
7. List every assumption you made, and write a short rationale for priorities and trade-offs.
