"""Master data for the three fictional factories (A apparel, B light assembly, C food packer).

All company, customer and supplier names are invented. Numbers (consumption,
SAM, lead times) are plausible values for the segment, not data from a real firm.
"""
from __future__ import annotations

# item fields: name, uom, kind (fg = finished good, sa = sub-assembly, rm = purchased),
# supplier, alt (alternate supplier), pack (purchase multiple), price (LKR per uom)

A = {
    "profile": "A",
    "profile_name": "Apparel export (cut-make-trim)",
    "system": "odoo",
    "company": {
        "name": "Sapphire Coast Apparel (Pvt) Ltd",
        "address": ["No. 42, Zone 2, Export Processing Zone", "Katunayake 11450, Sri Lanka"],
        "tin": "114583920-7000", "phone": "+94 11 225 4410", "email": "planning@sapphirecoast.example",
    },
    "currency": "USD",
    "customers": {
        "CUS-01": {"name": "Northwind Outfitters Ltd", "address": ["Unit 7, Riverside Park", "Leicester LE4 9JT, United Kingdom"], "currency": "USD", "incoterm": "FOB Colombo", "payment": "LC at sight"},
        "CUS-02": {"name": "Bluefjord Retail AB", "address": ["Hamngatan 18", "411 06 Gothenburg, Sweden"], "currency": "USD", "incoterm": "FOB Colombo", "payment": "TT 60 days after B/L"},
        "CUS-03": {"name": "Kestrel Sportswear GmbH", "address": ["Industriestrasse 21", "90765 Fuerth, Germany"], "currency": "USD", "incoterm": "FCA Katunayake", "payment": "TT 45 days"},
    },
    "suppliers": {
        "SUP-01": {"name": "Ocean Knit Fabrics (Pvt) Ltd", "address": ["Lot 12, Horana BOI Zone", "Horana, Sri Lanka"], "tin": "104772615-7000", "lead_days": 10},
        "SUP-02": {"name": "Lanka Trims & Accessories (Pvt) Ltd", "address": ["220, Negombo Road", "Wattala, Sri Lanka"], "tin": "114220871-7000", "lead_days": 5},
        "SUP-03": {"name": "Colombo Fabric Traders", "address": ["55, 3rd Cross Street", "Colombo 11, Sri Lanka"], "tin": "409118320-7000", "lead_days": 3},
        "SUP-04": {"name": "PackPro Lanka (Pvt) Ltd", "address": ["18, Industrial Estate", "Ekala, Ja-Ela, Sri Lanka"], "tin": "114906233-7000", "lead_days": 3},
        "SUP-05": {"name": "Thread Masters Asia (Pvt) Ltd", "address": ["9, Kandy Road", "Kadawatha, Sri Lanka"], "tin": "114650098-7000", "lead_days": 4},
    },
    "items": {
        # finished goods (style-colour); size breakdown lives on the order line
        "POLO-PIQ-NVY": {"name": "Men's polo shirt, pique, Navy", "uom": "pcs", "kind": "fg", "style": "SC-POLO-261", "colour": "Navy", "pantone": "19-4024 TCX"},
        "POLO-PIQ-WHT": {"name": "Men's polo shirt, pique, White", "uom": "pcs", "kind": "fg", "style": "SC-POLO-261", "colour": "White", "pantone": "11-0601 TCX"},
        "TEE-CRW-BLK": {"name": "Unisex crew-neck T-shirt, Black", "uom": "pcs", "kind": "fg", "style": "SC-TEE-114", "colour": "Black", "pantone": "19-0303 TCX"},
        "TEE-CRW-GRY": {"name": "Unisex crew-neck T-shirt, Grey marl", "uom": "pcs", "kind": "fg", "style": "SC-TEE-114", "colour": "Grey marl", "pantone": "16-3802 TCX"},
        "LEG-LDS-BLK": {"name": "Ladies' leggings, Black", "uom": "pcs", "kind": "fg", "style": "SC-LEG-308", "colour": "Black", "pantone": "19-0303 TCX"},
        # purchased
        "FAB-PIQ-NVY": {"name": "Pique knit fabric 220 gsm, Navy", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 25, "price": 2650.0},
        "FAB-PIQ-WHT": {"name": "Pique knit fabric 220 gsm, White", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 25, "price": 2480.0},
        "FAB-SJ-BLK": {"name": "Single jersey 180 gsm, Black", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 25, "price": 2150.0},
        "FAB-SJ-GRY": {"name": "Single jersey 180 gsm, Grey marl", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 25, "price": 2240.0},
        "FAB-SPX-BLK": {"name": "Cotton spandex jersey 240 gsm, Black", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 25, "price": 3120.0},
        "RIB-COL-NVY": {"name": "Flat-knit collar, Navy", "uom": "pcs", "kind": "rm", "cat": "Trims", "supplier": "SUP-02", "alt": None, "pack": 100, "price": 62.0},
        "RIB-COL-WHT": {"name": "Flat-knit collar, White", "uom": "pcs", "kind": "rm", "cat": "Trims", "supplier": "SUP-02", "alt": None, "pack": 100, "price": 58.0},
        "RIB-1X1-BLK": {"name": "1x1 rib fabric, Black", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 5, "price": 2380.0},
        "RIB-1X1-GRY": {"name": "1x1 rib fabric, Grey marl", "uom": "kg", "kind": "rm", "cat": "Fabric", "supplier": "SUP-01", "alt": "SUP-03", "pack": 5, "price": 2420.0},
        "BTN-4H-15L": {"name": "Button 4-hole 15L polyester", "uom": "pcs", "kind": "rm", "cat": "Trims", "supplier": "SUP-02", "alt": None, "pack": 1440, "price": 1.6},
        "ELS-30MM": {"name": "Waistband elastic 30 mm", "uom": "m", "kind": "rm", "cat": "Trims", "supplier": "SUP-02", "alt": None, "pack": 50, "price": 38.0},
        "THR-PL-40": {"name": "Sewing thread poly 40/2, 5000 m cone", "uom": "cone", "kind": "rm", "cat": "Thread", "supplier": "SUP-05", "alt": None, "pack": 12, "price": 520.0},
        "LBL-MAIN": {"name": "Woven main label", "uom": "pcs", "kind": "rm", "cat": "Labels", "supplier": "SUP-02", "alt": None, "pack": 1000, "price": 4.2},
        "LBL-CARE": {"name": "Printed care label", "uom": "pcs", "kind": "rm", "cat": "Labels", "supplier": "SUP-02", "alt": None, "pack": 1000, "price": 1.9},
        "TAG-HNG": {"name": "Hang tag with barcode", "uom": "pcs", "kind": "rm", "cat": "Labels", "supplier": "SUP-02", "alt": None, "pack": 1000, "price": 6.5},
        "PLY-BAG": {"name": "Polybag 30x40 cm, printed warning", "uom": "pcs", "kind": "rm", "cat": "Packaging", "supplier": "SUP-04", "alt": None, "pack": 500, "price": 3.1},
        "CTN-5PLY": {"name": "Export carton 5-ply 60x40x30", "uom": "pcs", "kind": "rm", "cat": "Packaging", "supplier": "SUP-04", "alt": None, "pack": 25, "price": 310.0},
    },
    # BoM per finished item: component -> (qty per unit, wastage %, consumed in operation)
    "boms": {
        "POLO-PIQ-NVY": {"version": "v3", "lines": [("FAB-PIQ-NVY", 0.255, 5, "Cutting"), ("RIB-COL-NVY", 1, 2, "Sewing"), ("BTN-4H-15L", 3, 3, "Sewing"), ("THR-PL-40", 0.03, 5, "Sewing"), ("LBL-MAIN", 1, 1, "Sewing"), ("LBL-CARE", 1, 1, "Sewing"), ("TAG-HNG", 1, 1, "Finishing and packing"), ("PLY-BAG", 1, 1, "Finishing and packing"), ("CTN-5PLY", 1 / 24, 0, "Finishing and packing")]},
        "POLO-PIQ-WHT": {"version": "v3", "lines": [("FAB-PIQ-WHT", 0.255, 5, "Cutting"), ("RIB-COL-WHT", 1, 2, "Sewing"), ("BTN-4H-15L", 3, 3, "Sewing"), ("THR-PL-40", 0.03, 5, "Sewing"), ("LBL-MAIN", 1, 1, "Sewing"), ("LBL-CARE", 1, 1, "Sewing"), ("TAG-HNG", 1, 1, "Finishing and packing"), ("PLY-BAG", 1, 1, "Finishing and packing"), ("CTN-5PLY", 1 / 24, 0, "Finishing and packing")]},
        "TEE-CRW-BLK": {"version": "v2", "lines": [("FAB-SJ-BLK", 0.18, 6, "Cutting"), ("RIB-1X1-BLK", 0.012, 6, "Cutting"), ("THR-PL-40", 0.02, 5, "Sewing"), ("LBL-MAIN", 1, 1, "Sewing"), ("LBL-CARE", 1, 1, "Sewing"), ("TAG-HNG", 1, 1, "Finishing and packing"), ("PLY-BAG", 1, 1, "Finishing and packing"), ("CTN-5PLY", 1 / 36, 0, "Finishing and packing")]},
        "TEE-CRW-GRY": {"version": "v2", "lines": [("FAB-SJ-GRY", 0.18, 6, "Cutting"), ("RIB-1X1-GRY", 0.012, 6, "Cutting"), ("THR-PL-40", 0.02, 5, "Sewing"), ("LBL-MAIN", 1, 1, "Sewing"), ("LBL-CARE", 1, 1, "Sewing"), ("TAG-HNG", 1, 1, "Finishing and packing"), ("PLY-BAG", 1, 1, "Finishing and packing"), ("CTN-5PLY", 1 / 36, 0, "Finishing and packing")]},
        "LEG-LDS-BLK": {"version": "v1", "lines": [("FAB-SPX-BLK", 0.22, 5, "Cutting"), ("ELS-30MM", 0.75, 3, "Sewing"), ("THR-PL-40", 0.025, 5, "Sewing"), ("LBL-MAIN", 1, 1, "Sewing"), ("LBL-CARE", 1, 1, "Sewing"), ("TAG-HNG", 1, 1, "Finishing and packing"), ("PLY-BAG", 1, 1, "Finishing and packing"), ("CTN-5PLY", 1 / 48, 0, "Finishing and packing")]},
    },
    # routing per style: (seq, operation, primary work centre, std minutes per unit, setup min)
    "routings": {
        "SC-POLO-261": [(10, "Cutting", "CUT-01", 0.9, 60), (20, "Sewing", "LINE-2", 18.5, 120), (30, "Finishing and packing", "PACK-01", 2.5, 30)],
        "SC-TEE-114": [(10, "Cutting", "CUT-01", 0.6, 45), (20, "Sewing", "LINE-1", 9.2, 90), (30, "Finishing and packing", "PACK-01", 1.8, 30)],
        "SC-LEG-308": [(10, "Cutting", "CUT-01", 0.7, 45), (20, "Sewing", "LINE-3", 11.4, 90), (30, "Finishing and packing", "PACK-01", 1.6, 30)],
    },
    # sewing breakdown of the operation bulletin (operation, machine type, SMV); sums to the routing SAM
    "ob": {
        "SC-POLO-261": [("Join shoulder", "4T Overlock", 0.55), ("Attach placket", "SNLS", 1.45), ("Placket top stitch", "SNLS", 1.10), ("Box placket close", "SNLS", 1.30), ("Attach collar", "SNLS", 1.40), ("Collar top stitch", "SNLS", 0.95), ("Attach main + care label", "SNLS", 0.60), ("Attach sleeve", "4T Overlock", 1.20), ("Sleeve hem", "Flatlock", 0.90), ("Side seam with side slit", "4T Overlock", 1.60), ("Side slit tack", "SNLS", 0.85), ("Bottom hem", "Flatlock", 0.95), ("Buttonhole x3", "Buttonhole", 1.15), ("Button attach x3", "Button sew", 1.00), ("Trim threads + inline check", "Manual", 3.50)],
        "SC-TEE-114": [("Join shoulder", "4T Overlock", 0.45), ("Neck rib close", "SNLS", 0.40), ("Attach neck rib", "4T Overlock", 0.85), ("Neck top stitch", "Flatlock", 0.75), ("Back tape", "SNLS", 0.70), ("Attach main + care label", "SNLS", 0.50), ("Attach sleeve", "4T Overlock", 1.10), ("Side seam", "4T Overlock", 1.25), ("Sleeve hem", "Flatlock", 0.80), ("Bottom hem", "Flatlock", 0.80), ("Trim threads + inline check", "Manual", 1.60)],
        "SC-LEG-308": [("Join inseam", "4T Overlock", 1.40), ("Crotch gusset attach", "4T Overlock", 1.30), ("Join side seam", "4T Overlock", 1.20), ("Elastic join", "SNLS", 0.60), ("Attach waistband", "4T Overlock", 1.70), ("Waistband top stitch", "Flatlock", 1.30), ("Attach main + care label", "SNLS", 0.50), ("Leg hem", "Flatlock", 1.40), ("Trim threads + inline check", "Manual", 2.00)],
    },
    # work centres: units = operators or machines, eff = planned efficiency
    "work_centres": {
        "CUT-01": {"name": "Cutting room table 1", "type": "Cutting", "units": 4, "eff": 0.85, "alts": ["CUT-02"]},
        "CUT-02": {"name": "Cutting room table 2", "type": "Cutting", "units": 3, "eff": 0.80, "alts": []},
        "LINE-1": {"name": "Sewing line 1", "type": "Sewing", "units": 26, "eff": 0.62, "alts": ["LINE-2", "LINE-3"]},
        "LINE-2": {"name": "Sewing line 2", "type": "Sewing", "units": 25, "eff": 0.60, "alts": ["LINE-1", "LINE-3"]},
        "LINE-3": {"name": "Sewing line 3", "type": "Sewing", "units": 22, "eff": 0.55, "alts": ["LINE-1", "LINE-2"]},
        "PACK-01": {"name": "Finishing and packing", "type": "Finishing", "units": 12, "eff": 0.75, "alts": []},
    },
    "key_machine": {"LINE-2": "BH-201 Buttonhole machine (only buttonhole machine on line 2)", "LINE-1": "OL-114 4T overlock", "LINE-3": "FL-305 Flatlock", "CUT-01": "CK-01 Straight knife cutter"},
    "order_items": [["POLO-PIQ-NVY", "POLO-PIQ-WHT"], ["TEE-CRW-BLK", "TEE-CRW-GRY"], ["LEG-LDS-BLK"]],
    "qty_range": (1200, 3600, 120),
    "sizes": ["S", "M", "L", "XL", "XXL"],
    "size_ratio": [1, 3, 3, 2, 1],
    "unit_price": {"POLO-PIQ-NVY": 6.85, "POLO-PIQ-WHT": 6.60, "TEE-CRW-BLK": 3.40, "TEE-CRW-GRY": 3.55, "LEG-LDS-BLK": 5.20},
    "locations": {"Fabric": "WH/Stock/Fabric Store", "Trims": "WH/Stock/Trims Store", "Thread": "WH/Stock/Trims Store", "Labels": "WH/Stock/Trims Store", "Packaging": "WH/Stock/Packing Store"},
}

B = {
    "profile": "B",
    "profile_name": "Light assembly (steel furniture)",
    "system": "sapb1",
    "company": {
        "name": "Meridian Steelcraft (Pvt) Ltd",
        "address": ["No. 61, Industrial Estate Road", "Ekala, Ja-Ela 11350, Sri Lanka"],
        "tin": "114702288-7000", "phone": "+94 11 223 8070", "email": "ppc@meridiansteel.example",
    },
    "currency": "LKR",
    "customers": {
        "CUS-11": {"name": "Atlas Office Interiors (Pvt) Ltd", "address": ["14, Ward Place", "Colombo 07, Sri Lanka"], "currency": "LKR", "incoterm": "Delivered", "payment": "Credit 30 days"},
        "CUS-12": {"name": "Harbourview Hotels Ltd", "address": ["88, Galle Road", "Colombo 03, Sri Lanka"], "currency": "LKR", "incoterm": "Delivered", "payment": "Credit 45 days"},
        "CUS-13": {"name": "Metro Warehousing Solutions (Pvt) Ltd", "address": ["301, Biyagama Road", "Kelaniya, Sri Lanka"], "currency": "LKR", "incoterm": "Ex works", "payment": "50% advance, balance on delivery"},
    },
    "suppliers": {
        "SUP-11": {"name": "Lanka Steel Distributors (Pvt) Ltd", "address": ["412, Sri Sangaraja Mawatha", "Colombo 10, Sri Lanka"], "tin": "114339027-7000", "lead_days": 6},
        "SUP-12": {"name": "Prime Coatings Lanka (Pvt) Ltd", "address": ["27, Station Road", "Ragama, Sri Lanka"], "tin": "114877150-7000", "lead_days": 4},
        "SUP-13": {"name": "Eastern Castors & Hardware", "address": ["5, Old Moor Street", "Colombo 12, Sri Lanka"], "tin": "409227714-7000", "lead_days": 8},
        "SUP-14": {"name": "Comfort Foam Industries (Pvt) Ltd", "address": ["Lot 4, Makandura Industrial Zone", "Gonawila, Sri Lanka"], "tin": "114512006-7000", "lead_days": 5},
        "SUP-15": {"name": "BoxCraft Packaging (Pvt) Ltd", "address": ["73, Hendala Road", "Wattala, Sri Lanka"], "tin": "114655390-7000", "lead_days": 3},
        "SUP-16": {"name": "Ceylon Metal Mart", "address": ["19, Prince Street", "Colombo 11, Sri Lanka"], "tin": "409551302-7000", "lead_days": 2},
    },
    "items": {
        "CHR-OFF-01": {"name": "Office chair, steel frame, fabric seat", "uom": "pcs", "kind": "fg"},
        "SHF-RCK-05": {"name": "Rack shelving unit, 5 tier, 1800 mm", "uom": "pcs", "kind": "fg"},
        "SA-CHR-FRM": {"name": "Chair frame, welded and coated", "uom": "pcs", "kind": "sa"},
        "SA-CHR-SEAT": {"name": "Chair seat, upholstered", "uom": "pcs", "kind": "sa"},
        "SA-UPR-1800": {"name": "Upright 1800 mm, coated", "uom": "pcs", "kind": "sa"},
        "SA-SHF-900": {"name": "Shelf panel 900 x 400, coated", "uom": "pcs", "kind": "sa"},
        "STL-TUB-25": {"name": "MS round tube 25 mm x 1.2 mm", "uom": "m", "kind": "rm", "cat": "Steel", "supplier": "SUP-11", "alt": "SUP-16", "pack": 6, "price": 720.0},
        "STL-SHT-2": {"name": "CR sheet 2.0 mm", "uom": "kg", "kind": "rm", "cat": "Steel", "supplier": "SUP-11", "alt": "SUP-16", "pack": 50, "price": 395.0},
        "STL-SHT-1": {"name": "CR sheet 1.0 mm", "uom": "kg", "kind": "rm", "cat": "Steel", "supplier": "SUP-11", "alt": "SUP-16", "pack": 50, "price": 410.0},
        "STL-ANG-40": {"name": "Slotted angle 40 x 40 x 2 mm", "uom": "m", "kind": "rm", "cat": "Steel", "supplier": "SUP-11", "alt": "SUP-16", "pack": 3, "price": 860.0},
        "PWD-BLK": {"name": "Epoxy powder, matt black", "uom": "kg", "kind": "rm", "cat": "Coating", "supplier": "SUP-12", "alt": None, "pack": 20, "price": 2950.0},
        "PWD-GRY": {"name": "Epoxy powder, RAL 7035 grey", "uom": "kg", "kind": "rm", "cat": "Coating", "supplier": "SUP-12", "alt": None, "pack": 20, "price": 2880.0},
        "CST-50": {"name": "Twin-wheel castor 50 mm", "uom": "pcs", "kind": "rm", "cat": "Hardware", "supplier": "SUP-13", "alt": "SUP-16", "pack": 100, "price": 185.0},
        "GAS-LFT": {"name": "Gas lift cylinder class 3", "uom": "pcs", "kind": "rm", "cat": "Hardware", "supplier": "SUP-13", "alt": None, "pack": 20, "price": 1450.0},
        "SCR-M6-20": {"name": "Screw M6 x 20 hex socket", "uom": "pcs", "kind": "rm", "cat": "Hardware", "supplier": "SUP-13", "alt": "SUP-16", "pack": 100, "price": 9.5},
        "BLT-M8-16": {"name": "Bolt and nut M8 x 16", "uom": "pcs", "kind": "rm", "cat": "Hardware", "supplier": "SUP-13", "alt": "SUP-16", "pack": 100, "price": 14.0},
        "PLY-SEAT": {"name": "Moulded plywood seat shell", "uom": "pcs", "kind": "rm", "cat": "Upholstery", "supplier": "SUP-14", "alt": None, "pack": 10, "price": 1180.0},
        "FOAM-40": {"name": "PU foam pad 40 mm, seat", "uom": "pcs", "kind": "rm", "cat": "Upholstery", "supplier": "SUP-14", "alt": None, "pack": 10, "price": 640.0},
        "FAB-UPH-BLK": {"name": "Upholstery fabric, black mesh", "uom": "m", "kind": "rm", "cat": "Upholstery", "supplier": "SUP-14", "alt": None, "pack": 50, "price": 980.0},
        "CTN-CHR": {"name": "Carton, chair", "uom": "pcs", "kind": "rm", "cat": "Packaging", "supplier": "SUP-15", "alt": None, "pack": 25, "price": 410.0},
        "CTN-SHF": {"name": "Carton, shelving flat pack", "uom": "pcs", "kind": "rm", "cat": "Packaging", "supplier": "SUP-15", "alt": None, "pack": 25, "price": 520.0},
    },
    "boms": {
        "CHR-OFF-01": {"version": "R2", "lines": [("SA-CHR-FRM", 1, 0, "Assembly"), ("SA-CHR-SEAT", 1, 0, "Assembly"), ("CST-50", 5, 1, "Assembly"), ("GAS-LFT", 1, 0, "Assembly"), ("SCR-M6-20", 12, 2, "Assembly"), ("CTN-CHR", 1, 0, "Packing")]},
        "SA-CHR-FRM": {"version": "R2", "lines": [("STL-TUB-25", 3.2, 4, "Laser cutting"), ("STL-SHT-2", 1.1, 6, "Laser cutting"), ("PWD-BLK", 0.25, 8, "Powder coating")]},
        "SA-CHR-SEAT": {"version": "R1", "lines": [("PLY-SEAT", 1, 1, "Assembly"), ("FOAM-40", 1, 1, "Assembly"), ("FAB-UPH-BLK", 0.9, 5, "Assembly")]},
        "SHF-RCK-05": {"version": "R1", "lines": [("SA-UPR-1800", 4, 0, "Packing"), ("SA-SHF-900", 5, 0, "Packing"), ("BLT-M8-16", 20, 2, "Packing"), ("CTN-SHF", 1, 0, "Packing")]},
        "SA-UPR-1800": {"version": "R1", "lines": [("STL-ANG-40", 1.8, 3, "Laser cutting"), ("PWD-GRY", 0.12, 8, "Powder coating")]},
        "SA-SHF-900": {"version": "R1", "lines": [("STL-SHT-1", 3.4, 5, "Laser cutting"), ("PWD-GRY", 0.15, 8, "Powder coating")]},
    },
    "routings": {
        "CHR-OFF-01": [(10, "Laser cutting", "LAS-01", 4.0, 45), (20, "Welding", "WLD-01", 9.0, 30), (30, "Powder coating", "PWC-01", 3.0, 60), (40, "Assembly", "ASM-01", 12.0, 20), (50, "Packing", "PCK-01", 3.0, 15)],
        "SHF-RCK-05": [(10, "Laser cutting", "LAS-01", 6.5, 45), (20, "Welding", "WLD-01", 5.0, 30), (30, "Powder coating", "PWC-01", 4.5, 60), (40, "Packing", "PCK-01", 4.0, 15)],
    },
    "work_centres": {
        "LAS-01": {"name": "Fibre laser cutter 3 kW", "type": "Laser cutting", "units": 1, "eff": 0.85, "alts": ["PRS-01"]},
        "PRS-01": {"name": "Mechanical press 80 t + saw", "type": "Laser cutting", "units": 1, "eff": 0.55, "alts": []},
        "WLD-01": {"name": "MIG welding bay A (3 stations)", "type": "Welding", "units": 3, "eff": 0.80, "alts": ["WLD-02"]},
        "WLD-02": {"name": "MIG welding bay B (2 stations)", "type": "Welding", "units": 2, "eff": 0.75, "alts": []},
        "PWC-01": {"name": "Powder coating line + curing oven", "type": "Powder coating", "units": 1, "eff": 0.90, "alts": ["PWC-02"]},
        "PWC-02": {"name": "Manual powder booth + batch oven", "type": "Powder coating", "units": 1, "eff": 0.45, "alts": []},
        "ASM-01": {"name": "Assembly bench (6 fitters)", "type": "Assembly", "units": 6, "eff": 0.80, "alts": []},
        "PCK-01": {"name": "Packing area (3 packers)", "type": "Packing", "units": 3, "eff": 0.85, "alts": []},
    },
    "key_machine": {"PWC-01": "PWC-01 Curing oven burner", "LAS-01": "LAS-01 Laser source", "WLD-01": "WLD-01 Welding power source #2"},
    "order_items": [["CHR-OFF-01"], ["SHF-RCK-05"]],
    "qty_range": (150, 600, 10),
    "unit_price": {"CHR-OFF-01": 28500.0, "SHF-RCK-05": 21900.0},
    "warehouses": {"Steel": "01 - Raw Material", "Coating": "02 - Paint Store", "Hardware": "01 - Raw Material", "Upholstery": "03 - Upholstery", "Packaging": "04 - Packing"},
}

C = {
    "profile": "C",
    "profile_name": "Food packer (tea)",
    "system": "tally",
    "company": {
        "name": "Hill Country Tea Packers (Pvt) Ltd",
        "address": ["No. 7, Katugastota Road", "Kandy 20000, Sri Lanka"],
        "tin": "114390451-7000", "phone": "+94 81 220 9135", "email": "stores@hillcountrytea.example",
    },
    "currency": "LKR",
    "customers": {
        "CUS-21": {"name": "Gulf Pearl Foodstuff Trading LLC", "address": ["Warehouse 14, Al Quoz Industrial 3", "Dubai, United Arab Emirates"], "currency": "USD", "incoterm": "FOB Colombo", "payment": "TT 30 days"},
        "CUS-22": {"name": "Lakeside Supermarkets (Pvt) Ltd", "address": ["210, High Level Road", "Nugegoda, Sri Lanka"], "currency": "LKR", "incoterm": "Delivered", "payment": "Credit 30 days"},
        "CUS-23": {"name": "Pacific Rim Tea Importers Pty Ltd", "address": ["3/41 Dock Road", "Port Melbourne VIC 3207, Australia"], "currency": "USD", "incoterm": "FOB Colombo", "payment": "TT 45 days"},
    },
    "suppliers": {
        "SUP-21": {"name": "Highland Tea Brokers (Pvt) Ltd", "address": ["101, Sir Chittampalam A Gardiner Mw", "Colombo 02, Sri Lanka"], "tin": "114208813-7000", "lead_days": 5},
        "SUP-22": {"name": "FlexiPack Laminates (Pvt) Ltd", "address": ["35, Pannipitiya Road", "Battaramulla, Sri Lanka"], "tin": "114760029-7000", "lead_days": 7},
        "SUP-23": {"name": "Kandy Print & Box", "address": ["62, Peradeniya Road", "Kandy, Sri Lanka"], "tin": "409883140-7000", "lead_days": 6},
        "SUP-24": {"name": "BoxCraft Packaging (Pvt) Ltd", "address": ["73, Hendala Road", "Wattala, Sri Lanka"], "tin": "114655390-7000", "lead_days": 3},
        "SUP-25": {"name": "Uva Leaf Traders", "address": ["12, Lower Street", "Badulla, Sri Lanka"], "tin": "409301776-7000", "lead_days": 2},
    },
    "items": {
        "TEA-BOP-100": {"name": "Ceylon black tea BOP 100 g carton", "uom": "pcs", "kind": "fg", "shelf_life_m": 24},
        "TEA-BOP-200": {"name": "Ceylon black tea BOP 200 g carton", "uom": "pcs", "kind": "fg", "shelf_life_m": 24},
        "TEA-GRN-100": {"name": "Ceylon green tea 100 g carton", "uom": "pcs", "kind": "fg", "shelf_life_m": 18},
        "BLK-BOP": {"name": "Bulk black tea BOP grade", "uom": "kg", "kind": "rm", "cat": "Raw tea", "supplier": "SUP-21", "alt": "SUP-25", "pack": 50, "price": 1650.0},
        "BLK-GRN": {"name": "Bulk green tea", "uom": "kg", "kind": "rm", "cat": "Raw tea", "supplier": "SUP-21", "alt": "SUP-25", "pack": 50, "price": 2100.0},
        "FLM-INR-100": {"name": "Inner foil laminate, 100 g pouch", "uom": "m", "kind": "rm", "cat": "Packing material", "supplier": "SUP-22", "alt": None, "pack": 500, "price": 21.0},
        "FLM-INR-200": {"name": "Inner foil laminate, 200 g pouch", "uom": "m", "kind": "rm", "cat": "Packing material", "supplier": "SUP-22", "alt": None, "pack": 500, "price": 26.0},
        "BOX-BOP-100": {"name": "Printed box BOP 100 g", "uom": "pcs", "kind": "rm", "cat": "Packing material", "supplier": "SUP-23", "alt": None, "pack": 1000, "price": 18.5},
        "BOX-BOP-200": {"name": "Printed box BOP 200 g", "uom": "pcs", "kind": "rm", "cat": "Packing material", "supplier": "SUP-23", "alt": None, "pack": 1000, "price": 24.0},
        "BOX-GRN-100": {"name": "Printed box green tea 100 g", "uom": "pcs", "kind": "rm", "cat": "Packing material", "supplier": "SUP-23", "alt": None, "pack": 1000, "price": 19.5},
        "CTN-48": {"name": "Shipper carton, 48 units", "uom": "pcs", "kind": "rm", "cat": "Packing material", "supplier": "SUP-24", "alt": None, "pack": 50, "price": 165.0},
        "CTN-24": {"name": "Shipper carton, 24 units", "uom": "pcs", "kind": "rm", "cat": "Packing material", "supplier": "SUP-24", "alt": None, "pack": 50, "price": 150.0},
    },
    "boms": {
        "TEA-BOP-100": {"version": "B-2026", "lines": [("BLK-BOP", 0.1, 1.5, "Blending"), ("FLM-INR-100", 0.16, 3, "Filling and sealing"), ("BOX-BOP-100", 1, 1, "Filling and sealing"), ("CTN-48", 1 / 48, 0, "Carton packing")]},
        "TEA-BOP-200": {"version": "B-2026", "lines": [("BLK-BOP", 0.2, 1.5, "Blending"), ("FLM-INR-200", 0.21, 3, "Filling and sealing"), ("BOX-BOP-200", 1, 1, "Filling and sealing"), ("CTN-24", 1 / 24, 0, "Carton packing")]},
        "TEA-GRN-100": {"version": "G-2026", "lines": [("BLK-GRN", 0.1, 2, "Blending"), ("FLM-INR-100", 0.16, 3, "Filling and sealing"), ("BOX-GRN-100", 1, 1, "Filling and sealing"), ("CTN-48", 1 / 48, 0, "Carton packing")]},
    },
    "routings": {
        "TEA-BOP-100": [(10, "Blending", "BLD-01", 0.010, 30), (20, "Filling and sealing", "FIL-01", 0.030, 60), (30, "Carton packing", "PKG-01", 0.020, 15)],
        "TEA-BOP-200": [(10, "Blending", "BLD-01", 0.018, 30), (20, "Filling and sealing", "FIL-01", 0.036, 60), (30, "Carton packing", "PKG-01", 0.025, 15)],
        "TEA-GRN-100": [(10, "Blending", "BLD-01", 0.010, 45), (20, "Filling and sealing", "FIL-02", 0.030, 60), (30, "Carton packing", "PKG-01", 0.020, 15)],
    },
    "work_centres": {
        "BLD-01": {"name": "Blending drum 500 kg", "type": "Blending", "units": 1, "eff": 0.85, "alts": []},
        "FIL-01": {"name": "Packing line 1 (auto filler + sealer)", "type": "Filling", "units": 1, "eff": 0.80, "alts": ["FIL-02"]},
        "FIL-02": {"name": "Packing line 2 (semi-auto filler)", "type": "Filling", "units": 1, "eff": 0.65, "alts": ["FIL-01"]},
        "PKG-01": {"name": "Carton packing table (4 packers)", "type": "Carton packing", "units": 4, "eff": 0.80, "alts": []},
    },
    "key_machine": {"FIL-01": "FIL-01 Heat sealer jaw assembly", "FIL-02": "FIL-02 Auger filler motor", "BLD-01": "BLD-01 Drum gearbox"},
    "order_items": [["TEA-BOP-100"], ["TEA-BOP-200"], ["TEA-GRN-100"]],
    "qty_range": (24000, 72000, 2400),
    "unit_price": {"TEA-BOP-100": 1.35, "TEA-BOP-200": 2.45, "TEA-GRN-100": 1.60},
    "godowns": {"Raw tea": "Main Godown", "Packing material": "Packing Material Store"},
}

MASTERS = {"A": A, "B": B, "C": C}


def explode(master: dict, item: str, qty: float, out: dict | None = None, path: tuple = ()) -> dict:
    """Multi-level BoM explosion to purchased items: {code: gross qty incl. wastage}."""
    out = {} if out is None else out
    for comp, per, wst, _op in master["boms"][item]["lines"]:
        need = qty * per * (1 + wst / 100.0)
        if master["items"][comp]["kind"] == "sa":
            explode(master, comp, need, out, path + (comp,))
        else:
            out[comp] = out.get(comp, 0.0) + need
    return out


def routing_for(master: dict, item: str):
    if master["profile"] == "A":
        return master["routings"][master["items"][item]["style"]]
    return master["routings"][item]
