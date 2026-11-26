# Synthetic factory documents

This folder holds the generator and the generated test data for the benchmark: 24 bundles of factory documents, each with a computed answer key.
Why each document has the layout it has, with sources, is in [EVIDENCE.md](EVIDENCE.md).

## Regenerate

From the repository root:

```bash
pip install -r data/requirements.txt          # openpyxl, reportlab, Pillow
python -m data.generator.generate --all       # 18 test + 3 dev + 3 scale bundles (about 30 s)
python -m data.generator.validate             # feasibility self-check of every answer key
```

One bundle on its own:

```bash
python -m data.generator.generate --profile A --scenario S3 --mess typical --seed 7
python -m data.generator.generate --profile A --scenario S1 --mess typical --seed 21 --orders 5   # scalability
```

The same seed always gives byte-identical files.

## What is in a bundle

```
data/bundles/<bundle_id>/
  inputs/               the ONLY files a system (or a human planner) may open
  state.json            hidden ground truth the documents were rendered from
  manifest.json         scenario, mess level, defects applied, planted conflicts, file list
  answer_key.json       expected WorkOrderPlan (API.md section 5) + expected flags
  extraction_key.json   what each input document says, field by field (for Intake evaluation)
```

**Hidden-data rule:** agents and the API only ever see `inputs/`. `state.json`, `manifest.json`, `answer_key.json` and `extraction_key.json` are for the generator and `evaluation/` only.

### Documents per profile

| Document | A: apparel (Odoo + Excel) | B: steel furniture (SAP Business One) | C: tea packer (TallyPrime) |
|---|---|---|---|
| Customer PO | `customer_po_*.pdf`, size x colour matrix | `customer_po_*.pdf`, buyer article no. | `customer_po_*.pdf`, buyer SKU |
| Sales order confirmation (S6 only) | `sales_order_confirmation_*.pdf` | same | not used (C's conflict is a delivery note) |
| Bill of materials | `bom_techpack.xlsx` | `bom_sapb1_export.xlsx` (multi-level) | `bom_tally.xlsx` (per 1,000 units) |
| Routing | `routing_odoo_operations.xlsx` + `operation_bulletin.xlsx` | `routing_sapb1_stages.xlsx` | `process_sheet.xlsx` (speeds) |
| Stock | `stock_on_hand_odoo.xlsx` | `inventory_status_report.xlsx` | `godown_summary_tally.xlsx` (names, batches, expiry, a QC-hold lot) |
| Open supplier POs | `purchase_order_*.pdf` | same | same |
| Supplier tax invoice / delivery note | `supplier_invoice_*.pdf` (Sri Lanka VAT format) | same | same, or `delivery_note_*.pdf` in S6 |
| Capacity and shift plan | `capacity_shift_plan.xlsx` (+ "OT plan" sheet in S9) | same | same |
| Production / WIP | `hourly_production_report_*.xlsx` | `daily_production_report_*.xlsx` | `daily_packing_log_*.xlsx`, or a photographed `.jpg` |
| Maintenance log | `maintenance_log.xlsx` | `maintenance_log.xlsx` | `maintenance_log.csv` |
| Supplier email (S5 only) | `supplier_email_*.txt` | same | same |
| Working-hours policy | `factory_policy_working_hours.pdf` | same | same |

### Mess levels and defects

- **clean:** no defects.
- **typical:** 2 to 4 of `title_block` (report title rows above the header), `dmy_dates` (DD/MM/YYYY text dates), `names_not_codes` (item code column missing), `unit_variant` (Gross, Box (100), Roll (500 m)), `notes_column` (free-text remarks), `blank_noncritical`, `mixed_currency` (USD supplier invoice).
- **messy:** typical plus 1 to 2 of `scanned_doc` (rotated, blurred, stamped scan with a handwritten note), `two_sheets` (stock split by store), `column_renamed`, `total_row_inside` (subtotal rows inside the data), `missing_critical` (customer PO delivery date "TBC").

`manifest.json` lists the defects selected and, under `defects_applied`, exactly which file and column each one changed.

## How the answer key is computed

`generator/solver.py` (rules in its docstring):

1. One work order per customer PO line, sequenced by due date.
2. BoM explosion with wastage (multi-level for B). Each order takes free stock (OK status minus reserved), then open POs by ETA. The rest is a shortage with a suggested PO to the fastest supplier. Goods are usable the working day after receipt. Quality-hold stock does not count.
3. Forward finite-capacity scheduling: one 8 h shift, Mon to Sat, Sri Lankan holidays off, existing WIP loaded first, down work centres at zero capacity, alternate work centres used when they finish earlier.
4. Overtime only when an order is late, and only within policy (2 h/day, 60 h/week).

Several schedules can be valid, so score schedules on feasibility and lateness, not exact times. Each expected flag has `required: true/false`; the overtime-approval flag is optional. `scoring_notes` in each key gives the tolerances.

Due dates are set from a first, undisrupted solve, then the scenario's disruption is applied. That is why late orders appear only where the scenario causes them.

## Bundle list

| Bundle | Set | Scenario | Mess | Docs | Orders | Shortages | Late WOs | Required flags | Defects | Human baseline |
|---|---|---|---|---|---|---|---|---|---|---|
| `A_S1_clean` | test | Normal order | clean | 12 | 2 | 0 | 0 | - | - | |
| `A_S2_typical` | test | Rush order | typical | 12 | 2 | 0 | 0 | - | mixed_currency, title_block | |
| `A_S3_typical` | test | Material shortage | typical | 11 | 1 | 1 | 0 | shortage | unit_variant, dmy_dates, blank_noncritical, notes_column | yes |
| `A_S4_messy` | test | Machine breakdown | messy | 11 | 1 | 0 | 0 | machine_down | names_not_codes, dmy_dates, unit_variant, notes_column, total_row_inside, scanned_doc | |
| `A_S6_typical` | test | Conflicting documents | typical | 12 | 1 | 0 | 0 | conflict | blank_noncritical, notes_column | |
| `A_S7_clean` | test | Capacity overload | clean | 13 | 3 | 0 | 1 | due_date_risk, capacity_overload | - | |
| `A_S8_messy` | test | Combined shock (shortage + breakdown) | messy | 12 | 2 | 1 | 1 | shortage, machine_down, due_date_risk | dmy_dates, unit_variant, two_sheets | yes |
| `A_S9_typical` | test | Compliance breach (overtime) | typical | 11 | 1 | 0 | 1 | due_date_risk, compliance_breach | dmy_dates, names_not_codes, mixed_currency, notes_column | yes |
| `B_S1_typical` | test | Normal order | typical | 10 | 1 | 0 | 0 | - | names_not_codes, title_block, blank_noncritical | |
| `B_S2_clean` | test | Rush order | clean | 11 | 2 | 0 | 0 | - | - | |
| `B_S3_messy` | test | Material shortage | messy | 10 | 1 | 1 | 0 | shortage, missing_field | mixed_currency, dmy_dates, notes_column, title_block, total_row_inside, missing_critical | |
| `B_S4_typical` | test | Machine breakdown | typical | 10 | 1 | 0 | 0 | machine_down | title_block, unit_variant, dmy_dates | |
| `B_S5_clean` | test | Supplier delay | clean | 13 | 2 | 1 | 0 | supplier_delay | - | yes |
| `C_S1_messy` | test | Normal order | messy | 10 | 1 | 0 | 0 | - | blank_noncritical, mixed_currency, dmy_dates, total_row_inside, scanned_doc | yes |
| `C_S3_typical` | test | Material shortage | typical | 9 | 1 | 1 | 1 | shortage, due_date_risk | dmy_dates, names_not_codes | |
| `C_S5_typical` | test | Supplier delay | typical | 12 | 1 | 1 | 1 | supplier_delay, due_date_risk | unit_variant, notes_column, mixed_currency | |
| `C_S6_clean` | test | Conflicting documents | clean | 10 | 1 | 0 | 0 | conflict | - | yes |
| `C_S8_messy` | test | Combined shock (shortage + breakdown) | messy | 11 | 2 | 1 | 1 | shortage, machine_down, due_date_risk | unit_variant, dmy_dates, title_block, names_not_codes, scanned_doc, column_renamed | |
| `dev_A_S1_clean` | dev | Normal order | clean | 11 | 1 | 0 | 0 | - | - | |
| `dev_B_S3_typical` | dev | Material shortage | typical | 10 | 1 | 1 | 0 | shortage | mixed_currency, unit_variant, blank_noncritical, dmy_dates | |
| `dev_C_S5_messy` | dev | Supplier delay | messy | 13 | 2 | 1 | 2 | supplier_delay, due_date_risk, due_date_risk | blank_noncritical, mixed_currency, dmy_dates, two_sheets, scanned_doc | |
| `scale_A_S1_typical_o1` | scale | Normal order | typical | 11 | 1 | 0 | 0 | - | blank_noncritical, unit_variant, names_not_codes | |
| `scale_A_S1_typical_o3` | scale | Normal order | typical | 13 | 3 | 0 | 0 | - | blank_noncritical, unit_variant, names_not_codes | |
| `scale_A_S1_typical_o5` | scale | Normal order | typical | 15 | 5 | 0 | 0 | - | blank_noncritical, unit_variant, names_not_codes | |

The six bundles marked for the human baseline cover S1, S3, S5, S6, S8 and S9 and all three mess levels. Ajey and Hatheem can swap them.

## Known limits

- In `A_S2_typical` and `B_S2_clean` the rush order is absorbed with policy overtime, so no existing order ends up late. The expected flag is only the optional overtime approval.
- `A_S4_messy` and `B_S4_typical` recover the breakdown on an alternate work centre without lateness.
- The solver is a simple rule-based planner (EDD, forward loading). It is a reference, not an optimum. Hand-check 3 bundles before the benchmark, as planned in TASKS.md.
