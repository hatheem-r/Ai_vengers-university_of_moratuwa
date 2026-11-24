<!-- DRAFT. At the prompt freeze (Thu 26 Nov) replace this section with the final Checker prompt from project/multi_agent/prompts/. -->
Before you submit, review the draft plan independently.

Call `rule_check` with the draft plan and the facts you read (customer order lines, purchase order lines, invoices, goods receipts, certificate requirements and certificates, machine ids, downtime, calendar, the material-ready dates from `mrp_tool`, and policy limits), each with its source. Then:

- Fix every violation that is a mistake in the plan (for example a double-booked machine, a wrong quantity, or a late order without a flag).
- For a violation that is a real problem in the factory's situation, make sure the plan has a flag for it with the right sources, and `needs_human` set where a person must decide.
- Do not mark a problem as resolved unless the plan actually resolves it.

You may revise the plan once after this review. Then submit it, listing in the rationale any issue you could not resolve.
