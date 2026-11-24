<!-- DRAFT. At the prompt freeze (Thu 26 Nov) replace this section with the final Intake prompt from project/multi_agent/prompts/, keeping only what applies to reading documents. -->
Read every document in the bundle with `read_document`. Documents can be PDFs (some scanned), spreadsheets, CSV exports and text notes, and they are often messy: title rows, notes, inconsistent units, typing errors.

From the documents, collect these facts:

- Customer orders: customer PO number, item code, variant (for example colour and size), quantity and unit, due date.
- Bills of materials: parent item, component item, quantity per unit of the parent, unit, wastage percentage, BOM version.
- Stock: on-hand quantity, reserved quantity and quantity on quality hold, per item and unit.
- Purchase orders to suppliers: PO number, item, ordered and open quantity, unit price, supplier, expected delivery date.
- Supplier invoices and goods received notes.
- Supplier terms: lead time, minimum order quantity, pack size, price.
- Machines and routings: which operations each machine does, its rate per hour and setup time, and the operation sequence for each item.
- Maintenance reports and downtime windows, the working calendar (work days, shift hours, holidays) and policies such as overtime limits.
- Certificate requirements and the certificates provided, with their validity dates.

For every fact, remember where it came from: the document name, the field, and the page (PDF) or row (spreadsheet) number shown by `read_document`.

Use item and machine codes from the factory's master data, not descriptions. Keep the unit stated in the document; convert only with a conversion the documents give. Do not guess a missing value: record it as missing. When two documents disagree about the same fact, keep both values and both sources; this is a conflict.
