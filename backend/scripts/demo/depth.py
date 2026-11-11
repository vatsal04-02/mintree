"""Depth steps: top up the modules the story steps (steps.py) and the volume steps (scale.py) leave thin, so every module has 10-15 or more meaningful
primary records. Same rules as the rest of the demo: every business record is created by calling the real HTTP API (permissions, validation, approvals,
ledger, audit, outbox); the only direct database access here is READING ids and reference numbers to link documents to the records they describe.

    stocktakes · purchase requests · RFQs and quotations · purchase returns · formulations and projects · deviations and CAPA · maintenance ·
    customer contacts · documents (uploads, versions, links)
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import engine
from scripts.demo.core import Ctx, step
from scripts.demo.steps import buy, inspect_batch, item_ids, me, receive, sup_ids


def rows(sql: str, **params) -> list[tuple]:
    with Session(engine) as s:
        return [tuple(r) for r in s.execute(text(sql), params)]


# ================================================================== purchase requests, RFQs, quotations, purchase returns
PR_SPECS = [  # (item code, qty, note, outcome)
    ("RM-ALOE", "80", "Aloe restock for the Oud wash run", "approved"), ("RM-SURF", "250", "Surfactant base for two bulk batches", "approved"),
    ("PM-BOTTLE-200", "800", "Bottles for the next body wash filling", "submitted"), ("RM-SHEA", "60", "Shea butter for the Olive body butter", "approved"),
    ("PM-JAR-150", "600", "Jars with wooden caps", "rejected"), ("RM-OLIVE", "90", "Cold-pressed olive oil", "submitted"),
    ("PM-CARTON-12", "400", "Shipper cartons: marketplace volume", "draft"), ("RM-KAOLIN", "70", "Kaolin for the Detan mask", "approved"),
    ("RM-VITE", "12", "Vitamin E oil: replace the rejected lot", "submitted"),
]


@step("depth_procurement")
def depth_procurement(c: Ctx) -> dict:
    """Nine more purchase requests in every state (draft, awaiting approval, approved, rejected); eleven more RFQs (sent, quotations in, awarded to a PO);
    four more QC-rejected receipts returned to the supplier."""
    items, sup = item_ids(c), sup_ids(c)
    buyer, admin = c.u("buyer"), c.u("admin")
    out: dict = {"prs": [], "rfqs": [], "returns": []}
    # ---- purchase requests
    for i, (code, qty, note, outcome) in enumerate(PR_SPECS):
        with c.days_ago(28 - 3 * i):
            pr = c.ok(buyer.post("/api/procurement/purchase-requests", json={"items": [{"item_id": items[code], "qty": qty}], "required_by": c.d(14), "notes": f"Demo: {note}"}))
            if outcome != "draft":
                pr = c.ok(buyer.post(f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]}))
            if outcome == "approved":
                pr = c.ok(admin.post(f"/api/procurement/purchase-requests/{pr['id']}/approve", json={"expected_version": pr["version"]}))
            elif outcome == "rejected":
                pr = c.ok(admin.post(f"/api/procurement/purchase-requests/{pr['id']}/reject", json={"expected_version": pr["version"]}))
        out["prs"].append(pr["id"])
    # ---- RFQs: (item code, qty, suppliers, quotation prices by supplier, stage) ; stage: sent | quoted | awarded
    rfq_specs = [
        ("RM-ALOE", "150", ["DM-SUP-1", "DM-SUP-5"], ["84", "81"], "awarded"), ("RM-SURF", "300", ["DM-SUP-1", "DM-SUP-2"], ["139", "142"], "awarded"),
        ("PM-BOTTLE-200", "1000", ["DM-SUP-3", "DM-SUP-4"], ["14", "13.5"], "awarded"), ("RM-OLIVE", "100", ["DM-SUP-2", "DM-SUP-5"], ["418", "430"], "awarded"),
        ("RM-SHEA", "80", ["DM-SUP-2", "DM-SUP-1"], ["508", "495"], "quoted"), ("PM-JAR-150", "700", ["DM-SUP-3", "DM-SUP-4"], ["22", "21"], "quoted"),
        ("RM-KAOLIN", "120", ["DM-SUP-1", "DM-SUP-5"], ["61", "64"], "quoted"), ("RM-VITE", "20", ["DM-SUP-5", "DM-SUP-2"], ["1850", "1790"], "quoted"),
        ("PM-JAR-60", "500", ["DM-SUP-3", "DM-SUP-4"], ["18", "17.5"], "sent"), ("RM-GLYCERIN", "150", ["DM-SUP-2", "DM-SUP-5"], ["98", "92"], "sent"),
        ("PM-CARTON-12", "600", ["DM-SUP-4", "DM-SUP-3"], ["11", "12"], "sent"),
    ]
    for i, (code, qty, sups, prices, stage) in enumerate(rfq_specs):
        with c.days_ago(26 - 2 * i):
            rfq = c.ok(buyer.post("/api/procurement/rfqs", json={"items": [{"item_id": items[code], "qty": qty}], "supplier_ids": [sup[s] for s in sups], "notes": f"Demo RFQ: {code}"}))
            c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/send"))
            if stage in ("quoted", "awarded"):
                for s_code, price, lead in zip(sups, prices, (7, 12)):
                    c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/quotations", json={"supplier_id": sup[s_code], "lead_time_days": lead, "freight": "0" if lead == 7 else "300",
                                                                                          "lines": [{"item_id": items[code], "unit_price": price}]}))
            if stage == "awarded":
                cmp = c.ok(buyer.get(f"/api/procurement/rfqs/{rfq['id']}/comparison"))
                po = c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/award", json={"quotation_id": cmp["recommended_quotation_id"]}))["purchase_order"]
                po = c.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
                po = c.ok(admin.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
                if i % 2 == 0:                                                   # half of the awarded orders are delivered, inspected and released
                    gr = receive(c, po, [(po["items"][0]["id"], po["items"][0]["qty_ordered"])], f"DN-DM-RFQD-{i:02d}", f"RFQD{i}")
                    inspect_batch(c, gr["lines"][0]["batch_id"], "pass")
        out["rfqs"].append(rfq["id"])
    # ---- more incoming rejections returned to the supplier (the first one, with its deviation and CAPA, is in procurement_exceptions)
    for i, (sup_code, code, qty, price, why) in enumerate([("DM-SUP-1", "RM-SURF", "40", "139", "Assay below specification"), ("DM-SUP-3", "PM-BOTTLE-200", "300", "14", "Neck finish out of tolerance"),
                                                         ("DM-SUP-2", "RM-OLIVE", "25", "418", "Peroxide value above limit"), ("DM-SUP-4", "PM-CARTON-12", "200", "11", "Crushed corners on arrival")]):
        with c.days_ago(18 - 3 * i):
            po = buy(c, sup_code, [(code, qty, price)], note=f"Demo: {why}")
            gr = receive(c, po, [(po["items"][0]["id"], qty)], f"DN-DM-REJ-{i:02d}", f"REJ{i}")
            bid = gr["lines"][0]["batch_id"]
            inspect_batch(c, bid, "fail", reason=why)
            gri = c.ok(c.u("stores").get(f"/api/inventory/batches/{bid}"))["source"]["goods_receipt_id"]
            gr_item = rows("SELECT id FROM goods_receipt_items WHERE gr_id = :g ORDER BY id LIMIT 1", g=gri)[0][0]
            ret = c.ok(c.u("stores").post(f"/api/procurement/goods-receipts/{gri}/returns", headers=c.k("ret"), json={"reason_type": "QC_REJECTED", "reason": f"Failed incoming QC: {why}", "lines": [{"gr_item_id": gr_item, "qty": qty}]}))
            out["returns"].append(ret["id"])
    return out


# ================================================================== R&D: projects, formulations, versions, trials
BASES = {  # product type -> (ingredient code, share of 100) ; water takes the rest
    "wash": [("RM-SURF", "28"), ("RM-ALOE", "9"), ("RM-GLYCERIN", "2")], "butter": [("RM-SHEA", "30"), ("RM-OLIVE", "14"), ("RM-VITE", "0.4")],
    "scrub": [("RM-KAOLIN", "24"), ("RM-OLIVE", "12"), ("RM-GLYCERIN", "3")], "mask": [("RM-KAOLIN", "40"), ("RM-ALOE", "12"), ("RM-VITE", "0.3")],
    "lotion": [("RM-SHEA", "8"), ("RM-OLIVE", "6"), ("RM-GLYCERIN", "4"), ("RM-ALOE", "5")],
}
RND_SPECS = [  # code, name, product FG code, base, scent, final state
    ("FANTASEA-WASH", "Fantasea body wash", "FG-BW-FANTASEA-200", "wash", "RM-OUD", "draft"), ("CALM-WASH", "Calm-A-Sutra body wash", "FG-BW-CALM-200", "wash", None, "draft"),
    ("OLIVE-WASH", "Olive body wash", "FG-BW-OLIVE-200", "wash", None, "draft"), ("OLIVE-SCRUB", "Olive brightening body scrub", "FG-BS-OLIVE-150", "scrub", None, "awaiting"),
    ("FANTASEA-SCRUB", "Fantasea brightening body scrub", "FG-BS-FANTASEA-150", "scrub", "RM-OUD", "awaiting"), ("OUD-LOTION", "Oud hydrating body lotion", "FG-BL-OUD-200", "lotion", "RM-OUD", "awaiting"),
    ("OLIVE-BUTTER", "Olive moisturising body butter", "FG-BB-OLIVE-150", "butter", None, "approved"), ("DETAN-MASK", "Detan face mask", "FG-DETAN-60", "mask", None, "approved"),
    ("OUD-SCRUB", "Oud brightening body scrub", "FG-BS-OUD-150", "scrub", "RM-OUD", "approved"), ("HANDCREAM-30", "Moisturising hand cream", "FG-HC-5X30", "butter", None, "revised"),
    ("FALLSTOP", "Fall Stop shampoo", "FG-FALLSTOP-200", "wash", None, "failed"),
]


@step("depth_rnd")
def depth_rnd(c: Ctx) -> dict:
    """Eleven more development projects and formulations in every state: draft, trial passed and awaiting approval, approved, a revision v2, and a failed trial."""
    items, rnd, rnd2 = item_ids(c), c.u("rnd"), c.u("rnd2")
    out: dict = {"projects": [], "formulations": []}
    for i, (code, name, fg, base, scent, state) in enumerate(RND_SPECS):
        if rows("SELECT 1 FROM formulations WHERE code = :c", c=f"F-DM-{code}"):          # already seeded (a re-run after a partial failure resumes here)
            continue
        ing = [(k, v) for k, v in BASES[base]]
        from decimal import Decimal
        if scent:
            ing.append((scent, "0.5"))
        from decimal import Decimal
        water = Decimal("100") - sum(Decimal(v) for _, v in ing)
        ingredients = [{"item_id": items[k], "qty": v} for k, v in ing] + [{"item_id": items["RM-DIWATER"], "qty": str(water)}]
        with c.days_ago(60 - 4 * i):
            proj = c.ok(rnd.post("/api/rnd/projects", json={"code": f"RND-DM-{code}", "name": f"{name}: development", "concept": f"{name} for the Mintree range", "product_category": "Body care" if base != "mask" else "Face care",
                                                           "target_specification": "pH 5.0-6.5; stable 4 weeks at 40C", "target_launch_date": c.d(45 + 5 * i), "lead_researcher_id": me(c, "rnd")}))
            f = c.ok(rnd.post("/api/rnd/formulations", json={"code": f"F-DM-{code}", "name": name, "product_item_id": items[fg], "batch_size": "100", "ingredients": ingredients,
                                                            "process_instructions": "Heat the water phase to 70C, add actives under slow agitation, cool to 40C, add fragrance."}))
            v1 = f["versions"][0]["id"]
            c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/formulations", json={"formulation_id": f["id"]}))
            if state != "draft":
                c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/status", json={"status": "DEVELOPMENT"}))
                t = c.ok(rnd.post(f"/api/rnd/versions/{v1}/trials", json={"quantity": "1"}))
                passed = state != "failed"
                for kind, param, val in (("STABILITY", "pH after 4 weeks at 40C", "5.6" if passed else "7.9"), ("TEXTURE", "Viscosity (cP)", "4100" if passed else "1900")):
                    c.ok(rnd.post(f"/api/rnd/trials/{t['id']}/observations", json={"kind": kind, "parameter": param, "value": val, "result": "PASS" if passed else "FAIL"}))
                c.ok(rnd.post(f"/api/rnd/trials/{t['id']}/conclude", json={"status": "PASSED" if passed else "FAILED", "observations": "Stable and on target." if passed else "Phase separation at 4 weeks; reformulate.",
                                                                        "stability_note": "4-week accelerated study"}))
            if state in ("awaiting", "approved", "revised"):
                c.ok(rnd.post(f"/api/rnd/versions/{v1}/submit"))
            if state in ("approved", "revised"):
                c.ok(rnd2.post(f"/api/rnd/versions/{v1}/approve"))
                c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/status", json={"status": "TRIALS"}))
            if state == "revised":
                v2 = c.ok(rnd.post(f"/api/rnd/formulations/{f['id']}/versions", json={"from_version_no": 1, "revision_note": "Cost reduction: trim the butter share by 2 points"}))["id"]
                c.ok(rnd.patch(f"/api/rnd/versions/{v2}", json={"ingredients": [{**ing_, "qty": ("28" if ing_["item_id"] == items["RM-SHEA"] else ing_["qty"])} for ing_ in ingredients]}))
            if state == "failed":                                          # trial 1 failed -> the draft is reformulated (stabiliser added) -> trial 2 passes -> submitted; both trials stay on record
                tweaked = [dict(x) for x in ingredients]
                tweaked[-1] = {**tweaked[-1], "qty": str(Decimal(tweaked[-1]["qty"]) - Decimal("0.5"))}           # 0.5 kg less water ...
                gly = next(x for x in tweaked if x["item_id"] == items["RM-GLYCERIN"])                            # ... becomes 0.5 kg more glycerin (every base carries some)
                gly["qty"] = str(Decimal(gly["qty"]) + Decimal("0.5"))
                c.ok(rnd.patch(f"/api/rnd/versions/{v1}", json={"ingredients": tweaked}))
                t2 = c.ok(rnd.post(f"/api/rnd/versions/{v1}/trials", json={"quantity": "1"}))
                c.ok(rnd.post(f"/api/rnd/trials/{t2['id']}/observations", json={"kind": "STABILITY", "parameter": "pH after 4 weeks at 40C", "value": "5.5", "result": "PASS"}))
                c.ok(rnd.post(f"/api/rnd/trials/{t2['id']}/conclude", json={"status": "PASSED", "observations": "Separation resolved with 0.5% glycerin."}))
                c.ok(rnd.post(f"/api/rnd/versions/{v1}/submit"))
        out["projects"].append(proj["id"])
        out["formulations"].append(f["id"])
    return out


# ================================================================== quality: deviations and CAPA
DEV_SPECS = [  # title, description, class, depth: open | investigated | capa | capa_done | closed
    ("Filling weight drift on line 2", "Net content 3 g under target across 40 jars.", "MINOR", "open"), ("Label misalignment on a body wash lot", "Labels skewed by more than 3 mm on 120 bottles.", "MINOR", "open"),
    ("Cold-room temperature excursion", "Raw material store reached 29C for 3 hours overnight.", "MAJOR", "open"), ("Wrong batch number printed on cartons", "Carton print used the previous batch counter.", "MAJOR", "investigated"),
    ("Out-of-specification pH in a bulk batch", "pH 7.1 against a limit of 5.0-6.5; batch held.", "MAJOR", "investigated"), ("Foreign particle found during filling", "A small fibre in 2 of 50 inspected jars.", "CRITICAL", "capa"),
    ("Missing cap liners on a jar lot", "Supplier delivered 600 jars without liners.", "MAJOR", "capa"), ("Calibration overdue on the lab balance", "Calibration certificate expired two days before use.", "MINOR", "capa_done"),
    ("Mixer seal contamination risk", "Seal wear traced after a maintenance breakdown.", "MAJOR", "capa_done"), ("Documentation gap in a batch record", "Operator signature missing on one stage.", "MINOR", "closed"),
    ("Damaged outer cartons at dispatch", "12 cartons crushed in the dispatch bay.", "MINOR", "closed"),
]


@step("depth_quality")
def depth_quality(c: Ctx) -> dict:
    """Eleven more deviations at every point of the workflow: raised, investigated, CAPA assigned, CAPA completed, CAPA verified and the deviation closed."""
    qa, qa2 = c.u("qa"), c.u("qa2")
    batches = [r[0] for r in rows("SELECT id FROM inventory_batches ORDER BY id DESC LIMIT 11")]
    owner = me(c, "qa2")
    out: dict = {"deviations": [], "capa": []}
    for i, (title, desc, cls, depth) in enumerate(DEV_SPECS):
        if rows("SELECT 1 FROM deviations WHERE title = :t", t=title):                    # already seeded (a re-run after a partial failure resumes here)
            continue
        with c.days_ago(40 - 3 * i):
            dev = c.ok(qa.post("/api/quality/deviations", json={"title": title, "description": desc, "classification": cls, "batch_id": batches[i] if i < len(batches) and i % 2 == 0 else None}))
            if depth != "open":
                c.ok(qa.post(f"/api/quality/deviations/{dev['id']}/investigate", json={"root_cause": f"Investigated: {desc[:60]} Contributing cause confirmed on the floor."}))
            if depth in ("capa", "capa_done", "closed"):
                view = c.ok(qa.post(f"/api/quality/deviations/{dev['id']}/capa", json={"kind": "CORRECTIVE" if i % 2 else "PREVENTIVE", "description": f"Action for: {title}", "responsible_id": owner, "due_date": c.d(14 - i)}))
                capa = view["actions"][-1]                                   # the API answers with the deviation; its newest action is the CAPA just raised
                out["capa"].append(capa["id"])
                if depth in ("capa_done", "closed"):
                    c.ok(qa2.post(f"/api/quality/capa/{capa['id']}/complete"))
                if depth == "closed":
                    c.ok(qa.post(f"/api/quality/capa/{capa['id']}/verify"))
                    c.ok(qa2.post(f"/api/quality/deviations/{dev['id']}/close"))                     # a different person from the one who raised it (segregation of duties)
        out["deviations"].append(dev["id"])
    return out


# ================================================================== maintenance: equipment, calibration, schedules, breakdowns
EQUIP = [("EQ-HEAT-01", "Jacketed heating vessel 300 L", "Mixing", "Plant 1"), ("EQ-FILL-03", "Cream filling machine", "Filling", "Plant 1"), ("EQ-CAP-01", "Capping and sealing unit", "Packing", "Plant 2"),
         ("EQ-LABEL-01", "Wrap-around labelling machine", "Packing", "Plant 2"), ("EQ-AIR-01", "Compressed air system", "Utilities", "Plant 1"), ("EQ-RO-01", "Reverse-osmosis water plant", "Utilities", "Plant 1"),
         ("EQ-BAL-01", "Analytical balance", "Laboratory", "QC lab"), ("EQ-VISC-01", "Brookfield viscometer", "Laboratory", "QC lab")]


@step("depth_maintenance")
def depth_maintenance(c: Ctx) -> dict:
    """Eight more machines (twelve in all), calibrations with a PASS and a FAIL, a preventive schedule for each (some overdue), and five more breakdowns with downtime and spare parts."""
    adm, mm = c.u("admin"), c.u("maint")
    tech = me(c, "maint")
    eq: dict = {}
    for aid, name, cat, loc in EQUIP:
        lab = cat == "Laboratory"
        eq[aid] = c.ok(adm.post("/api/maintenance/equipment", json={"asset_id": aid, "name": name, "category": cat, "location": loc, "calibration_required": lab, "calibration_due": c.d(60) if lab else None,
                                                                   "purchase_date": c.d(-500), "installation_date": c.d(-490), "manufacturer": "Demo Machinery Co"}))["id"]
    for aid, result in (("EQ-BAL-01", "PASS"), ("EQ-VISC-01", "PASS"), ("EQ-LAB-PH", "FAIL")):
        eid = eq.get(aid) or c.refs["maintenance"]["equipment"][aid]
        c.ok(mm.post(f"/api/maintenance/equipment/{eid}/calibrations", json={"calibrated_on": c.d(-5), "due_date": c.d(85 if result == "PASS" else 7), "result": result, "certificate_ref": f"CAL-DM-{aid}"}))
    for i, (aid, name, cat, loc) in enumerate(EQUIP):
        c.ok(mm.post("/api/maintenance/schedules", json={"equipment_id": eq[aid], "title": f"{name}: periodic service", "frequency_days": 30 * (1 + i % 3), "first_due": c.d(-6 + 9 * (i % 4)),
                                                         "checklist": ["Inspect and clean", "Lubricate moving parts", "Record readings"], "assigned_to": tech}))
    gen = c.ok(mm.post("/api/maintenance/schedules/generate"))
    out: dict = {"equipment": eq, "generated": gen, "breakdowns": []}
    for i, (aid, what, cause, part, cost) in enumerate([("EQ-FILL-03", "Nozzle blocked; fill volume unstable", "Residue build-up", "Nozzle set", "2200.00"), ("EQ-CAP-01", "Capping torque out of range", "Worn clutch", "Torque clutch", "3600.00"),
                                                      ("EQ-LABEL-01", "Labels skewing on the wrap station", "Misaligned guide", "Guide rail kit", "1400.00"), ("EQ-AIR-01", "Pressure drop on the packing line", "Failed regulator", "Pressure regulator", "2900.00"),
                                                      ("EQ-RO-01", "Conductivity alarm on product water", "Exhausted membrane", "RO membrane", "18500.00")]):
        with c.days_ago(24 - 4 * i):
            bd = c.ok(mm.post("/api/maintenance/breakdowns", json={"equipment_id": eq[aid], "description": what, "priority": ("CRITICAL", "HIGH", "MEDIUM", "HIGH", "CRITICAL")[i], "production_impact": "Line stopped until repair"}))
            c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/assign", json={"assignee_id": tech}))
            c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/start"))
            if i < 4:                                                  # the last one is still in progress: an open breakdown on the board
                c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/spare-parts", json={"part_name": part, "qty": 1, "unit_cost": cost}))
                c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/complete", json={"root_cause": cause, "repair_notes": f"{part} replaced and tested."}))
                if i < 3:
                    c.ok(adm.post(f"/api/maintenance/work-orders/{bd['id']}/verify"))
        out["breakdowns"].append(bd["id"])
    return out


# ================================================================== CRM: contacts
CONTACTS = [("Priya Nair", "Owner", "9810000001"), ("Arjun Mehta", "Purchase head", "9810000002"), ("Sana Qureshi", "Store manager", "9810000003"), ("Rahul Verma", "Category manager", "9810000004"),
            ("Neha Iyer", "Accounts", "9810000005"), ("Kabir Shah", "Regional lead", "9810000006"), ("Isha Rao", "Buyer", "9810000007"), ("Aarav Mehta", "Director", "9810000008"),
            ("Meera Joshi", "Operations", "9810000009"), ("Dev Malhotra", "Owner", "9810000010"), ("Tara Menon", "Procurement", "9810000011"), ("Vikram Sethi", "Finance", "9810000012")]


@step("depth_contacts")
def depth_contacts(c: Ctx) -> dict:
    """A named primary contact for twelve customers, so the customer pages and quotations have people attached."""
    cust = rows("SELECT id, code FROM customers WHERE code LIKE 'DM-CUS-%' ORDER BY id LIMIT 12")
    sales = c.u("sales")
    out = []
    for (cid, code), (name, role, phone) in zip(cust, CONTACTS):
        r = sales.get(f"/api/sales/customers/{cid}/contacts")
        if r.status_code == 200 and any(ct.get("is_primary") for ct in (r.json() if isinstance(r.json(), list) else r.json().get("items", []))):
            continue
        c.ok(sales.post(f"/api/sales/customers/{cid}/contacts", json={"name": name, "role": role, "phone": phone, "email": f"{name.split()[0].lower()}.{code.lower()}@example.com", "is_primary": True}))
        out.append(cid)
    return {"customers": out}


# ================================================================== inventory: stock counts
@step("depth_stocktakes")
def depth_stocktakes(c: Ctx) -> dict:
    """Stock counts: counted against the system quantity, small differences approved into adjustments, one count cancelled."""
    stores, admin = c.u("stores"), c.u("admin")
    wh = [r[0] for r in rows("SELECT w.id FROM warehouses w WHERE EXISTS (SELECT 1 FROM stock_balances b WHERE b.warehouse_id = w.id AND b.qty_physical > 0) ORDER BY w.id LIMIT 4")]
    out: dict = {"stocktakes": []}
    for i, w in enumerate(wh):
        with c.days_ago(20 - 5 * i):
            st = c.ok(stores.post("/api/inventory/stocktakes", json={"warehouse_id": w, "note": f"Demo: cycle count {i + 1}"}))
            lines = st.get("lines") or c.ok(stores.get(f"/api/inventory/stocktakes/{st['id']}")).get("lines") or []
            if not lines:
                continue
            counts = []
            for j, ln in enumerate(lines):
                sysq = ln.get("system_qty", ln.get("qty_system", "0"))
                from decimal import Decimal
                delta = Decimal("-1") if j in (2, 11, 23) and Decimal(str(sysq)) >= 1 else Decimal("0")     # a handful of one-unit shortages, everything else matches
                counts.append({"balance_id": ln["balance_id"], "counted_qty": str(max(Decimal(str(sysq)) + delta, Decimal("0"))), "note": "Counted by hand" if delta == 0 else "One unit short"})
            c.ok(stores.post(f"/api/inventory/stocktakes/{st['id']}/counts", json={"counts": counts}))
            if i == 3:
                c.ok(stores.post(f"/api/inventory/stocktakes/{st['id']}/cancel"))
            else:
                c.ok(admin.post(f"/api/inventory/stocktakes/{st['id']}/approve"))
        out["stocktakes"].append(st["id"])
    return out


# ================================================================== document centre
def _pdf(title: str, lines: list[str]) -> bytes:
    """A small, valid single-page PDF. Content is plain text: this is a labelled demo document, not a rendering of the record."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = ["BT /F1 15 Tf 56 780 Td (" + esc(title) + ") Tj ET", "BT /F1 9 Tf 56 760 Td (DEMO DOCUMENT. Sample data for demonstration only.) Tj ET"]
    y = 730
    for ln in lines:
        body.append("BT /F1 11 Tf 56 %d Td (%s) Tj ET" % (y, esc(ln)))
        y -= 20
    stream = "\n".join(body)
    objs = ["<</Type/Catalog/Pages 2 0 R>>", "<</Type/Pages/Kids[3 0 R]/Count 1>>",
            "<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            f"<</Length {len(stream)}>>\nstream\n{stream}\nendstream", "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode("latin-1", "replace")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@step("depth_documents")
def depth_documents(c: Ctx) -> dict:
    """Fourteen documents uploaded through the real endpoint (allow-list, size limit, SHA-256, audit), each linked to the record it describes; three get a second version; every one is downloaded back and checked."""
    admin = c.u("admin")
    pick = {
        "purchase_order": rows("SELECT id, number FROM purchase_orders ORDER BY id LIMIT 3"),
        "supplier_invoice": rows("SELECT id, number FROM supplier_invoices ORDER BY id LIMIT 2"),
        "goods_receipt": rows("SELECT id, number FROM goods_receipts ORDER BY id LIMIT 2"),
        "inspection": rows("SELECT id, number FROM inspections ORDER BY id LIMIT 2"),
        "production_order": rows("SELECT id, number FROM production_orders ORDER BY id LIMIT 2"),
        "sales_invoice": rows("SELECT id, number FROM sales_invoices ORDER BY id LIMIT 2"),
        "shipment": rows("SELECT id, awb FROM shipments WHERE awb IS NOT NULL ORDER BY id LIMIT 2"),
        "maintenance_work_order": rows("SELECT id, number FROM maintenance_work_orders ORDER BY id LIMIT 1"),
        "formulation_version": rows("SELECT id, version_no::text FROM formulation_versions ORDER BY id LIMIT 1"),
    }
    plan = [  # (entity type, index, doc_type, title template, file name)
        ("purchase_order", 0, "SUPPLIER_DOCUMENT", "Purchase order {n}: signed copy", "po-signed.pdf"), ("purchase_order", 1, "SUPPLIER_DOCUMENT", "Purchase order {n}: supplier acknowledgement", "po-ack.pdf"),
        ("purchase_order", 2, "SUPPLIER_DOCUMENT", "Purchase order {n}: delivery schedule", "po-schedule.pdf"), ("supplier_invoice", 0, "SUPPLIER_INVOICE", "Supplier invoice {n}", "supplier-invoice.pdf"),
        ("supplier_invoice", 1, "SUPPLIER_INVOICE", "Supplier invoice {n}", "supplier-invoice-2.pdf"), ("goods_receipt", 0, "SUPPLIER_DOCUMENT", "Delivery challan for receipt {n}", "challan.pdf"),
        ("goods_receipt", 1, "SUPPLIER_DOCUMENT", "Delivery challan for receipt {n}", "challan-2.pdf"), ("inspection", 0, "INSPECTION_RECORD", "QC report for inspection {n}", "qc-report.pdf"),
        ("inspection", 1, "COA", "Certificate of analysis: inspection {n}", "coa.pdf"), ("production_order", 0, "BATCH_RECORD", "Batch manufacturing record: {n}", "bmr.pdf"),
        ("sales_invoice", 0, "CUSTOMER_DOCUMENT", "Customer invoice {n}", "invoice.pdf"), ("shipment", 0, "COURIER_LABEL", "Courier label {n}", "label.pdf"),
        ("formulation_version", 0, "FORMULATION", "Approved formulation, version {n}", "formulation.pdf"), ("maintenance_work_order", 0, "OTHER", "Maintenance report {n}", "maintenance.pdf"),
    ]
    out: dict = {"documents": []}
    for k, (etype, idx, dtype, tmpl, fname) in enumerate(plan):
        recs = pick.get(etype) or []
        if idx >= len(recs):
            continue
        rid, ref = recs[idx]
        title = tmpl.format(n=ref)
        data = _pdf(title, [f"Record: {etype.replace('_', ' ')} {ref}", f"Document type: {dtype}", "Generated by the demo seeder from a real record reference."])
        r = admin.post("/api/documents", data={"title": title, "doc_type": dtype, "description": f"DEMO: linked to {etype} {ref}", "entity_type": etype, "entity_id": str(rid)}, files={"file": (fname, data, "application/pdf")})
        doc = c.ok(r)
        if k % 5 == 0:
            c.ok(admin.post(f"/api/documents/{doc['id']}/versions", data={"note": "Revised copy after supplier comments"}, files={"file": (fname, _pdf(title + " (rev 2)", ["Revision 2: supplier comments incorporated."]), "application/pdf")}))
        dl = admin.get(f"/api/documents/{doc['id']}/download")
        assert dl.status_code == 200 and dl.content[:5] == b"%PDF-", f"download of document {doc['id']} failed"
        out["documents"].append(doc["id"])
    return out



# ================================================================== second wave: BOMs, plans, certificates, complaints, contacts, counts, maintenance
BOM_RECIPES = {   # product kind -> (batch size, [(raw code, qty per batch)])
    "wash": (200, [("RM-SURF", "30"), ("RM-ALOE", "8"), ("RM-GLYCERIN", "2"), ("PM-BOTTLE-200", "200")]),
    "scrub": (150, [("RM-KAOLIN", "15"), ("RM-OLIVE", "6"), ("RM-GLYCERIN", "2"), ("PM-JAR-150", "150")]),
    "butter": (150, [("RM-SHEA", "20"), ("RM-OLIVE", "10"), ("RM-VITE", "0.3"), ("PM-JAR-150", "150")]),
    "lotion": (200, [("RM-SHEA", "8"), ("RM-OLIVE", "6"), ("RM-ALOE", "5"), ("PM-BOTTLE-200", "200")]),
}
FG_KIND = {"FG-BL-OUD-200": "lotion", "FG-BS-FANTASEA-150": "scrub", "FG-BS-OLIVE-150": "scrub", "FG-BW-CALM-200": "wash", "FG-BW-FANTASEA-200": "wash", "FG-BW-OLIVE-200": "wash",
           "FG-FALLSTOP-200": "wash", "FG-HC-5X30": "butter"}


@step("depth_production")
def depth_production(c: Ctx) -> dict:
    """Eight more approved BOMs (one for every product), then four more production plans: a draft, two confirmed, one with a line converted into a planned order."""
    items, pm, pm2 = item_ids(c), c.u("pm"), c.u("pm2")
    W, wcs = c.refs["foundation"]["wh_rm"], c.refs["master_data"]["wc"]
    have = {r[0] for r in rows("SELECT i.item_code FROM boms b JOIN items i ON i.id = b.output_item_id")}
    out: dict = {"boms": {}, "plans": []}
    for code, kind in FG_KIND.items():
        if code in have:
            continue
        size, lines = BOM_RECIPES[kind]
        b = c.ok(pm.post("/api/production/boms/manual", json={"output_item_id": items[code], "output_qty": str(size), "lines": [{"item_id": items[k], "qty": q} for k, q in lines]}))
        c.ok(pm2.post(f"/api/production/boms/{b['id']}/approve"))
        out["boms"][code] = b["id"]
    return out


@step("depth_plans")
def depth_plans(c: Ctx) -> dict:
    """Four more production plans over the approved BOMs: a draft, two confirmed (overload accepted with a reason) and one with a line converted into a planned order."""
    pm = c.u("pm")
    W, wcs = c.refs["foundation"]["wh_rm"], c.refs["master_data"]["wc"]
    out: dict = {"plans": []}
    bom_of = {r[0]: r[1] for r in rows("SELECT i.item_code, b.id FROM boms b JOIN items i ON i.id = b.output_item_id WHERE b.status = 'ACTIVE'")}
    plans = [("Week 47: wash range", [("FG-BW-CALM-200", "200", 2, "WC-FILL"), ("FG-BW-OLIVE-200", "200", 4, "WC-FILL")], "draft"),
             ("Week 47: scrubs", [("FG-BS-OLIVE-150", "150", 3, "WC-FILL"), ("FG-BS-OUD-150", "150", 5, "WC-PACK")], "confirmed"),
             ("Week 48: gifting hand creams", [("FG-HC-5X30", "150", 6, "WC-PACK")], "confirmed"),
             ("Week 48: Fantasea launch", [("FG-BW-FANTASEA-200", "200", 7, "WC-FILL"), ("FG-BS-FANTASEA-150", "150", 8, "WC-PACK")], "converted")]
    for name, lines, state in plans:
        usable = [ln for ln in lines if ln[0] in bom_of]
        if not usable:
            continue
        plan = c.ok(pm.post("/api/production/plans", json={"name": name, "period_start": c.d(1), "period_end": c.d(12), "notes": "Demo plan", "lines": [
            {"bom_id": bom_of[code], "planned_qty": qty, "planned_date": c.d(day), "warehouse_id": W, "work_centre_id": wcs[wc]} for code, qty, day, wc in usable]}))
        if state in ("confirmed", "converted"):
            r = pm.post(f"/api/production/plans/{plan['id']}/confirm", json={"expected_version": plan["version"], "accept_overload_reason": "Overtime shift approved by the plant head"})
            plan = c.ok(r)
        if state == "converted":
            c.ok(pm.post(f"/api/production/plans/{plan['id']}/lines/1/convert"))
        out["plans"].append(plan["id"])
    return out


@step("depth_quality_extras")
def depth_quality_extras(c: Ctx) -> dict:
    """Certificates of analysis for released finished-goods and incoming inspections (the product issues one only for a RELEASED inspection) and six more customer complaints, some resolved."""
    qa, sales = c.u("qa"), c.u("sales")
    have = {r[0] for r in rows("SELECT inspection_id FROM certificates_of_analysis")}
    cand = [r[0] for r in rows("SELECT id FROM inspections WHERE decision = 'RELEASE' ORDER BY (stage = 'FINISHED') DESC, id DESC LIMIT 24") if r[0] not in have][:11]
    coas = [c.ok(qa.post(f"/api/quality/inspections/{i}/coa"))["id"] for i in cand]
    cust = [r[0] for r in rows("SELECT id FROM customers WHERE code LIKE 'DM-CUS-%' ORDER BY id")][:6]
    batches = [r[0] for r in rows("SELECT b.id FROM inventory_batches b ORDER BY b.id DESC LIMIT 6")]
    texts = ["Pump dispenser stuck on arrival", "Jar lid cracked in transit", "Fragrance weaker than the previous lot", "Label peeled after a week", "Short quantity in a carton of 12", "Texture thinner than usual"]
    cids = []
    for i, (cid, bid, txt) in enumerate(zip(cust, batches, texts)):
        with c.days_ago(20 - 3 * i):
            cp = c.ok(sales.post("/api/crm/complaints", json={"customer_id": cid, "batch_id": bid if i % 2 == 0 else None, "description": f"{txt} (demo complaint)"}))
            if i % 3 == 0:
                c.ok(sales.post(f"/api/crm/complaints/{cp['id']}/update", json={"status": "INVESTIGATING"}))
            if i % 3 == 1:
                c.ok(sales.post(f"/api/crm/complaints/{cp['id']}/update", json={"status": "RESOLVED", "resolution": "Replacement sent; carrier claim raised."}))
        cids.append(cp["id"])
    return {"coas": coas, "complaints": cids}


@step("depth_inventory_counts")
def depth_inventory_counts(c: Ctx) -> dict:
    """Three more stock counts on the working store, a fortnight apart, each approved into adjustments: the stock ledger and the books both move."""
    stores, admin = c.u("stores"), c.u("admin")
    from decimal import Decimal
    w = rows("SELECT w.id FROM warehouses w WHERE EXISTS (SELECT 1 FROM stock_balances b WHERE b.warehouse_id = w.id AND b.qty_physical > 0) ORDER BY w.id LIMIT 1")[0][0]
    out = []
    for i in range(3):
        with c.days_ago(9 - 3 * i):
            st = c.ok(stores.post("/api/inventory/stocktakes", json={"warehouse_id": w, "note": f"Demo: cycle count round {i + 2}"}))
            lines = st.get("lines") or c.ok(stores.get(f"/api/inventory/stocktakes/{st['id']}")).get("lines") or []
            counts = []
            for j, ln in enumerate(lines):
                sysq = Decimal(str(ln.get("system_qty", ln.get("qty_system", "0"))))
                delta = Decimal("1") if j in (4 + i, 17 + i) else Decimal("0")                    # found-stock overages only: a shortage below the reserved quantity is (rightly) refused
                counts.append({"balance_id": ln["balance_id"], "counted_qty": str(sysq + delta), "note": "One unit found" if delta else "Counted"})
            c.ok(stores.post(f"/api/inventory/stocktakes/{st['id']}/counts", json={"counts": counts}))
            c.ok(admin.post(f"/api/inventory/stocktakes/{st['id']}/approve"))
        out.append(st["id"])
    return {"stocktakes": out}


@step("depth_more")
def depth_more(c: Ctx) -> dict:
    """Contacts for every customer that still has none (up to twelve), four more machine breakdowns (two repaired, one verified, one open) and calibration history for the lab instruments."""
    sales, mm, adm = c.u("sales"), c.u("maint"), c.u("admin")
    done = {r[0] for r in rows("SELECT customer_id FROM customer_contacts")}
    out: dict = {"contacts": [], "breakdowns": []}
    for (cid, code), (name, role, phone) in zip([r for r in rows("SELECT id, code FROM customers WHERE customer_type NOT IN ('MARKETPLACE') ORDER BY id") if r[0] not in done][:12 - len(done)], CONTACTS[::-1]):
        c.ok(sales.post(f"/api/sales/customers/{cid}/contacts", json={"name": name, "role": role, "phone": phone, "email": f"{name.split()[0].lower()}.{code.lower()}@example.com", "is_primary": True}))
        out["contacts"].append(cid)
    eq = {r[1]: r[0] for r in rows("SELECT id, asset_id FROM equipment")}
    tech = me(c, "maint")
    for i, (aid, what, cause, part, cost) in enumerate([("EQ-HEAT-01", "Temperature controller drifting", "Sensor fault", "PT100 sensor", "1800.00"), ("EQ-FILL-02", "Air leak on the piston", "Worn seal", "Piston seal kit", "2400.00"),
                                                      ("EQ-MIX-500", "Unusual gearbox noise", "Bearing wear", "Gearbox bearing", "5200.00"), ("EQ-HOMOG-01", "Vibration at speed", "Loose coupling", None, "0")]):
        with c.days_ago(10 - 2 * i):
            bd = c.ok(mm.post("/api/maintenance/breakdowns", json={"equipment_id": eq[aid], "description": what, "priority": "HIGH" if i % 2 == 0 else "MEDIUM", "production_impact": "Short stop; production re-sequenced"}))
            c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/assign", json={"assignee_id": tech}))
            c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/start"))
            if i < 3:
                if part:
                    c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/spare-parts", json={"part_name": part, "qty": 1, "unit_cost": cost}))
                c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/complete", json={"root_cause": cause, "repair_notes": "Repaired and tested."}))
                if i < 2:
                    c.ok(adm.post(f"/api/maintenance/work-orders/{bd['id']}/verify"))
        out["breakdowns"].append(bd["id"])
    for aid in ("EQ-LAB-PH", "EQ-BAL-01", "EQ-VISC-01"):
        for k, back in enumerate((95, 185)):
            c.ok(mm.post(f"/api/maintenance/equipment/{eq[aid]}/calibrations", json={"calibrated_on": c.d(-back), "due_date": c.d(-back + 90), "result": "PASS", "certificate_ref": f"CAL-DM-{aid}-H{k + 1}"}))
    return out

# ================================================================== traceability scenarios: a batch split across warehouses, and a recorded impact analysis
@step("depth_trace")
def depth_trace(c: Ctx) -> dict:
    """Two traceability scenarios the earlier steps do not create. Everything else Batch 360 shows (multi-source consumption, one raw lot feeding several orders, partial dispatch, returns,
    a failed result and its retest) already exists in the seeded production, dispatch and quality stories.

    SPLIT: a released finished batch with free stock is partly transferred to a second warehouse through the real transfer endpoint; the batch keeps one identity, now held in two balances.
    RECALL ANALYSIS: the quality manager runs a batch impact analysis on the raw lot with the widest downstream reach. The run is audited; it changes no stock and no status."""
    stores, qa = c.u("stores"), c.u("qa")
    out: dict = {}
    cand = rows("""SELECT sb.batch_id, sb.item_id, sb.warehouse_id, sb.qty_available FROM stock_balances sb JOIN inventory_batches b ON b.id = sb.batch_id JOIN items i ON i.id = sb.item_id
                    WHERE i.kind = 'FG' AND b.qc_status = 'RELEASED' AND sb.qty_available >= 20 AND (SELECT count(*) FROM stock_balances x WHERE x.batch_id = sb.batch_id) = 1
                    ORDER BY sb.qty_available DESC, sb.batch_id LIMIT 1""")
    other = rows("SELECT id FROM warehouses WHERE is_active AND id NOT IN (SELECT warehouse_id FROM stock_balances WHERE batch_id = :b) ORDER BY id LIMIT 1", b=cand[0][0]) if cand else []
    if cand and other:
        bid, item, wh, avail = cand[0]
        with c.days_ago(3):
            r = c.ok(stores.post("/api/inventory/transfers", headers=c.k("split"), json={"item_id": item, "batch_id": bid, "from_warehouse_id": wh, "to_warehouse_id": other[0][0], "qty": "10", "source_id": 9001}))
        out["split_batch"] = bid
    heavy = rows("""SELECT pc.batch_id FROM production_consumptions pc JOIN production_orders po ON po.id = pc.order_id JOIN shipment_lines sl ON sl.batch_id = po.output_batch_id
                      JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_orders so ON so.id = sh.so_id
                     GROUP BY pc.batch_id ORDER BY count(DISTINCT so.customer_id) DESC, pc.batch_id LIMIT 1""")             # the raw lot whose finished goods reached the most customers
    if heavy:
        imp = c.ok(qa.post(f"/api/traceability/batches/{heavy[0][0]}/impact-analysis"))
        out.update(impact_batch=heavy[0][0], impact_customers=imp["summary"]["customers"], impact_orders=imp["summary"]["production_orders"])
    return out

# ================================================================== Meta & Branding library, item categories, storage racks
BRAND_SPECS = [("MT-SKIN", "Mintree Skin", "Face masks, serums and creams"), ("MT-HAIR", "Mintree Hair", "Shampoos, conditioners and scalp care"),
               ("MT-BODY", "Mintree Body", "Body wash, lotions and butters"), ("MT-SALON", "Mintree Salon Pro", "Backbar and professional range")]
BRAND_COLOURS = [("Mintree teal", "#017468"), ("Deep forest", "#014F48"), ("Warm sand", "#E5C9A8"), ("Soft butter", "#F3E7B1"), ("Ink black", "#111111")]
BRAND_ASSETS = [("LOGO", "Primary logo lockup"), ("LOGO", "Square monogram"), ("GUIDELINE", "Brand guidelines v2"), ("PHOTO", "Studio product photography"), ("PHOTO", "Ingredient macro shots")]
SOCIAL_SPECS = [("INSTAGRAM", "@mintree.skin", "MT-SKIN"), ("INSTAGRAM", "@mintree.hair", "MT-HAIR"), ("FACEBOOK", "Mintree Body Care", "MT-BODY"),
                ("INSTAGRAM", "@mintreesalonpro", "MT-SALON"), ("FACEBOOK", "Mintree India", "MINTREE")]
CATEGORY_SPECS = [("SKIN", "Skin care", "FG"), ("HAIR", "Hair care", "FG"), ("BODY", "Body care", "FG"), ("OILS", "Oils and butters", "RM"), ("ACTV", "Actives and extracts", "RM"),
                  ("BASE", "Surfactants and bases", "RM"), ("CONT", "Primary containers", "PM"), ("CART", "Cartons and shippers", "PM")]


@step("depth_brand_library")
def depth_brand_library(c: Ctx) -> dict:
    """Four sub-brands with colours, logos, guidelines and photography; a social account for each brand (manual recording, no credentials); the item category tree;
    and three racks per warehouse. Brand and library records are idempotent by code / name; nothing is created twice."""
    adm, mk = c.u("admin"), c.u("mkt")
    brands = {r[0]: r[1] for r in rows("SELECT code, id FROM brands")}
    for code, name, desc in BRAND_SPECS:
        if code not in brands:
            brands[code] = c.ok(adm.post("/api/masters/brands", json={"code": code, "name": name, "description": desc}))["id"]
    names = {r[0]: r[1] for r in rows("SELECT code, name FROM brands")}
    have = {(r[0], r[1]) for r in rows("SELECT brand_id, name FROM brand_assets")}
    made = {"assets": 0, "social": 0, "categories": 0, "locations": 0}
    for i, (code, bid) in enumerate(sorted(brands.items())):
        label = names.get(code, code)
        specs = [("COLOR", f"{label}: {BRAND_COLOURS[(i + k) % 5][0]}", BRAND_COLOURS[(i + k) % 5][1]) for k in (0, 2)] + [(kind, f"{label}: {nm}", None) for kind, nm in (BRAND_ASSETS[i % 5], BRAND_ASSETS[(i + 3) % 5])]
        for kind, name, val in specs:
            if (bid, name) in have:
                continue
            body = {"brand_id": bid, "kind": kind, "name": name}
            if val:
                body["value"] = val
            else:     # a file asset must point at a Document Centre record: upload a small labelled PDF through the real endpoint
                fname = name.replace(":", "").replace(" ", "-").lower() + ".pdf"
                doc = c.ok(adm.post("/api/documents", data={"title": name, "doc_type": "OTHER", "description": f"DEMO: brand library file for {label}"},
                                    files={"file": (fname, _pdf(name, [f"Brand: {label}", f"Asset type: {kind}"]), "application/pdf")}))
                body["document_id"] = doc["id"]
            c.ok(mk.post("/api/branding/assets", json=body))
            made["assets"] += 1
    have_social = {(r[0], r[1]) for r in rows("SELECT platform, handle FROM social_accounts")}
    for platform, handle, bcode in SOCIAL_SPECS:
        if (platform, handle) in have_social or bcode not in brands:
            continue
        c.ok(mk.post("/api/branding/social-accounts", json={"platform": platform, "handle": handle, "brand_id": brands[bcode]}))
        made["social"] += 1
    have_cat = {r[0] for r in rows("SELECT code FROM product_categories")}
    for code, name, kind in CATEGORY_SPECS:
        if code not in have_cat:
            c.ok(adm.post("/api/masters/categories", json={"code": code, "name": name, "kind": kind}))
            made["categories"] += 1
    have_loc = {r[0] for r in rows("SELECT code FROM storage_locations")}
    for wid, wcode in rows("SELECT id, code FROM warehouses WHERE code NOT IN ('REJ', 'WIP', 'QC-QRN') ORDER BY id"):
        for n in (1, 2, 3):
            code = f"{wcode}-R{n}"
            if code not in have_loc:
                c.ok(adm.post("/api/masters/locations", json={"code": code, "name": f"Rack {n}", "warehouse_id": wid}))
                made["locations"] += 1
    return made


# ================================================================== freight charges on shipments
@step("depth_shipping_charges")
def depth_shipping_charges(c: Ctx) -> dict:
    """Record the courier's freight charge on every booked, in-transit or delivered shipment that has none (45 to 184 rupees, varying by shipment), through the real endpoint,
    so the dispatch dashboard shows what shipping actually cost and which shipments still lack a charge."""
    disp = c.u("disp")
    todo = rows("SELECT id FROM shipments WHERE shipping_charge IS NULL AND status NOT IN ('PENDING_BOOKING', 'BOOKING') ORDER BY id")
    for (sid,) in todo:
        amount = 45 + (sid * 37) % 140 + (0.5 if sid % 3 == 0 else 0)
        c.ok(disp.post(f"/api/dispatch/shipments/{sid}/shipping-charge", json={"amount": f"{amount:.2f}"}))
    return {"shipments": len(todo)}
