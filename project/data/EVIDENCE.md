# Why each document looks the way it does

_Checked 21 Nov 2026. Every link below was opened and read on that date unless it is marked otherwise._

Our target companies are small and mid-sized Sri Lankan manufacturers. They keep stock and money in an entry-level ERP or accounting package (Odoo, SAP Business One or TallyPrime) and run the shop floor on hand-built Excel sheets. Outside documents (customer POs, supplier invoices) arrive as PDFs in each sender's own layout, and some are scanned. So "the most common format" for each document means one of three things:

1. **A format set by law.** The Sri Lankan VAT tax invoice has particulars fixed by gazette. This is the strongest evidence.
2. **The default export or print layout of the system the company runs.** An Odoo, SAP B1 or Tally user gets the same columns as every other user of that system, so we copy the vendor's documented fields.
3. **The format taught and used across an industry.** The garment operation bulletin and the hourly production report are taught in the ILO's factory training material and on the main garment-industry sites. Retail and apparel PO fields are described the same way by several independent sources.

No source gives a market-share number for a document layout. Where we say "most common", the evidence is either a legal requirement, a vendor's default, or several independent sources describing the same fields. Each section says which kind it is.

| # | Document (file in `inputs/`) | Format we copy | Kind of evidence |
|---|---|---|---|
| 1 | Customer purchase order (`customer_po_*.pdf`) | Buyer's own PDF: header, parties, PO no./date, Incoterm, payment terms, delivery date, item lines; apparel adds a size × colour matrix | Several independent sources agree |
| 2 | Bill of materials (`bom_techpack.xlsx`, `bom_sapb1_export.xlsx`, `bom_tally.xlsx`) | A: apparel tech-pack BoM; B: SAP B1 BoM (multi-level); C: Tally BoM with a unit of manufacture | Vendor defaults + industry practice |
| 3 | Routing / operation bulletin (`routing_*.xlsx`, `operation_bulletin.xlsx`, `process_sheet.xlsx`) | Odoo Operations tab; SAP B1 stages; ILO-style operation bulletin | ILO training material + vendor defaults |
| 4 | Stock report (`stock_on_hand_odoo.xlsx`, `inventory_status_report.xlsx`, `godown_summary_tally.xlsx`) | Odoo stock export; SAP B1 Inventory Status; Tally Godown Summary | Vendor defaults |
| 5 | Our purchase orders to suppliers (`purchase_order_*.pdf`) | Odoo / SAP B1 / Tally PO print | Vendor defaults + general PO practice |
| 6 | Supplier tax invoice / delivery note (`supplier_invoice_*.pdf`, `delivery_note_*.pdf`) | Sri Lankan VAT tax invoice; delivery note with ordered vs delivered qty | **Legal requirement** + GRN practice |
| 7 | Capacity and shift plan (`capacity_shift_plan.xlsx`) | Hand-built sheet: work centres, operators, efficiency, shift, calendar with Sri Lankan holidays | Industry formula + official holiday list |
| 8 | Production / WIP report (`hourly_production_report_*.xlsx`, `daily_production_report_*.xlsx`, `daily_packing_log_*`) | Garment hourly production report; ERP production order status; paper packing log | Industry practice |
| 9 | Maintenance / downtime log (`maintenance_log.*`) | Breakdown register: machine, start/end, category, reason, action, status | Several independent sources agree |
| 10 | Supplier email (`supplier_email_*.txt`) | Plain email | No standard exists |
| 11 | Factory working-hours policy (`factory_policy_working_hours.pdf`) | One-page policy citing the ETI Base Code limits | Buyer code of conduct |

---

## 1. Customer purchase order

**What we generate.** A PDF in the buyer's layout (two layouts, picked per bundle): buyer name and address, supplier (us), PO number, PO date, currency, Incoterm, payment terms, delivery or ex-factory date, item lines with quantity, unit price and amount, and notes on packing. Apparel POs carry a size breakdown (S to XXL) per style and colour. B and C buyers use their own article numbers. Rush orders carry an "URGENT" line.

**Why this format.**
- Sage: "A standard PO includes a unique PO number, buyer and seller details, item descriptions and quantities, payment terms, and delivery information." ([Sage, What is a purchase order](https://www.sage.com/en-us/blog/what-is-a-purchase-order/))
- Wikipedia: most POs "contain little more than a list of the goods or services the buyer desires to purchase, along with price, payment terms, and shipping instructions." ([Purchase order](https://en.wikipedia.org/wiki/Purchase_order))
- Apparel: a production PO "at minimum needs" PO number, style and tech-pack reference, a size and colour matrix, FOB cost, currency and Incoterms, an ex-factory date, payment terms, and inspection/AQL terms. ([Uphance, What is a purchase order](https://uphance.com/blog/what-is-a-purchase-order/))
- "Garment buyers and garment sourcing agents follow a standard formats for purchase order", with style, order quantity, size break-up, price, packing details and shipping address. ([Online Clothing Study, How to make a PO for garment sourcing](https://www.onlineclothingstudy.com/2013/12/how-to-make-purchase-order-po-for.html))
- A PO sheet is "an important document commonly used in garment manufacturing" and covers quantity, size breakdown, colours and shipment date. ([Techpacker, PO sheet in garment manufacturing](https://techpacker.com/blog/manufacturing/what-is-a-purchase-order-sheet-in-garment-manufacturing/))

## 2. Bill of materials

**What we generate.**
- **A (apparel):** a tech-pack BoM sheet per style and colour: Category, Item code, Component, Colour, Placement, Consumption per garment, UoM, Wastage %, Supplier, with style, Pantone and BoM version above.
- **B (light assembly):** a SAP Business One BoM export, one tree per parent. The finished chair has sub-assemblies (frame, seat), each with its own tree, so the BoM is multi-level. Columns: Product No., BOM Type, Quantity, Warehouse, then Type, No., Quantity, UoM, Warehouse, Issue Method (Backflush/Manual), plus a scrap % user-defined field.
- **C (food):** a Tally BoM printout: stock item, name of BoM, "Unit of manufacture: 1,000 pcs", and component lines with godown and quantity (with unit in the same cell).

**Why this format.**
- Odoo 17's BoM has a Components tab (with "Consumed in Operation"), an Operations tab (Operation, Work Center, Default Duration) and a Manuf Lead Time. ([Odoo 17, Bill of materials](https://www.odoo.com/documentation/17.0/applications/inventory_and_mrp/manufacturing/basic_setup/bill_configuration.html))
- SAP Business One BoM window: header Product No., BOM Type, Quantity, Warehouse; lines Type, No., Quantity, UoM, Warehouse, Issue Method. ([SAP B1 9.2, Bill of Materials window](https://help.sap.com/saphelp_sbo92/helpdata/en/ca/3170cd2b4440acb0a8b7a226cfd37f/content.htm); [SAP B1 9.2, Defining BoMs](https://help.sap.com/saphelp_sbo92/helpdata/en/44/f29333df7365fbe10000000a1553f7/content.htm)) _Checked in the earlier planning thread on 19 Nov 2026, not re-opened today._
- Multi-level BoMs are reported level by level: Sage 100 prints "each level indented from the preceding level" ([Sage 100, Indented BoM report](https://help-sage100.na.sage.com/2016/Subsystems/BM/BMREPORT/INDENTED_BOM_RPT_OVR.htm)); Infor lists "the level of the component item, the unit of measure, the source code, and the quantity required" per parent unit ([Infor, Indented costed BoM report](https://docs.infor.com/csb/9.01.x/en-us/csbiolh/mergedprojects/sl_invprod/forms/invtopic/indented_costed_bill_of_material_report.htm)).
- TallyPrime BoM: name of BoM, unit of manufacture, item, godown, quantity, and component types. ([TallyPrime, Manage inventory in manufacturing](https://help.tallysolutions.com/tally-prime/inventory/manage-inventory-in-manufacturing-tally/)) _Checked 19 Nov 2026._
- Apparel tech-pack BoM columns (category, component, description, colour/Pantone, quantity per unit, grouped as fabric, trims, labels, packaging) ([Shopify, How to create a tech pack](https://www.shopify.com/blog/how-to-create-a-tech-pack), checked 19 Nov 2026; the page refused our fetch today), and costing adds consumption and wastage ([Techpacker, Apparel and garment costing](https://www.techpacker.com/blog/apparel-and-garment-costing), checked 19 Nov 2026).

## 3. Routing and operation bulletin

**What we generate.**
- **A:** an Odoo Operations export (Bill of Material, Sequence, Operation, Work Center, Default Duration per unit, Setup), plus a garment **operation bulletin** per style: header (style, line, total SMV, operators, efficiency, target per day) and rows (#, Operation, Machine type, SMV, target per hour at 100% and at planned efficiency, machines required). The SMVs add up to the routing's sewing SAM.
- **B:** SAP B1 routing stages: Product No., Stage, Resource, Run time per unit, Setup.
- **C:** a process sheet with rated machine speed (units per minute) and changeover time, because packing lines are rated by speed rather than minutes per unit.

**Why this format.**
- The ILO's factory training module "Making an operation bulletin" uses exactly these columns: "# | Operations | Machine type | SMV at 100% efficiency | SMV at 60% efficiency | # of machines | # of operator", with Style #, Target/day, Order qty and Total SMV in the header. ([ILO FIT module, via Asia Garment Hub](https://asiagarmenthub.net/resources/2021/sl2-making-an-operation-bulletin-sewing-room-operations-pdfen-1.pdf))
- The OB is "one of the primary IE tools" and lists target output, total SMV, machine description, SMV at 100% and target efficiency, and machines required. ([Online Clothing Study, How to make an operation bulletin](https://www.onlineclothingstudy.com/2012/08/how-to-make-operation-bulletin-for.html)); OBs also carry machine type per operation ([Apparel Resources, Making an operational bulletin](https://apparelresources.com/business-news/manufacturing/industrial-engineering-apparel-manufacturing-vi-making-operational-bulletin/), checked 19 Nov 2026).
- Odoo's Operations tab fields are Operation, Work Center and Default Duration. ([Odoo 17, Bill of materials](https://www.odoo.com/documentation/17.0/applications/inventory_and_mrp/manufacturing/basic_setup/bill_configuration.html))

## 4. Stock report

**What we generate.**
- **A, Odoo:** Location, Product (`[CODE] Name`, the way Odoo shows display names), Internal Reference, Lot/Serial Number, On Hand Quantity, Reserved Quantity, Unit of Measure.
- **B, SAP B1 Inventory Status:** Item No., Item Description, Warehouse, In Stock, Committed, Ordered, Available, UoM.
- **C, Tally Godown Summary:** title block (company, report name, period "1-Apr-26 to …"), items grouped by godown with a total per godown, columns Particulars, Batch, Expiry, Closing Quantity (with the unit inside the cell, as Tally exports it), Rate, Value. Tally items have names, not codes. One raw-tea lot sits in a "Quality Hold" godown and must not be counted.

**Why this format.**
- Tally's Godown Summary shows "consolidated quantity, rate, and value" per godown, with closing balance and inwards/outwards. ([TallyHelp, Godown Summary](https://help.tallysolutions.com/tally-prime/inventory-reports/godown-summary-tally/)); the Stock Summary has options for opening balance, inwards, outwards and closing value ([TallyHelp, Stock Summary FAQ](https://help.tallysolutions.com/stock-summary/)).
- Batch, lot and expiry are what food plants track per lot. ([MRPeasy, Batch records](https://www.mrpeasy.com/blog/batch-records/)); a regulated batch record must hold the lot number and "the identity and weight or measure of each component used" ([21 CFR 111.260](https://www.law.cornell.edu/cfr/text/21/111.260), a US dietary-supplement rule we use only as a reference for lot fields).
- _Not verified today:_ the exact Odoo stock-export and SAP B1 Inventory Status column names come from the vendors' standard screens as we know them; we did not open a vendor page for them in this check.

## 5. Our purchase orders to suppliers

**What we generate.** The open POs the factory has already sent. A uses Odoo's print ("Purchase Order #P00xxx", Order Deadline, Expected Arrival, Description, Taxes, Date Req., Qty, Unit Price, Amount). B uses SAP B1's (No., Posting Date, Delivery Date, Item No., Quantity, Price, Total (LC)). C uses Tally's voucher layout (Order No., Dated, Mode/Terms of Payment, Due on, Description of Goods, Quantity, Rate, per, Amount).

**Why this format.**
- Odoo 17's purchase dates: the order deadline ("the date by which the order should be confirmed"), the expected arrival / receipt date (confirmation date plus vendor lead time), and the vendor's "Delivery Lead Time". ([Odoo 17, Lead times](https://www.odoo.com/documentation/17.0/applications/inventory_and_mrp/inventory/warehouses_storage/replenishment/lead_times.html))
- The general PO fields are those in section 1 ([Sage](https://www.sage.com/en-us/blog/what-is-a-purchase-order/)).

## 6. Supplier tax invoice and delivery note

**What we generate.** A "TAX INVOICE" with supplier and purchaser name, address and TIN, a serial invoice number, date of invoice, date of supply, place of supply, description, quantity, value excluding VAT, VAT at 18%, total including VAT, the amount in words and the mode of payment. If the bundle has the `mixed_currency` defect, prices are in USD with an LKR equivalent line. In C's conflict scenario the supplier document is a **delivery note** with ordered and delivered quantities.

**Why this format.**
- This is a legal format. Gazette 2463/05 (17 Nov 2025) sets the particulars: supplier and purchaser TIN, name, address and telephone; serial number; invoice date; place and date of supply; description; quantity; value excluding VAT, VAT, total, total in words; and payment mode. ([KPMG Sri Lanka, VAT invoice format](https://assets.kpmg.com/content/dam/kpmg/lk/pdf/kpmg-tax-news/2025/november-2025/Tax%20Flash%20News%20-%20Value%20Added%20Tax%20(VAT)%20Invoice%20Format.pdf))
- The revised format was postponed from 1 July 2026 to 1 October 2026 by Gazette 2500/106. ([KPMG TaxNewsFlash, 18 Aug 2026](https://kpmg.com/us/en/taxnewsflash/news/2026/08/tnf-sri-lanka-postponement-of-revised-vat-invoice-format.html)) So it applies to our December 2026 documents.
- A third-party checklist lists the mandatory fields, including "LKR values for foreign-currency invoices", and says telephone, place of supply, amount in words and payment mode are optional. ([Invoice Data Extraction, Sri Lanka tax invoice format](https://invoicedataextraction.com/blog/sri-lanka-tax-invoice-format)) We print all of them, mandatory and optional.
- Delivery note / goods received: a receipt should hold a GRN number, date, PO reference, supplier, delivery docket reference, item and unit, and "ordered/delivered/accepted/rejected/outstanding quantities". ([HashMicro, Goods received note](https://www.hashmicro.com/au/blog/goods-received-note/))
- _Assumption:_ the 18% VAT rate was not checked in this pass.

## 7. Capacity and shift plan

**What we generate.** A hand-built workbook. Sheet "Work centres": ID, name, type, operators or machines, planned efficiency %, shift (08:00 to 17:00 with a one-hour lunch), regular hours per day, working days (Mon to Sat), OT allowed (2 h/day), alternate for, status. Sheet "Calendar": 6 weeks of dates with Sundays and public holidays marked. In S9 bundles a third sheet, "OT plan", holds the production manager's proposal that breaks the policy.

**Why this format.**
- Line capacity is computed the way garment IE teams do it: "Hourly Production Target of a line = ((60 × No. of operators working in a line × Line efficiency %) / (Garment SAM))". ([Online Clothing Study, Hourly production target](https://www.onlineclothingstudy.com/2016/12/how-to-calculate-hourly-production.html)) Our solver uses the same terms (operators × efficiency ÷ minutes per unit).
- Holidays in the horizon: 24 Nov 2026 Il Full Moon Poya, 23 Dec 2026 Unduvap Full Moon Poya, 25 Dec 2026 Christmas. ([Ada Derana, 2026 holiday calendar](https://www.adaderana.lk/news/116465/sri-lankas-2026-holiday-calendar-heres-the-full-list-); [Office Holidays, Sri Lanka 2026](https://www.officeholidays.com/countries/sri_lanka/index.php))
- Dates are day-month-year because "only Day-month-year format … are used in governmental and other English documents of official contexts" in Sri Lanka. ([Wikipedia, Date format by country](https://en.wikipedia.org/wiki/Date_format_by_country)) ERP exports keep their own formats (Odoo ISO, SAP B1 dd.mm.yyyy, Tally 1-Dec-26), and the `dmy_dates` defect mixes them.

## 8. Production and WIP report

**What we generate.**
- **A:** the garment **hourly production report** for yesterday: Line, Style, Buyer, MO, Operators, SAM, Target/day, Target/hr, one column per hour, Total, Eff %. A second sheet lists open MOs with order, cut, sewn, packed and balance to sew. That balance is the load each line still carries.
- **B:** a daily production report per SAP production order: Prod. Order, Item, Stage, Resource, Planned, Completed, Rejected, Remaining, remaining time.
- **C:** a daily packing log, as Excel when clean, and as a **photographed paper form** at typical and messy levels.

**Why this format.**
- The hourly production report is "the basic reporting tool used by garment factories", also called the "Production by Hour Report", and carries the daily target, manpower and product SAM. ([Online Clothing Study, Hourly production report](https://onlineclothingstudy.com/2015/11/hourly-production-report-basic-tool-to.html))
- Line efficiency and targets use the formula in section 7.

## 9. Maintenance and downtime log

**What we generate.** A breakdown register (Excel for A and B, CSV for C): Date, Machine ID, Work centre, Machine/part, Breakdown start, Breakdown end, Downtime (h), Category, Reason, Action/remarks, Expected return, Status, Reported by. Every bundle has one closed past breakdown as noise. S4 and S8 bundles add an open breakdown on the bottleneck.

**Why this format.** Four independent templates list the same core fields:
- A log "should capture equipment ID, downtime category, start/stop times, root cause, and corrective action", plus date, shift and reported/resolved by. ([Alpha Software, Machine downtime log template](https://www.alphasoftware.com/blog/machine-downtime-log-template-fields-pdfs-digital-options))
- Event ID, plant/line/machine, start, end, reason code, planned or not, order, shift. ([Fabrico, Downtime event data model](https://www.fabrico.io/blog/downtime-event-data-model/))
- Machine, Reason, Start Time, Downtime Minutes, Shift, Reporting Operator. ([MangoApps, Machine downtime log](https://www.mangoapps.com/templates/power_tables/machine-downtime-log))
- Start, end and duration, machine, planned or unplanned, cause, impact, corrective actions. ([ProjectManager, Downtime report](https://www.projectmanager.com/blog/downtime-report))

## 10. Supplier email

**What we generate.** A plain-text email (From, To, Date, Subject "RE: PO … - revised delivery date") giving the old and new delivery dates and a reason.

**Why this format.** There is no standard. Delay notices arrive as free-text emails, which is the point: the Intake agent has to pull the PO number, item and new date out of prose.

## 11. Factory working-hours policy

**What we generate.** A one-page policy: 48 regular hours a week, voluntary overtime paid at not less than 125%, at most 2 h a day and 12 h a week of overtime, at most 60 hours in any 7 days, and one day off in seven. S9 checks plans against it.

**Why this format.** Sri Lankan exporters sign their buyers' codes of conduct, and the ETI Base Code is the common reference:
- Clause 6: contracted hours "shall not exceed 48 hours per week", "All overtime shall be voluntary", total hours "shall not exceed 60 hours in any seven day period", and "at least one day off in every 7 day period". ([ETI Base Code clause 6](https://ethicaltrade.org/eti-base-code/6-working-hours-are-not-excessive))
- ETI guidance recommends overtime pay of "not less than 125% of the regular rate of pay". ([ETI clause 6 guidance](https://www.ethicaltrade.org/sites/default/files/shared_resources/eti_base_code_clause_6_guidance_english_0.pdf))

---

## What is invented

All companies, people, addresses, TINs, item codes and prices are made up. Consumption rates, SAMs, lead times and efficiencies are plausible values for the segment, not data from a real factory. The USD/LKR rate is fixed at 300.00 to keep the numbers simple.

## Pages that could not be used

- Shopify tech pack guide and the CBSL bank-holiday page refused automated fetches today (the Shopify page was read on 19 Nov).
- The Odoo work-centre capacity page could not be fetched (robots.txt).
- SafetyCulture's downtime form redirects to another site and was not followed.
