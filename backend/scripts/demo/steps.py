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


@step("production_planning")
def production_planning(c: Ctx) -> dict:
    """A confirmed weekly plan: one line is short of materials (it waits on the surfactant PO that is still awaiting approval) and over a work centre's capacity (overload accepted with a
    reason); one line is converted to a planned order. Plus a DELAYED order that is mid-production and a future order."""
    items, pm = item_ids(c), c.u("pm")
    boms, wcs, W = c.refs["production_finished_goods"]["boms"], c.refs["master_data"]["wc"], c.refs["foundation"]["wh_rm"]
    plan = c.ok(pm.post("/api/production/plans", json={"name": "Week 46: Oud wash push + Olive butter", "period_start": c.d(-1), "period_end": c.d(10), "notes": "Demo plan", "lines": [
        {"bom_id": boms["FG-BW-OUD-200"], "planned_qty": "1000", "planned_date": c.d(3), "warehouse_id": W, "work_centre_id": wcs["WC-PACK"]},
        {"bom_id": boms["FG-BB-OLIVE-150"], "planned_qty": "150", "planned_date": c.d(2), "warehouse_id": W, "work_centre_id": wcs["WC-FILL"]}]}))
    mc = c.ok(pm.get(f"/api/production/plans/{plan['id']}/material-check"))
    assert mc["ok"] is False, "the plan is expected to be short of materials (surfactant / bottles)"
    c.expect(pm.post(f"/api/production/plans/{plan['id']}/confirm", json={"expected_version": plan["version"]}), 409, "capacity_exceeded")      # shown refusal, then accepted with a reason
    conf = c.ok(pm.post(f"/api/production/plans/{plan['id']}/confirm", json={"expected_version": plan["version"], "accept_overload_reason": "Second packing shift approved by the plant head"}))
    conv = c.ok(pm.post(f"/api/production/plans/{plan['id']}/lines/2/convert"))                                  # Olive butter: materials available -> a planned order
    delayed = run_order(c, boms["FG-BW-OUD-200"], "200", planned_days=-3, finish="progress", stages_done=1)     # started, stage 1 running, planned date already passed
    future = run_order(c, boms["FG-DETAN-60"], "100", planned_days=5, finish="plan")
    return {"plan": plan["id"], "plan_short_items": [l["item_id"] for l in mc["lines"] if l["short"] != "0.000"], "converted_order": conv["order"]["id"], "delayed_order": delayed["id"], "future_order": future["id"]}


@step("sales_crm")
def sales_crm(c: Ctx) -> dict:
    items, cus = item_ids(c), cus_ids(c)
    sales, adm = c.u("sales"), c.u("admin")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    unit = lambda code, trade=True: money(price[code] * (TRADE if trade else 1))
    src = {s["code"]: s["id"] for s in c.ok(adm.get("/api/crm/lead-sources"))["items"]}
    out: dict = {}
    c.ok(c.u("disp").post("/api/sales/prices", json={"customer_id": cus["DM-CUS-1"], "item_id": items["FG-BW-OUD-200"], "price": unit("FG-BW-OUD-200"), "valid_from": c.d(-90)}))
    # 1) won: lead -> qualified -> customer -> opportunity -> quotation -> accepted -> sales order
    with c.days_ago(14):
        l1 = c.ok(sales.post("/api/crm/leads", json={"name": "Priya Nair", "company": "Studio Bloom Salon, Kochi", "email": "priya@studiobloom.example", "phone": "+910000000101",
                                                    "lead_source_id": src["TRADE-FAIR"], "source": "Trade fair", "notes": "Demo lead: salon range enquiry"}))
        c.ok(sales.post("/api/crm/activities", json={"kind": "CALL", "subject": "Intro call: salon range and trade pricing", "duration_min": 20, "lead_id": l1["id"]}))
        c.ok(sales.post(f"/api/crm/leads/{l1['id']}/status", json={"status": "CONTACTED"}))
        c.ok(sales.post("/api/crm/activities", json={"kind": "MEETING", "subject": "Product demo at the salon", "duration_min": 60, "lead_id": l1["id"], "notes": "Wants Oud body wash for retail shelf + backbar"}))
        c.ok(sales.post(f"/api/crm/leads/{l1['id']}/status", json={"status": "QUALIFIED"}))
        conv = c.ok(sales.post(f"/api/crm/leads/{l1['id']}/convert", json={"customer_type": "B2B"}))
        opp = c.ok(sales.post("/api/crm/opportunities", json={"name": "Studio Bloom: Oud wash launch order", "lead_id": l1["id"], "expected_value": "75000", "expected_close": c.d(14)}))
        q = c.ok(sales.post("/api/sales/quotations", json={"customer_id": conv["customer_id"], "valid_until": c.d(30), "notes": "Trade pricing, 30-day validity", "lines": [{"item_id": items["FG-BW-OUD-200"], "qty": "100", "unit_price": unit("FG-BW-OUD-200")}]}))
        opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/quotation", json={"quotation_id": q["id"], "expected_version": opp["version"]}))
        q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/send", json={"expected_version": q["version"]}))
        q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/accept", json={"expected_version": q["version"]}))
        opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "NEGOTIATION", "expected_version": opp["version"], "note": "Customer accepted terms by e-mail"}))
        opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "WON", "expected_version": opp["version"], "note": "PO received"}))
        q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/convert", json={"expected_version": q["version"], "warehouse_id": W}))
    out.update(won_lead=l1["id"], won_customer=conv["customer_id"], won_opportunity=opp["id"], won_quotation=q["id"], won_order=q["sales_order_id"])
    # 2) open: qualified lead, opportunity in negotiation, quotation sent but not yet accepted
    with c.days_ago(5):
        l2 = c.ok(sales.post("/api/crm/leads", json={"name": "Arjun Mehta", "company": "Urban Spa Group", "email": "arjun@urbanspa.example", "lead_source_id": src["REFERRAL"], "source": "Referral"}))
        for st in ("CONTACTED", "QUALIFIED"):
            c.ok(sales.post(f"/api/crm/leads/{l2['id']}/status", json={"status": st}))
        conv2 = c.ok(sales.post(f"/api/crm/leads/{l2['id']}/convert", json={"customer_type": "B2B"}))
        o2 = c.ok(sales.post("/api/crm/opportunities", json={"name": "Urban Spa: 3-outlet body care range", "lead_id": l2["id"], "expected_value": "210000", "expected_close": c.d(20)}))
        q2 = c.ok(sales.post("/api/sales/quotations", json={"customer_id": conv2["customer_id"], "valid_until": c.d(25), "lines": [
            {"item_id": items["FG-BB-OLIVE-150"], "qty": "120", "unit_price": unit("FG-BB-OLIVE-150")}, {"item_id": items["FG-BS-OUD-150"], "qty": "80", "unit_price": unit("FG-BS-OUD-150")}]}))
        o2 = c.ok(sales.post(f"/api/crm/opportunities/{o2['id']}/quotation", json={"quotation_id": q2["id"], "expected_version": o2["version"]}))
        c.ok(sales.post(f"/api/sales/quotations/{q2['id']}/send", json={"expected_version": q2["version"]}))
        o2 = c.ok(sales.post(f"/api/crm/opportunities/{o2['id']}/stage", json={"stage": "NEGOTIATION", "expected_version": o2["version"]}))
        c.ok(sales.post("/api/crm/activities", json={"kind": "EMAIL", "subject": "Sent revised volume pricing", "opportunity_id": o2["id"]}))
    out.update(open_lead=l2["id"], open_customer=conv2["customer_id"], open_opportunity=o2["id"], open_quotation=q2["id"])
    # 3) lost: a qualified opportunity that did not convert, with the reason on record
    with c.days_ago(10):
        l4 = c.ok(sales.post("/api/crm/leads", json={"name": "Meera Iyer", "company": "Aroma Boutique, Pune", "email": "meera@aroma.example", "lead_source_id": src["INSTAGRAM"], "source": "Instagram"}))
        for st in ("CONTACTED", "QUALIFIED"):
            c.ok(sales.post(f"/api/crm/leads/{l4['id']}/status", json={"status": st}))
        o4 = c.ok(sales.post("/api/crm/opportunities", json={"name": "Aroma Boutique: gift sets", "lead_id": l4["id"], "expected_value": "40000", "expected_close": c.d(7)}))
        c.ok(sales.post(f"/api/crm/opportunities/{o4['id']}/stage", json={"stage": "LOST", "expected_version": o4["version"], "lost_reason": "Prefers imported brands; price sensitive"}))
        c.ok(sales.post(f"/api/crm/leads/{l4['id']}/status", json={"status": "LOST", "reason": "Prefers imported brands; price sensitive"}))
    # 4) new + follow-ups (one overdue)
    l3 = c.ok(sales.post("/api/crm/leads", json={"name": "Sana Qureshi", "company": "Velvet Wellness, Hyderabad", "email": "sana@velvet.example", "lead_source_id": src["WEBSITE"], "source": "Website 'Partner With Us'"}))
    with c.days_ago(8):
        c.ok(sales.post("/api/sales/followups", json={"subject": "Send salon catalogue and price list", "due_date": c.d(3), "lead_id": l3["id"], "assigned_to": me(c, "sales")}))       # now overdue
    c.ok(sales.post("/api/sales/followups", json={"subject": "Call back about backbar sizes", "due_date": c.d(2), "lead_id": l3["id"], "assigned_to": me(c, "sales")}))
    c.ok(sales.post(f"/api/sales/customers/{out['won_customer']}/contacts", json={"name": "Priya Nair", "role": "Owner", "email": "priya@studiobloom.example", "is_primary": True}))
    out.update(lost_lead=l4["id"], lost_opportunity=o4["id"], new_lead=l3["id"])
    return out


# ================================================================== dispatch and receivables
def _hook(c: Ctx, awb: str, event_id: str, etype: str, when) -> None:
    import hashlib
    import hmac
    import json
    from app.core.config import get_settings
    raw = json.dumps({"event_id": event_id, "awb": awb, "event_type": etype, "occurred_at": when.isoformat()}).encode()
    sig = hmac.new(get_settings().courier_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    c.ok(c.u("disp").post("/api/dispatch/webhooks/courier/fake", content=raw, headers={"X-Signature": sig, "Content-Type": "application/json"}))


def _ago(days: float, hours: float = 0):
    from datetime import datetime, timezone
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


@step("dispatch_and_receivables")
def dispatch_and_receivables(c: Ctx) -> dict:
    items, cus = item_ids(c), cus_ids(c)
    disp, acct = c.u("disp"), c.u("acct")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    out: dict = {"orders": {}, "invoices": {}, "shipments": {}}

    def new_order(cust: str, lines: list[tuple[str, int]], trade=True, channel="B2B") -> dict:
        return c.ok(disp.post("/api/dispatch/orders", json={"customer_id": cus[cust], "warehouse_id": W, "channel": channel, "lines": [
            {"item_id": items[code], "qty": str(q), "unit_price": money(price[code] * (TRADE if trade else 1))} for code, q in lines]}))

    def advance(o: dict, to: str = "PACKED") -> dict:
        v = o["version"]
        c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": v}))
        a = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/allocate", headers=c.k("al"), json={"expected_version": v + 1}))
        if to == "ALLOCATED":
            return a
        return c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": v + 2, "cartons": 2, "weight_kg": "6.5"}))

    def ship(o: dict, lines: list[tuple[int, str]] | None = None, packages: list[dict] | None = None) -> dict:
        body: dict = {"expected_version": o["version"], "courier": "fake"}
        if lines:
            body["lines"] = [{"so_item_id": i, "qty": q} for i, q in lines]
        if packages:
            body["packages"] = packages
        d = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=c.k("dp"), json=body))
        c.ok(disp.post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
        d["awb"] = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
        return d

    def pay(inv: dict, cust: str, amount: str) -> None:
        c.ok(acct.post("/api/accounts/payments", headers=c.k("pay"), json={"direction": "RECEIVED", "party_id": cus[cust], "amount": amount, "payment_date": c.d(0), "method": "BANK_TRANSFER",
                                                                           "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": amount}]}))

    # 1) the CRM-won order (100 Oud wash) ships in TWO shipments: 60 now-ish (one failed attempt, then delivered with proof), 40 later (still on the road)
    won = c.ok(disp.get(f"/api/dispatch/orders/{c.refs['sales_crm']['won_order']}"))
    with c.days_ago(12):
        packed = advance(won)
        s1 = ship(packed, [(won["items"][0]["id"], "60")], packages=[{"weight_kg": "6.2", "length_cm": "40", "width_cm": "30", "height_cm": "25", "contents": "Oud body wash x 30"}, {"weight_kg": "6.2", "contents": "Oud body wash x 30"}])
        _hook(c, s1["awb"], "w1-pk", "PICKED_UP", _ago(11.8))
        _hook(c, s1["awb"], "w1-it", "IN_TRANSIT", _ago(11))
    c.ok(disp.post(f"/api/dispatch/shipments/{s1['shipment_id']}/delivery-attempts", headers=c.k("att"), json={"outcome": "FAILED", "reason": "Salon closed on Sunday; customer to be called", "attempted_at": _ago(9).isoformat()}))
    c.ok(disp.post(f"/api/dispatch/shipments/{s1['shipment_id']}/delivery-attempts", headers=c.k("att"), json={"outcome": "DELIVERED", "received_by": "P. Nair (owner)", "remarks": "Signed delivery slip", "attempted_at": _ago(8).isoformat()}))
    with c.days_ago(4):
        s2 = ship({"id": won["id"], "version": s1["version"]}, None)                       # the remaining 40
        _hook(c, s2["awb"], "w2-pk", "PICKED_UP", _ago(3.8))
        _hook(c, s2["awb"], "w2-it", "IN_TRANSIT", _ago(3))
    out["orders"]["won_partial"], out["shipments"]["won_1"], out["shipments"]["won_2"] = won["id"], s1["shipment_id"], s2["shipment_id"]
    out["invoices"]["won_1"], out["invoices"]["won_2"] = s1["invoice"]["id"], s2["invoice"]["id"]
    pay(s1["invoice"], _cust_of(c, won["id"]), money(Decimal(s1["invoice"]["total"])))        # shipment 1 invoice paid in full; shipment 2 invoice still open

    # 2) a paid order (20 days ago)
    with c.days_ago(20):
        so1 = new_order("DM-CUS-1", [("FG-BW-OUD-200", 60)])
        d1 = ship(advance(so1))
        _hook(c, d1["awb"], "o1-d", "DELIVERED", _ago(18))
        pay(d1["invoice"], "DM-CUS-1", money(Decimal(d1["invoice"]["total"])))
    out["orders"]["paid"], out["invoices"]["paid"] = so1["id"], d1["invoice"]["id"]
    # 3) an OVERDUE receivable: invoiced 45 days ago on 30-day terms, delivered, nothing paid
    with c.days_ago(45):
        so2 = new_order("DM-CUS-2", [("FG-BB-OLIVE-150", 60), ("FG-BS-OUD-150", 40)], channel="DISTRIBUTOR")
        d2 = ship(advance(so2))
        _hook(c, d2["awb"], "o2-d", "DELIVERED", _ago(43))
    out["orders"]["overdue"], out["invoices"]["overdue"] = so2["id"], d2["invoice"]["id"]
    # 4) a customer return with RESTOCK decision -> credit note -> customer pays the reduced balance
    with c.days_ago(18):
        so3 = new_order("DM-CUS-3", [("FG-BB-OLIVE-150", 30)], trade=False, channel="BRAND")
        d3 = ship(advance(so3))
        _hook(c, d3["awb"], "o3-d", "DELIVERED", _ago(16))
        sh = c.ok(disp.get(f"/api/dispatch/shipments/{d3['shipment_id']}"))
        r = c.ok(disp.post(f"/api/dispatch/returns?order_id={so3['id']}", headers=c.k("rt"), json={"reason": "damaged jar lid on arrival", "lines": [{"shipment_line_id": sh["lines"][0]["id"], "qty": "2"}]}))
        c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/receive", headers=c.k("rc")))
        c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/decide", headers=c.k("dc"), json={"decision": "RESTOCK"}))
        inv3 = c.ok(disp.get(f"/api/dispatch/invoices/{d3['invoice']['id']}"))
        due = Decimal(str(inv3["total"])) - Decimal(str(inv3.get("credited_amount") or 0)) - Decimal(str(inv3.get("amount_paid") or 0))
        pay(d3["invoice"], "DM-CUS-3", money(due))
    out["orders"]["returned"], out["returns"] = so3["id"], r["id"]
    # 5) shipment exceptions: one stuck in transit for 9 days, one delivery that failed
    with c.days_ago(9):
        so4 = new_order("DM-CUS-6", [("FG-BB-OLIVE-150", 20)], channel="DISTRIBUTOR")
        d4 = ship(advance(so4))
        _hook(c, d4["awb"], "o4-pk", "PICKED_UP", _ago(8.8))
        _hook(c, d4["awb"], "o4-it", "IN_TRANSIT", _ago(8))
    with c.days_ago(3):
        so5 = new_order("DM-CUS-5", [("FG-BS-OUD-150", 15)])
        d5 = ship(advance(so5))
        _hook(c, d5["awb"], "o5-pk", "PICKED_UP", _ago(2.8))
        _hook(c, d5["awb"], "o5-ft", "FAILED_DELIVERY", _ago(1))
    with Session(engine) as s:                                       # demo fixture: the courier's "created" age for the delayed shipment (shipments.created_at is set by the database clock)
        from sqlalchemy import text
        s.execute(text("UPDATE shipments SET created_at = now() - interval '9 days' WHERE id = :i"), {"i": d4["shipment_id"]})
        s.commit()
    out["orders"].update(delayed=so4["id"], failed=so5["id"])
    out["shipments"].update(delayed=d4["shipment_id"], failed=d5["shipment_id"])
    # 6) work in progress for the demo: packed (dispatch is the next click), allocated, confirmed-but-QC-blocked, draft
    p = advance(new_order("DM-CUS-1", [("FG-BS-OUD-150", 50)]))
    a = advance(new_order("DM-CUS-2", [("FG-BW-OUD-200", 30)], channel="DISTRIBUTOR"), "ALLOCATED")
    blocked = new_order("DM-CUS-5", [("FG-DETAN-60", 10)])
    c.ok(disp.post(f"/api/dispatch/orders/{blocked['id']}/confirm", json={"expected_version": blocked["version"]}))
    c.expect(disp.post(f"/api/dispatch/orders/{blocked['id']}/allocate", headers=c.k("al"), json={"expected_version": blocked["version"] + 1}), 409, "insufficient_stock")   # batch still in QC quarantine
    d = new_order("DM-CUS-1", [("FG-DETAN-60", 20)])
    out["orders"].update(packed=p["id"], allocated=a["id"], qc_blocked=blocked["id"], draft=d["id"])
    return out


def _cust_of(c: Ctx, order_id: int) -> str:
    cid = c.ok(c.u("disp").get(f"/api/dispatch/orders/{order_id}"))["customer_id"]
    return next(code for code, i in cus_ids(c).items() if i == cid)


# ================================================================== marketplaces (DEMO accounts, sample orders; no live API is contacted)
PLATFORMS = [("AMAZON", "DM-MKT-AMZ", "Amazon"), ("FLIPKART", "DM-MKT-FLK", "Flipkart"), ("MYNTRA", "DM-MKT-MYN", "Myntra"), ("NYKAA", "DM-CUS-4", "Nykaa")]
SKUS = {"AMAZON": ("MT-AMZ-OLIVE-BB", "FG-BB-OLIVE-150"), "FLIPKART": ("MT-FLK-OUDSCRUB", "FG-BS-OUD-150"), "MYNTRA": ("MT-MYN-OLIVE-BB", "FG-BB-OLIVE-150"), "NYKAA": ("MT-NYK-OUDSCRUB", "FG-BS-OUD-150")}


@step("marketplaces")
def marketplaces(c: Ctx) -> dict:
    items, cus = item_ids(c), cus_ids(c)
    mgr, ops, disp, acct = c.u("disp"), c.u("sales"), c.u("disp"), c.u("acct")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    out: dict = {"accounts": {}, "orders": {}}
    for plat, ccode, label in PLATFORMS:
        a = c.ok(mgr.post("/api/ecommerce/accounts", json={"platform": plat, "name": f"[DEMO] {label} seller account (sample data, no live API)", "seller_id": f"DEMO-{plat}-01",
                                                          "customer_id": cus[ccode], "warehouse_id": W}))
        sku, code = SKUS[plat]
        c.ok(mgr.put(f"/api/ecommerce/accounts/{a['id']}/mappings", json={"platform_sku": sku, "item_id": items[code]}))
        out["accounts"][plat] = a["id"]
    with c.days_ago(10):
        for plat, ccode, label in PLATFORMS:
            sku, code = SKUS[plat]
            aid = out["accounts"][plat]
            orders = [{"order_id": f"DEMO-{plat[:3]}-1001", "order_date": c.d(0), "customer_name": f"Online customer ({label}), Bengaluru", "shipping_address": "Indiranagar, Bengaluru", "items": [{"sku": sku, "qty": "3", "unit_price": money(price[code])}]},
                      {"order_id": f"DEMO-{plat[:3]}-1002", "order_date": c.d(0), "customer_name": f"Online customer ({label}), Delhi", "shipping_address": "Saket, Delhi", "items": [{"sku": sku, "qty": "2", "unit_price": money(price[code])}]}]
            first = c.ok(ops.post(f"/api/ecommerce/accounts/{aid}/orders", json={"orders": orders}))
            again = c.ok(ops.post(f"/api/ecommerce/accounts/{aid}/orders", json={"orders": orders}))      # the same file imported twice: nothing is duplicated
            assert again["duplicate"] == 2 and again["created"] == 0, again
            out["orders"][plat] = first
    # an order with an unmapped SKU lands in the exception queue; mapping the SKU and retrying creates the sales order
    with c.days_ago(2):
        aid = out["accounts"]["NYKAA"]
        c.ok(ops.post(f"/api/ecommerce/accounts/{aid}/orders", json={"orders": [{"order_id": "DEMO-NYK-2001", "order_date": c.d(0), "customer_name": "Online customer (Nykaa), Mumbai",
                                                                                "items": [{"sku": "MT-NYK-NEWSKU", "qty": "1", "unit_price": "650"}]}]}))
    # fulfil the first Amazon and Nykaa orders: allocate -> pack -> dispatch -> delivered; then settlement and a customer return on Amazon
    def fulfil(plat: str, ext_id: str, delivered: bool = True) -> dict:
        ext = next(e for e in c.ok(mgr.get("/api/ecommerce/external-orders", params={"account_id": out["accounts"][plat], "page_size": 100}))["items"] if e["external_order_id"] == ext_id)
        so = c.ok(disp.get(f"/api/dispatch/orders/{ext['sales_order_id']}"))
        v = so["version"]
        c.ok(disp.post(f"/api/dispatch/orders/{so['id']}/confirm", json={"expected_version": v}))
        c.ok(disp.post(f"/api/dispatch/orders/{so['id']}/allocate", headers=c.k("al"), json={"expected_version": v + 1}))
        pk = c.ok(disp.post(f"/api/dispatch/orders/{so['id']}/pack", json={"expected_version": v + 2, "cartons": 1, "weight_kg": "0.9"}))
        d = c.ok(disp.post(f"/api/dispatch/orders/{so['id']}/dispatch", headers=c.k("dp"), json={"expected_version": pk["version"], "courier": "fake"}))
        c.ok(disp.post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
        awb = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
        if delivered:
            _hook(c, awb, f"mk-{ext_id}", "DELIVERED", _ago(7))
        return {"so": so["id"], "invoice": d["invoice"], "shipment": d["shipment_id"], "ext": ext_id}
    with c.days_ago(9):
        amz = fulfil("AMAZON", "DEMO-AMA-1001")
        nyk = fulfil("NYKAA", "DEMO-NYK-1001")
        fulfil("FLIPKART", "DEMO-FLI-1001", delivered=False)                    # shipped, still in transit
    out["fulfilled"] = {"amazon": amz, "nykaa": nyk}
    # settlement: gross sales of the delivered Amazon order less commission/fees; the bank credit matches -> reconciled; then posted to the books
    gross = Decimal(amz["invoice"]["total"])
    commission, ship_fee, ad = (gross * Decimal("0.15")).quantize(Decimal("0.01")), Decimal("60.00"), Decimal("45.00")
    st = c.ok(mgr.post(f"/api/ecommerce/accounts/{out['accounts']['AMAZON']}/settlements", json={"reference": "DEMO-AMZ-SET-001", "period_start": c.d(-14), "period_end": c.d(-7),
                                                                                                  "gross_sales": str(gross), "commission": str(commission), "shipping_fees": str(ship_fee), "ad_fees": str(ad)}))
    c.ok(mgr.post(f"/api/ecommerce/settlements/{st['id']}/received", json={"received_amount": st["net_settlement"]}))
    c.ok(acct.post(f"/api/accounts/settlements/{st['id']}/post", headers=c.k("set"), json={"bank_account_code": "1100"}))
    out["settlement"] = st["id"]
    # a marketplace return/refund against the delivered Nykaa order
    rt = c.ok(mgr.post("/api/ecommerce/returns", json={"account_id": out["accounts"]["NYKAA"], "platform_return_ref": "DEMO-NYK-RET-01", "external_order_id": "DEMO-NYK-1001", "reason": "Customer changed mind",
                                                       "lines": [{"sku": SKUS["NYKAA"][0], "qty": "1"}]}))
    out["marketplace_return"] = rt["id"]
    return out


# ================================================================== accounts payable
@step("payables")
def payables(c: Ctx) -> dict:
    """Supplier invoices matched to received POs (three-way match), paid in full / partly / not yet; a debit note for a short supply."""
    acct, buyer = c.u("acct"), c.u("buyer")
    sup = sup_ids(c)
    base = c.refs["procurement_baseline"]["po"]
    out: dict = {"invoices": {}}

    def invoice(po_id: int, no: str, days: int, only: list[tuple[int, str]] | None = None) -> dict:
        po = c.ok(buyer.get(f"/api/procurement/purchase-orders/{po_id}"))
        with c.days_ago(days):
            lines = [{"po_item_id": i["id"], "qty": q if only else i["qty_received"], "unit_price": i["unit_price"], "tax_rate": i["tax_rate"]} for i, q in
                     ([(next(x for x in po["items"] if x["id"] == pid), q) for pid, q in only] if only else [(i, None) for i in po["items"]])]
            return c.ok(acct.post("/api/accounts/supplier-invoices", headers=c.k("si"), json={"po_id": po_id, "supplier_invoice_no": no, "invoice_date": c.d(0), "lines": lines}))

    def pay(inv: dict, supplier: str, amount: str, days: int) -> None:
        with c.days_ago(days):
            c.ok(acct.post("/api/accounts/payments", headers=c.k("sp"), json={"direction": "PAID", "party_id": sup[supplier], "amount": amount, "payment_date": c.d(0), "method": "BANK_TRANSFER",
                                                                              "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": amount}]}))

    i1 = invoice(base["1"], "HB/2026/0412", 36)
    pay(i1, "DM-SUP-1", str(i1["total"]), 20)                                                  # paid in full
    i2 = invoice(base["2"], "CO/2026/0188", 34)                                                # invoiced, awaiting payment (older than the 30-day terms -> overdue payable)
    i3 = invoice(base["3"], "PP/26-27/0907", 33)
    pay(i3, "DM-SUP-3", money(Decimal(str(i3["total"])) / 2), 15)                              # half paid
    rfq_po = c.refs["procurement_exceptions"]["rfq_po"]
    i4 = invoice(rfq_po, "GL/26/0061", 27, only=[(c.refs["procurement_exceptions"]["rfq_item_po"], "120")])   # invoice only what has been received (120 of 240 kg)
    # a short-supply debit note on the unpaid Coastal Oils invoice
    dn = c.ok(acct.post(f"/api/accounts/supplier-invoices/{i2['id']}/debit-notes", headers=c.k("dn"), json={"amount_ex_tax": "1500", "tax_rate": "18", "reason_type": "SHORT_SUPPLY", "reason": "2 kg short on Oud fragrance oil delivery"}))
    out["invoices"].update(paid=i1["id"], unpaid=i2["id"], half=i3["id"], rfq=i4["id"])
    out["debit_note"] = dn["id"]
    return out


@step("banking")
def banking(c: Ctx) -> dict:
    """Owner capital, then a bank statement that matches most book entries; one SMS charge is unmatched and one book payment is not on the statement yet."""
    acct = c.u("acct")
    bank_id = c.refs["master_data"]["bank_id"]
    with c.days_ago(60):
        c.ok(acct.post("/api/accounts/journals", headers=c.k("jv"), json={"entry_date": c.d(0), "description": "Owner capital introduced (demo)", "lines": [{"account_code": "1100", "debit": "1500000"}, {"account_code": "3000", "credit": "1500000"}]}))
    with Session(engine) as s:
        from sqlalchemy import text
        rows = s.execute(text("""SELECT l.id, e.entry_date, l.debit - l.credit AS amt, e.description FROM journal_lines l JOIN accounts a ON a.id = l.account_id JOIN journal_entries e ON e.id = l.entry_id
                                 WHERE a.code = '1100' ORDER BY e.entry_date, l.id""")).all()
    on_stmt = rows[:-1]                                              # the latest book entry has not reached the statement yet
    csv_ = "date,description,reference,credit,debit\n" + "".join(f"{r[1]},{(r[3] or 'bank movement')[:40].replace(',', ' ')},,{r[2] if r[2] > 0 else ''},{-r[2] if r[2] < 0 else ''}\n" for r in on_stmt)
    csv_ += f"{c.d(-1)},SMS alert charges,,,23.60\n"
    imp = c.ok(acct.post(f"/api/accounts/bank-accounts/{bank_id}/statements", files={"file": ("demo-statement.csv", csv_, "text/csv")}))
    matched = 0
    for sug in c.ok(acct.get(f"/api/accounts/bank-accounts/{bank_id}/suggestions")):
        cand = sug["candidates"][0]
        r = acct.post(f"/api/accounts/bank-transactions/{sug['txn_id']}/match", json={"journal_line_id": cand["journal_line_id"]})
        matched += r.status_code == 200
    return {"statement_lines": imp["imported"], "matched": matched}


# ================================================================== maintenance
@step("maintenance")
def maintenance(c: Ctx) -> dict:
    """Assets, preventive schedules (one completed, one OVERDUE), a breakdown with downtime and spare-part use."""
    adm, mm = c.u("admin"), c.u("maint")
    eq: dict = {}
    for aid, name, cat, loc, kw in [("EQ-MIX-500", "Planetary Mixer 500 L", "Mixing", "Plant 1", {"manufacturer": "Demo Machinery Co", "capacity": "500 L"}),
                                    ("EQ-FILL-02", "Semi-automatic Filling Line", "Filling", "Plant 1", {"manufacturer": "Demo Machinery Co", "capacity": "1200 units/h"}),
                                    ("EQ-HOMOG-01", "High-shear Homogeniser", "Mixing", "Plant 1", {}), ("EQ-LAB-PH", "Laboratory pH meter", "Laboratory", "QC lab", {})]:
        eq[aid] = c.ok(adm.post("/api/maintenance/equipment", json={"asset_id": aid, "name": name, "category": cat, "location": loc, "calibration_required": aid == "EQ-LAB-PH",
                                                                   "calibration_due": c.d(45) if aid == "EQ-LAB-PH" else None, "purchase_date": c.d(-700), "installation_date": c.d(-690), **kw}))["id"]
    out: dict = {"equipment": eq}
    tech = me(c, "maint")
    # preventive schedules: the mixer's service was due 20 days ago and done; the filling line's was due 8 days ago and is still open (overdue)
    with c.days_ago(35):
        s1 = c.ok(mm.post("/api/maintenance/schedules", json={"equipment_id": eq["EQ-MIX-500"], "title": "Mixer monthly service: seals, gearbox oil, motor check", "frequency_days": 30, "first_due": c.d(15),
                                                             "checklist": ["Check seals", "Replace gearbox oil", "Inspect motor insulation"], "assigned_to": tech}))
    s2 = c.ok(mm.post("/api/maintenance/schedules", json={"equipment_id": eq["EQ-FILL-02"], "title": "Filling line quarterly service: nozzles and pneumatics", "frequency_days": 90, "first_due": c.d(-8),
                                                         "checklist": ["Clean nozzles", "Check air pressure", "Lubricate slides"], "assigned_to": tech}))
    gen = c.ok(mm.post("/api/maintenance/schedules/generate"))
    wos = c.ok(mm.get("/api/maintenance/work-orders", params={"page_size": 100}))
    items = wos["items"] if isinstance(wos, dict) else wos
    mix_wo = next(w for w in items if w["equipment_id"] == eq["EQ-MIX-500"] and w["kind"] == "PREVENTIVE")
    c.ok(mm.post(f"/api/maintenance/work-orders/{mix_wo['id']}/assign", json={"assignee_id": tech}))
    c.ok(mm.post(f"/api/maintenance/work-orders/{mix_wo['id']}/start"))
    for i in range(3):
        c.ok(mm.post(f"/api/maintenance/work-orders/{mix_wo['id']}/checklist", json={"index": i, "done": True}))
    c.ok(mm.post(f"/api/maintenance/work-orders/{mix_wo['id']}/spare-parts", json={"part_name": "Gearbox oil 5 L", "qty": 2, "unit_cost": "1250.00"}))
    c.ok(mm.post(f"/api/maintenance/work-orders/{mix_wo['id']}/complete", json={"root_cause": None, "repair_notes": "Oil changed; seals intact."}))
    c.ok(c.u("admin").post(f"/api/maintenance/work-orders/{mix_wo['id']}/verify"))
    # breakdown on the homogeniser: downtime starts, repair uses spare parts, closes the downtime
    with c.days_ago(6):
        bd = c.ok(mm.post("/api/maintenance/breakdowns", json={"equipment_id": eq["EQ-HOMOG-01"], "description": "Mechanical seal leaking; machine stopped", "priority": "HIGH", "production_impact": "Bulk batches delayed by a day"}))
        c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/assign", json={"assignee_id": tech}))
        c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/start"))
        c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/spare-parts", json={"part_name": "Mechanical seal kit", "qty": 1, "unit_cost": "8400.00"}))
        c.ok(mm.post(f"/api/maintenance/work-orders/{bd['id']}/complete", json={"root_cause": "Seal face worn after 14 months", "repair_notes": "Seal kit replaced; pressure-tested."}))
        c.ok(c.u("admin").post(f"/api/maintenance/work-orders/{bd['id']}/verify"))
    out.update(mixer_wo=mix_wo["id"], breakdown_wo=bd["id"], overdue_schedule=s2["id"], generated=gen)
    return out


# ================================================================== branding and marketing (manual recording: no Meta API is called)
@step("branding")
def branding(c: Ctx) -> dict:
    mk, adm = c.u("mkt"), c.u("admin")
    brands = c.ok(adm.get("/api/masters/brands", params={"page_size": 20}))["items"]
    if brands:
        brand_id = brands[0]["id"]
    else:
        brand_id = c.ok(adm.post("/api/masters/brands", json={"code": "MINTREE", "name": "Mintree"}))["id"]
    camp = c.ok(mk.post("/api/branding/campaigns", json={"name": "Winter Oud collection (demo)", "brand_id": brand_id, "budget": "150000", "start_date": c.d(-20), "end_date": c.d(25)}))
    camp2 = c.ok(mk.post("/api/branding/campaigns", json={"name": "Salon partner outreach (demo)", "brand_id": brand_id, "budget": "60000", "start_date": c.d(-5), "end_date": c.d(40)}))
    c.ok(mk.post("/api/branding/assets", json={"brand_id": brand_id, "kind": "COLOR", "name": "Brand black", "value": "#111111"}))
    c.ok(mk.post("/api/branding/social-accounts", json={"platform": "INSTAGRAM", "handle": "@mintree.demo", "brand_id": brand_id}))      # no credentials: manual recording only
    posts = {}
    with c.days_ago(15):
        for key, title, status in (("published", "Oud body wash reel: the winter ritual", "publish"), ("approved", "Behind the scenes: small-batch mixing", "approve"),
                                   ("rejected", "Flash sale teaser", "reject"), ("pending", "Salon backbar range carousel", "submit"), ("draft", "Customer review round-up", "draft")):
            cont = c.ok(mk.post("/api/branding/content", json={"campaign_id": camp["id"] if key != "pending" else camp2["id"], "channel": "INSTAGRAM", "title": title,
                                                              "caption": "Demo content (recorded manually; nothing is posted by the system)."}))
            posts[key] = cont["id"]
            if status in ("publish", "approve", "reject", "submit"):
                c.ok(mk.post(f"/api/branding/content/{cont['id']}/submit"))
            if status in ("publish", "approve"):
                c.ok(adm.post(f"/api/branding/content/{cont['id']}/approve"))
            if status == "reject":
                c.ok(adm.post(f"/api/branding/content/{cont['id']}/reject", json={"reason": "Discount claim not approved by management"}))
            if status == "publish":
                c.ok(mk.post(f"/api/branding/content/{cont['id']}/publish", json={"external_url": "https://example.com/demo-post"}))
    for day, (reach, imp, clicks, eng, results, spend) in enumerate([(5200, 8100, 310, 640, 18, "4200"), (6100, 9400, 365, 720, 22, "4600"), (4800, 7600, 280, 590, 15, "3900")], 1):
        c.ok(mk.post("/api/branding/metrics", json={"campaign_id": camp["id"], "metric_date": c.d(-day), "reach": reach, "impressions": imp, "clicks": clicks, "engagements": eng, "results": results, "spend": spend}))
    return {"brand": brand_id, "campaigns": [camp["id"], camp2["id"]], "content": posts}
