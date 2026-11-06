"""Demo scenarios, in dependency order. Each step is a function of the context; it may rely on the refs of earlier steps."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.administration.models import Role, User
from app.core.db import engine
from app.core.security import hash_password
from app.masters.models import Customer, Item, Supplier, UnitOfMeasure, Warehouse
from scripts.demo.catalogue import BOMS, CATALOGUE, CUSTOMERS, GST, POS, RAW, SUPPLIERS, TRADE
from scripts.demo.core import PASSWORD, PEOPLE, Ctx, email, money, step

# extra demo masters (illustrative names)
EXTRA_SUPPLIERS = [("DM-SUP-4", "Sunrise Packaging Co", "PM", "Vapi", "GJ"), ("DM-SUP-5", "Greenleaf Essential Oils", "RM", "Mysuru", "KA")]
EXTRA_CUSTOMERS = [("DM-CUS-5", "Lakshmi Wellness Spa", "B2B", "Chennai", "TN"), ("DM-CUS-6", "Urban Glow Distributors", "DISTRIBUTOR", "Delhi", "DL"),
                   ("DM-MKT-AMZ", "Amazon (demo marketplace account)", "MARKETPLACE", "Bengaluru", "KA"), ("DM-MKT-FLK", "Flipkart (demo marketplace account)", "MARKETPLACE", "Bengaluru", "KA"),
                   ("DM-MKT-MYN", "Myntra (demo marketplace account)", "MARKETPLACE", "Bengaluru", "KA")]
EXTRA_RAW = [("RM-DIWATER", "Purified process water", "RM", "KG"), ("RM-GLYCERIN", "Vegetable Glycerin", "RM", "KG"), ("RM-VITE", "Vitamin E Oil", "RM", "KG"), ("PM-CARTON-12", "Shipper carton, 12 units", "PM", "PCS"),
             ("BLK-OUD-WASH", "Oud Body Wash base (bulk)", "BULK", "KG")]
REORDER = {"RM-OUD": "9", "RM-SHEA": "60", "RM-ALOE": "40", "PM-JAR-60": "150", "RM-VITE": "5"}


@step("foundation")
def foundation(c: Ctx) -> dict:
    """Users and master data (the one place records are written directly, like the base seed does)."""
    with Session(engine) as s:
        uom = {u.code: u.id for u in s.scalars(select(UnitOfMeasure))}
        roles = {r.code: r for r in s.scalars(select(Role))}
        for key, (role, name) in PEOPLE.items():
            s.add(User(email=email(key), full_name=name, password_hash=hash_password(PASSWORD), roles=[roles[role]], must_change_password=False))
        for code, name, cat, city, st in SUPPLIERS + EXTRA_SUPPLIERS:
            s.add(Supplier(code=code, name=name, category=cat, city=city, state=st, approval_status="APPROVED", payment_terms_days=30))
        for code, name, ctype, city, st in CUSTOMERS + EXTRA_CUSTOMERS:
            s.add(Customer(code=code, name=name, customer_type=ctype, state=st, billing_address=f"{city}, {st}", shipping_address=f"Warehouse Road, {city}", credit_days=30,
                           credit_limit=Decimal("500000") if ctype in ("B2B", "DISTRIBUTOR") else None))
        for code, name, kind, u in RAW + EXTRA_RAW:
            s.add(Item(item_code=code, name=name, kind=kind, uom_id=uom[u], shelf_life_days=730, gst_rate=18, is_batch_tracked=True, reorder_level=Decimal(REORDER[code]) if code in REORDER else None,
                       description="Demo data"))
        for code, name, price, produced in CATALOGUE:
            s.add(Item(item_code=code, name=name, kind="FG", uom_id=uom["PCS"], shelf_life_days=730, gst_rate=18, hsn_code="33049990",
                       reorder_level=20 if produced else None, description=f"Storefront price Rs {price} incl. GST"))
        s.commit()
        wh = {w.code: w.id for w in s.scalars(select(Warehouse))}
    return {"users": len(PEOPLE), "wh_rm": wh["RM-WH"], "wh_fg": wh["FG-WH"], "wh_rej": wh["REJ"], "wh_qrn": wh["QC-QRN"]}


@step("master_data")
def master_data(c: Ctx) -> dict:
    """Newer masters, created through the API (so validation, permissions and audit apply)."""
    a = c.u("admin")
    uom = {u["code"]: u["id"] for u in c.ok(a.get("/api/masters/units-of-measure", params={"page_size": 100}))["items"]}
    for f, t, factor in (("KG", "G", "1000"), ("L", "ML", "1000"), ("BOX", "PCS", "12")):
        c.ok(a.post("/api/masters/unit-conversions", json={"from_uom_id": uom[f], "to_uom_id": uom[t], "factor": factor}))
    for code, name, hsn, rate in (("GST0", "GST 0% (exempt / nil)", None, "0"), ("GST5", "GST 5%", None, "5"), ("GST12", "GST 12%", None, "12"),
                                  ("GST18-COSM", "GST 18% (cosmetics, HSN 3304)", "3304", "18"), ("GST28", "GST 28%", None, "28")):
        c.ok(a.post("/api/masters/tax-classes", json={"code": code, "name": name, "hsn_prefix": hsn, "gst_rate": rate}))
    for code, name, kind, ledger in (("BANK-TRANSFER", "Bank transfer (NEFT/RTGS/IMPS)", "BANK_TRANSFER", "1100"), ("UPI", "UPI", "UPI", "1100"), ("CASH", "Cash", "CASH", "1000"),
                                     ("CHEQUE", "Cheque", "CHEQUE", "1100"), ("MARKETPLACE", "Marketplace settlement", "MARKETPLACE", "1100")):
        c.ok(a.post("/api/masters/payment-methods", json={"code": code, "name": name, "kind": kind, "ledger_account_code": ledger}))
    # couriers: only the in-process fake adapter is DEMO; the rest are MANUAL (booked by hand). Nothing here claims a live carrier integration.
    for code, name, mode in (("FAKE", "Demo courier (in-process fake adapter, no real carrier)", "DEMO"), ("BLUEDART", "Blue Dart (manual booking)", "MANUAL"), ("DELHIVERY", "Delhivery (manual booking)", "MANUAL")):
        c.ok(a.post("/api/masters/courier-partners", json={"code": code, "name": name, "kind": "COURIER", "integration_mode": mode}))
    for code, name in (("WEBSITE", "Website enquiry"), ("TRADE-FAIR", "Trade fair"), ("REFERRAL", "Referral"), ("INSTAGRAM", "Instagram")):
        c.ok(a.post("/api/crm/lead-sources", json={"code": code, "name": name}))
    qa = c.u("qa")
    sq = c.ok(qa.post("/api/quality/sampling-plans", json={"code": "SQRT-N1", "name": "Incoming lots: root n + 1", "method": "SQRT_PLUS_ONE"}))
    pc = c.ok(qa.post("/api/quality/sampling-plans", json={"code": "PCT-10", "name": "Finished goods: 10% of lot (min 5)", "method": "PERCENT", "percent": "10", "min_samples": 5}))
    wcs = {}
    for code, name, kind, cap in (("WC-MIX", "Mixing room", "MIXING", "500"), ("WC-FILL", "Filling line", "FILLING", "400"), ("WC-PACK", "Packing line", "PACKING", "600"), ("WC-LAB", "QC laboratory", "LAB", None)):
        wcs[code] = c.ok(c.u("pm").post("/api/production/work-centres", json={"code": code, "name": name, "kind": kind, "capacity_per_day": cap}))["id"]
    bank = c.ok(c.u("acct").post("/api/accounts/bank-accounts", json={"name": "Demo current account", "bank_name": "Demo Bank", "account_last4": "0001", "ledger_account_code": "1100", "opening_balance": "0"}))
    rm = c.refs["foundation"]["wh_rm"]
    for code in ("A-01", "A-02", "B-01"):
        c.ok(a.post("/api/masters/locations", json={"code": f"RM-{code}", "name": f"RM rack {code}", "warehouse_id": rm}))
    return {"sampling_incoming": sq["id"], "sampling_fg": pc["id"], "wc": wcs, "bank_id": bank["id"]}


# ================================================================== helpers
RM_CODES = [r[0] for r in RAW + EXTRA_RAW if r[2] in ("RM", "PM")]
_SPEC_CACHE: dict[int, list[dict]] = {}


def item_ids(c: Ctx) -> dict[str, int]:
    if "_items" not in c.refs:
        c.refs["_items"] = {i["item_code"]: i["id"] for i in c.ok(c.u("admin").get("/api/masters/items", params={"page_size": 200}))["items"]}
    return c.refs["_items"]


def sup_ids(c: Ctx) -> dict[str, int]:
    return {s["code"]: s["id"] for s in c.ok(c.u("admin").get("/api/masters/suppliers", params={"page_size": 100}))["items"]}


def cus_ids(c: Ctx) -> dict[str, int]:
    return {s["code"]: s["id"] for s in c.ok(c.u("admin").get("/api/masters/customers", params={"page_size": 100}))["items"]}


def _tests_for(c: Ctx, item_id: int, stage: str) -> list[dict]:
    specs = c.ok(c.u("qa").get("/api/quality/specs", params={"item_id": item_id}))
    specs = specs["items"] if isinstance(specs, dict) else specs
    return next(s for s in specs if s["stage"] == stage and s["status"] == "ACTIVE")["tests"]


def inspect_batch(c: Ctx, batch_id: int, outcome: str = "pass", reason: str = "Assay below specification") -> dict:
    """The real QC path: the inspector records results (judged server-side), a DIFFERENT quality manager decides. outcome: pass | fail | leave_open | leave_partial"""
    qa, qi = c.u("qa"), c.u("qi")
    insp = c.ok(qa.get("/api/quality/inspections", params={"batch_id": batch_id}))["items"]
    if not insp:
        c.ok(qa.post(f"/api/inventory/batches/{batch_id}/qc-decision", headers=c.k("qc"), json={"decision": "RELEASED" if outcome == "pass" else "REJECTED", "reason": None if outcome == "pass" else reason}))
        return {"inspection": None}
    iid = insp[0]["id"]
    if outcome == "leave_open":
        return {"inspection": iid}
    item_id = c.ok(qa.get(f"/api/inventory/batches/{batch_id}"))["batch"]["item_id"]
    tests = _tests_for(c, item_id, insp[0]["stage"])
    for i, t in enumerate(tests):
        if outcome == "leave_partial" and i > 0:
            break
        if t["pass_fail"]:
            body = {"test_name": t["test_name"], "passed": True}
        else:
            lo, hi = Decimal(t["min"] or "0"), Decimal(t["max"] or t["min"] or "0")
            val = (lo + hi) / 2 if outcome != "fail" else (hi + Decimal("5") if t["max"] else lo - Decimal("5"))
            body = {"test_name": t["test_name"], "value": str(val)}
        c.ok(qi.post(f"/api/quality/inspections/{iid}/results", json=body))
    if outcome in ("leave_partial",):
        return {"inspection": iid}
    if outcome == "fail":
        c.ok(qa.post(f"/api/quality/inspections/{iid}/decide", headers=c.k("dec"), json={"decision": "REJECT", "reason": reason}))
    else:
        c.ok(qa.post(f"/api/quality/inspections/{iid}/decide", headers=c.k("dec"), json={"decision": "RELEASE"}))
    return {"inspection": iid}


def receive(c: Ctx, po: dict, lines: list[tuple[int, str]], dn: str, lot_tag: str, wh: int | None = None, mfg_days: int = -12) -> dict:
    """Goods receipt for (po_item_id, qty) pairs with deterministic delivery note and lots."""
    return c.ok(c.u("stores").post("/api/procurement/goods-receipts", headers=c.k("gr"), json={
        "po_id": po["id"], "warehouse_id": wh or c.refs["foundation"]["wh_rm"], "delivery_note_no": dn,
        "lines": [{"po_item_id": pid, "qty": q, "supplier_lot_no": f"LOT-{lot_tag}-{n}", "mfg_date": c.d(mfg_days), "expiry_date": c.d(500)} for n, (pid, q) in enumerate(lines, 1)]}))


def buy(c: Ctx, supplier: str, lines: list[tuple[str, str, str]], expected: int = 7, approve: bool = True, note: str | None = None) -> dict:
    items, sup = item_ids(c), sup_ids(c)
    po = c.ok(c.u("buyer").post("/api/procurement/purchase-orders", json={"supplier_id": sup[supplier], "expected_date": c.d(expected), "notes": note or "Demo data",
                                                                          "items": [{"item_id": items[code], "qty": q, "unit_price": p} for code, q, p in lines]}))
    po = c.ok(c.u("buyer").post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
    if approve:
        po = c.ok(c.u("admin").post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
    return po


# ================================================================== quality specifications
@step("quality_specs")
def quality_specs(c: Ctx) -> dict:
    items, m = item_ids(c), c.refs["master_data"]
    qa = c.u("qa")
    for code in RM_CODES:
        c.ok(qa.post("/api/quality/specs", json={"item_id": items[code], "stage": "INCOMING", "sampling_plan_id": m["sampling_incoming"], "tests": [
            {"test_name": "Appearance", "is_pass_fail": True, "method": "Visual"}, {"test_name": "Moisture", "unit": "%", "min_value": "0", "max_value": "5", "method": "Karl Fischer"}]}))
    for code, *_ in CATALOGUE:
        if code in BOMS:
            c.ok(qa.post("/api/quality/specs", json={"item_id": items[code], "stage": "FINISHED", "sampling_plan_id": m["sampling_fg"], "tests": [
                {"test_name": "Appearance", "is_pass_fail": True, "method": "Visual"}, {"test_name": "pH", "unit": "", "min_value": "4.5", "max_value": "6.5", "method": "pH meter"}]}))
    return {"incoming_specs": len(RM_CODES), "finished_specs": len(BOMS)}


# ================================================================== procurement
@step("procurement_baseline")
def procurement_baseline(c: Ctx) -> dict:
    """Purchase request (approved and rejected examples), purchase orders, receipts and incoming QC -- backdated ~40 days so history looks lived-in."""
    items = item_ids(c)
    out = {"po": {}, "batches": {}}
    with c.days_ago(40):
        for label, ok_ in (("approved", True), ("rejected", False)):
            pr = c.ok(c.u("buyer").post("/api/procurement/purchase-requests", json={"items": [{"item_id": items["RM-ALOE"], "qty": "60"}], "required_by": c.d(14), "notes": f"Aloe restock ({label} example)"}))
            pr = c.ok(c.u("buyer").post(f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]}))
            if ok_:
                c.ok(c.u("admin").post(f"/api/procurement/purchase-requests/{pr['id']}/approve", json={"expected_version": pr["version"]}))
            else:
                c.ok(c.u("admin").post(f"/api/procurement/purchase-requests/{pr['id']}/reject", json={"expected_version": pr["version"]}))
            out[f"pr_{label}"] = pr["id"]
        for n, (sup, lines, action) in enumerate(POS[:4], 1):
            po = buy(c, sup, lines)
            gr = receive(c, po, [(pi["id"], pi["qty_ordered"]) for pi in po["items"]], f"DN-DM-{n:03d}", f"DM{n}")
            out["po"][str(n)] = po["id"]
            out["batches"][str(n)] = [ln["batch_id"] for ln in gr["lines"]]
            if action == "release":
                for ln in gr["lines"]:
                    inspect_batch(c, ln["batch_id"], "pass")
            else:                                                # received, inspection started but undecided: a QC release waiting for the quality manager
                for ln in gr["lines"]:
                    inspect_batch(c, ln["batch_id"], "leave_partial")
    # PO awaiting approval (pending purchase approval for the demo)
    po5 = buy(c, POS[4][0], POS[4][1], approve=False)
    pr = c.ok(c.u("buyer").post("/api/procurement/purchase-requests", json={"items": [{"item_id": items["RM-SURF"], "qty": "300"}], "required_by": c.d(10), "notes": "Surfactant for the next Oud wash batches"}))
    c.ok(c.u("buyer").post(f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]}))
    out["po_pending_approval"], out["pr_pending"] = po5["id"], pr["id"]
    return out


def me(c: Ctx, key: str) -> int:
    return c.ok(c.u(key).get("/api/auth/me"))["id"]


@step("procurement_exceptions")
def procurement_exceptions(c: Ctx) -> dict:
    """RFQ comparison and award; a partial receipt; a PO amendment; a late supplier; a failed incoming inspection that ends in a deviation, CAPA and a purchase return."""
    items, sup = item_ids(c), sup_ids(c)
    buyer, admin, qa = c.u("buyer"), c.u("admin"), c.u("qa")
    out: dict = {}
    # ---- A. RFQ -> two quotations -> comparison -> award -> PO -> PARTIAL receipt -> QC release (30 days ago)
    with c.days_ago(30):
        rfq = c.ok(buyer.post("/api/procurement/rfqs", json={"items": [{"item_id": items["RM-GLYCERIN"], "qty": "200"}], "supplier_ids": [sup["DM-SUP-2"], sup["DM-SUP-5"]], "notes": "Glycerin for the winter range"}))
        c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/send"))
        c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/quotations", json={"supplier_id": sup["DM-SUP-2"], "lead_time_days": 7, "freight": "0", "lines": [{"item_id": items["RM-GLYCERIN"], "unit_price": "98"}]}))
        c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/quotations", json={"supplier_id": sup["DM-SUP-5"], "lead_time_days": 12, "freight": "400", "lines": [{"item_id": items["RM-GLYCERIN"], "unit_price": "90"}]}))
        cmp = c.ok(buyer.get(f"/api/procurement/rfqs/{rfq['id']}/comparison"))
        aw = c.ok(buyer.post(f"/api/procurement/rfqs/{rfq['id']}/award", json={"quotation_id": cmp["recommended_quotation_id"]}))
        po = aw["purchase_order"]
        po = c.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
        po = c.ok(admin.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
        gr = receive(c, po, [(po["items"][0]["id"], "120")], "DN-DM-RFQ-01", "RFQ1")          # 120 of 200 kg arrive first: PARTIALLY_RECEIVED
        inspect_batch(c, gr["lines"][0]["batch_id"], "pass")
        out.update(rfq_id=rfq["id"], rfq_po=po["id"], rfq_recommended=cmp["recommended_quotation_id"], rfq_item_po=po["items"][0]["id"])
        # ---- B. an approved PO is amended (more quantity, agreed price) -- needs a second person's approval
        am = c.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/amendments", json={"reason": "Winter demand: supplier agreed +40 kg at the same price", "lines": [{"po_item_id": po["items"][0]["id"], "new_qty": "240"}]}))
        c.ok(admin.post(f"/api/procurement/po-amendments/{am['id']}/approve", json={}))
        out["amendment_id"] = am["id"]
    # ---- C. a late supplier delivery: approved, expected 5 days ago, nothing received
    late = buy(c, "DM-SUP-4", [("PM-CARTON-12", "500", "11")], expected=-5, note="Demo: supplier is late")
    out["late_po"] = late["id"]
    # ---- D. a failed incoming inspection -> REJECT -> deviation + CAPA -> purchase return to the supplier (20 days ago)
    with c.days_ago(20):
        po = buy(c, "DM-SUP-5", [("RM-VITE", "30", "1850")], note="Demo: this lot fails incoming QC")
        gr = receive(c, po, [(po["items"][0]["id"], "30")], "DN-DM-VITE-01", "VITE1")
        bid = gr["lines"][0]["batch_id"]
        # first attempt fails (moisture out of range) ...
        inspect_batch(c, bid, "fail", reason="Moisture above the 5% limit")
        dev = c.ok(qa.post("/api/quality/deviations", json={"title": "Vitamin E oil lot failed incoming moisture test", "description": "Moisture 9.8% vs max 5%. Supplier DM-SUP-5, lot LOT-VITE1-1.",
                                                           "classification": "MAJOR", "batch_id": bid}))
        c.ok(qa.post(f"/api/quality/deviations/{dev['id']}/investigate", json={"root_cause": "Drum seal damaged in transit; moisture ingress"}))
        capa = c.ok(qa.post(f"/api/quality/deviations/{dev['id']}/capa", json={"kind": "CORRECTIVE", "description": "Supplier to re-ship with sealed, desiccated drums", "responsible_id": me(c, "qa2"), "due_date": c.d(10)}))
        c.ok(c.u("qa2").post(f"/api/quality/capa/{capa['id']}/complete"))      # done, but not yet verified/closed: a task left for the Quality Manager
        gri = c.ok(c.u("stores").get(f"/api/inventory/batches/{bid}"))["source"]["goods_receipt_id"]
        line = c.ok(buyer.get(f"/api/procurement/purchase-orders/{po['id']}"))["items"][0]["id"]
        with Session(engine) as s:
            from sqlalchemy import text
            gr_item = s.scalar(text("SELECT id FROM goods_receipt_items WHERE gr_id = :g"), {"g": gri})
        ret = c.ok(c.u("stores").post(f"/api/procurement/goods-receipts/{gri}/returns", headers=c.k("ret"), json={"reason_type": "QC_REJECTED", "reason": "Failed incoming QC: moisture 9.8% (deviation raised)", "lines": [{"gr_item_id": gr_item, "qty": "30"}]}))
        out.update(vite_batch=bid, vite_deviation=dev["id"], vite_capa=capa["id"], vite_return=ret["id"], vite_po=po["id"])
    # ---- E. a retest: a borderline lot fails once, is retested and passes (results are append-only; the history keeps both)
    with c.days_ago(15):
        po = buy(c, "DM-SUP-1", [("RM-ALOE", "50", "86")], note="Demo: retest example")
        gr = receive(c, po, [(po["items"][0]["id"], "50")], "DN-DM-ALOE-02", "ALOE2")
        bid = gr["lines"][0]["batch_id"]
        insp = c.ok(qa.get("/api/quality/inspections", params={"batch_id": bid}))["items"][0]["id"]
        qi = c.u("qi")
        c.ok(qi.post(f"/api/quality/inspections/{insp}/results", json={"test_name": "Moisture", "value": "5.4"}))              # fails (limit 5)
        c.ok(qi.post(f"/api/quality/inspections/{insp}/results", json={"test_name": "Moisture", "value": "4.1"}))              # retest passes
        c.ok(qi.post(f"/api/quality/inspections/{insp}/results", json={"test_name": "Appearance", "passed": True}))
        c.ok(qa.post(f"/api/quality/inspections/{insp}/decide", headers=c.k("dec"), json={"decision": "RELEASE"}))
        out.update(retest_inspection=insp, retest_batch=bid)
    return out


# ================================================================== R&D -> production
def run_order(c: Ctx, bom_id: int, qty: str, *, warehouse: int | None = None, planned_days: int = 0, finish: str | None = "complete", actual: str | None = None,
              wastage: list[dict] | None = None, stages_done: int | None = None, order_id: int | None = None, user_op: str = "op") -> dict:
    """Production order through the real states: create -> release (reserve materials) -> issue -> stages -> complete (output batch + costing)."""
    pm, op = c.u("pm"), c.u(user_op)
    wh = warehouse or c.refs["foundation"]["wh_rm"]
    if order_id is None:
        o = c.ok(pm.post("/api/production/orders", json={"bom_id": bom_id, "planned_qty": qty, "warehouse_id": wh, "planned_date": c.d(planned_days)}))
    else:
        o = c.ok(pm.get(f"/api/production/orders/{order_id}"))
    if finish == "plan":
        return {"id": o["id"], "number": o["number"]}
    c.ok(pm.post(f"/api/production/orders/{o['id']}/release", headers=c.k("rel"), json={"expected_version": o["version"]}))
    c.ok(pm.post(f"/api/production/orders/{o['id']}/issue", headers=c.k("iss")))
    n = len(o["stages"])
    todo = n if stages_done is None else stages_done
    for seq in range(1, todo + 1):
        c.ok(op.post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={}))
        if seq < todo or finish == "complete":
            c.ok(op.post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={"parameters": {"temp_c": 72}}))
    if finish != "complete":
        return {"id": o["id"], "number": o["number"]}
    ver = c.ok(pm.get(f"/api/production/orders/{o['id']}"))["version"]
    done = c.ok(pm.post(f"/api/production/orders/{o['id']}/complete", headers=c.k("cmp"), json={"expected_version": ver, "actual_qty": actual or qty, "wastage": wastage or []}))
    return {"id": o["id"], "number": o["number"], "batch": done["output_batch_id"], "yield_pct": done.get("yield_pct")}


@step("rnd_to_production")
def rnd_to_production(c: Ctx) -> dict:
    """Project -> formulation v1 -> trial + stability observations -> approval -> BOM v1 -> production. Then a REVISION: v2 approved, BOM v2 supersedes -- v1's order keeps its exact pins."""
    items, rnd, rnd2, pm = item_ids(c), c.u("rnd"), c.u("rnd2"), c.u("pm")
    bulk = items["BLK-OUD-WASH"]
    out: dict = {}
    with c.days_ago(36):                                            # process water for the trial and production batches: bought, received and released like any material
        wpo = buy(c, "DM-SUP-1", [("RM-DIWATER", "2000", "1.5")], note="Demo: purified water")
        wgr = receive(c, wpo, [(wpo["items"][0]["id"], "2000")], "DN-DM-WATER-01", "WATER1")
        inspect_batch(c, wgr["lines"][0]["batch_id"], "pass")
    with c.days_ago(35):
        proj = c.ok(rnd.post("/api/rnd/projects", json={"code": "RND-DM-OUD", "name": "Oud body wash: sulphate-free base", "concept": "Premium, sulphate-free body wash with Oud fragrance",
                                                       "product_category": "Body wash", "target_specification": "pH 5.0-6.0; viscosity 3500-5500 cP", "target_launch_date": c.d(60), "lead_researcher_id": me(c, "rnd")}))
        f = c.ok(rnd.post("/api/rnd/formulations", json={"code": "F-DM-OUD-WASH", "name": "Oud body wash base", "product_item_id": bulk, "batch_size": "100",
                                                        "process_instructions": "Heat water phase to 70C; add surfactant under slow agitation; cool to 40C; add aloe and fragrance.",
                                                        "ingredients": [{"item_id": items["RM-SURF"], "qty": "30"}, {"item_id": items["RM-ALOE"], "qty": "10"}, {"item_id": items["RM-OUD"], "qty": "0.5"}, {"item_id": items["RM-DIWATER"], "qty": "59.5"}]}))
        v1 = f["versions"][0]["id"]
        c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/formulations", json={"formulation_id": f["id"]}))
        c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/status", json={"status": "DEVELOPMENT"}))
        t = c.ok(rnd.post(f"/api/rnd/versions/{v1}/trials", json={"quantity": "1"}))
        for kind, param, val, res in (("STABILITY", "pH after 4 weeks at 40C", "5.4", "PASS"), ("TEXTURE", "Viscosity (cP)", "4200", "PASS"), ("FRAGRANCE", "Oud note retention", "Good", "PASS")):
            c.ok(rnd.post(f"/api/rnd/trials/{t['id']}/observations", json={"kind": kind, "parameter": param, "value": val, "result": res}))
        c.ok(rnd.post(f"/api/rnd/trials/{t['id']}/conclude", json={"status": "PASSED", "observations": "Stable at 40C for 4 weeks; texture and fragrance on target.", "stability_note": "4-week accelerated study passed"}))
        c.ok(rnd.post(f"/api/rnd/versions/{v1}/submit"))
        c.ok(rnd2.post(f"/api/rnd/versions/{v1}/approve"))
        bom1 = c.ok(pm.post("/api/production/boms", json={"formulation_version_id": v1}))
        o1 = run_order(c, bom1["id"], "100", planned_days=0)
        inspect_batch(c, o1["batch"], "pass")
    out.update(project=proj["id"], formulation=f["id"], v1=v1, bom1=bom1["id"], bulk_order_v1=o1["id"], bulk_batch_v1=o1["batch"])
    # ---- revision: lower fragrance cost, add glycerin for slip; the old version stays on record
    with c.days_ago(6):
        v2 = c.ok(rnd.post(f"/api/rnd/formulations/{f['id']}/versions", json={"from_version_no": 1, "revision_note": "Add 2% glycerin for slip; reduce oud oil to 0.45 kg"}))["id"]
        c.ok(rnd.patch(f"/api/rnd/versions/{v2}", json={"ingredients": [{"item_id": items["RM-SURF"], "qty": "28"}, {"item_id": items["RM-ALOE"], "qty": "10"}, {"item_id": items["RM-OUD"], "qty": "0.45"},
                                                                         {"item_id": items["RM-GLYCERIN"], "qty": "2"}, {"item_id": items["RM-DIWATER"], "qty": "59.55"}]}))
        t2 = c.ok(rnd.post(f"/api/rnd/versions/{v2}/trials", json={"quantity": "1"}))
        c.ok(rnd.post(f"/api/rnd/trials/{t2['id']}/observations", json={"kind": "STABILITY", "parameter": "pH after 4 weeks at 40C", "value": "5.5", "result": "PASS"}))
        c.ok(rnd.post(f"/api/rnd/trials/{t2['id']}/conclude", json={"status": "PASSED", "observations": "Slip improved; stability unchanged."}))
        c.ok(rnd.post(f"/api/rnd/versions/{v2}/submit"))
        c.ok(rnd2.post(f"/api/rnd/versions/{v2}/approve"))
        bom2 = c.ok(pm.post("/api/production/boms", json={"formulation_version_id": v2}))
        o2 = run_order(c, bom2["id"], "100", planned_days=0, finish="progress", stages_done=2)      # v2 is in production right now (stage 2 running)
    c.ok(rnd.post(f"/api/rnd/projects/{proj['id']}/status", json={"status": "TRIALS"}))
    out.update(v2=v2, bom2=bom2["id"], bulk_order_v2=o2["id"])
    return out


@step("production_finished_goods")
def production_finished_goods(c: Ctx) -> dict:
    """Four finished products through the whole chain (backdated), one with a low yield and recorded wastage; FG inspections, a CoA, and one batch left in quarantine."""
    items = item_ids(c)
    pm, pm2, qa = c.u("pm"), c.u("pm2"), c.u("qa")
    out: dict = {"boms": {}, "orders": {}, "batches": {}}
    with c.days_ago(25):
        for code, (size, lines) in BOMS.items():
            b = c.ok(pm.post("/api/production/boms/manual", json={"output_item_id": items[code], "output_qty": str(size), "lines": [{"item_id": items[i], "qty": q} for i, q in lines]}))
            c.ok(pm2.post(f"/api/production/boms/{b['id']}/approve"))
            out["boms"][code] = b["id"]
        for code, (size, _) in BOMS.items():
            low = code == "FG-BS-OUD-150"                                # one batch under-yields: wastage must be recorded with a reason
            actual = str(size - 12) if low else str(size - max(2, size // 50))
            w = [{"category": "SPILLAGE", "qty": "9", "reason": "Jar-filling line overflow during changeover", "department": "Production"},
                 {"category": "REJECT", "qty": "3", "reason": "Cracked jars removed at inspection", "department": "Packing"}] if low else []
            r = run_order(c, out["boms"][code], str(size), planned_days=0, actual=actual, wastage=w)
            out["orders"][code], out["batches"][code] = r["id"], r["batch"]
    # finished-goods QC (inspector records, a different manager decides). Detan face mask: batch stays in QUARANTINE -> a QC release is waiting.
    for code, bid in out["batches"].items():
        if code == "FG-DETAN-60":
            inspect_batch(c, bid, "leave_partial")
            continue
        r = inspect_batch(c, bid, "pass")
        if code == "FG-BW-OUD-200":
            coa = c.ok(qa.post(f"/api/quality/inspections/{r['inspection']}/coa"))
            out["coa_id"] = coa["id"]
    return out
