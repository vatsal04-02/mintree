"""Demo data: a small catalogue modelled on the public Mintree storefront (vmintree.in: product names, pack sizes and shelf prices),
pushed through the REAL workflows so every module has records in a believable state.

    cd backend && python -m scripts.demo_data            # needs the same MINTREE_* environment as the API (database, storage root)

Everything below the master data goes through the HTTP API in-process (same permissions, approvals, costing and ledger postings as production),
so nothing is inserted behind the business rules' back. Master data and demo users are created directly, like the seed does.

What is illustrative, not factual: supplier and customer names, raw-material costs, trade (B2B) discounts, quantities and dates. Only the product
names, sizes and shelf prices come from the storefront. Refuses to run twice; run against an empty or disposable database."""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import engine
from app.core.security import hash_password
from app.main import app
from app.masters.models import Customer, Item, Supplier, UnitOfMeasure, Warehouse
from app.administration.models import Role, User
from scripts.check_integrity import run_checks

PASSWORD = "Mintree-Demo#2026"
GST = Decimal("1.18")
TRADE = Decimal("0.65")            # B2B / salon price as a share of the shelf price (illustrative)

# name, shelf price incl. GST (storefront), pack, produced through the real chain?
CATALOGUE = [
    ("FG-DETAN-60", "100% ORGANIC Detan Face Mask 60 gm", 1423, True),
    ("FG-FALLSTOP-200", "Fall Stop Shampoo & Conditioner 200 ml each", 1846, False),
    ("FG-BW-OUD-200", "NOURISHING Oud Body Wash 200 ml", 991, True),
    ("FG-BW-FANTASEA-200", "NOURISHING Fantasea Body Wash 200 ml", 991, False),
    ("FG-BW-CALM-200", "NOURISHING Calm-A-Sutra Body Wash 200 ml", 991, False),
    ("FG-BW-OLIVE-200", "NOURISHING Olive Body Wash 200 ml", 991, False),
    ("FG-BB-OLIVE-150", "MOISTURIZING Olive Body Butter 150 gm", 991, True),
    ("FG-BS-OLIVE-150", "BRIGHTENING Olive Body Scrub 150 gm", 991, False),
    ("FG-BS-OUD-150", "BRIGHTENING Oud Body Scrub 150 gm", 991, True),
    ("FG-BS-FANTASEA-150", "BRIGHTENING Fantasea Body Scrub 150 gm", 991, False),
    ("FG-BL-OUD-200", "HYDRATING Oud Body Lotion 200 ml", 991, False),
    ("FG-HC-5X30", "MOISTURIZING HANDCREAMS (PACK OF 5) 30 gm each", 1999, False),
]
RAW = [  # code, name, kind, uom, gst
    ("RM-ALOE", "Aloe Vera Gel, organic", "RM", "KG"), ("RM-SURF", "Mild Surfactant Base (sulphate-free)", "RM", "KG"),
    ("RM-KAOLIN", "Organic Kaolin Clay", "RM", "KG"), ("RM-OLIVE", "Cold-pressed Olive Oil", "RM", "KG"),
    ("RM-SHEA", "Shea Butter, refined", "RM", "KG"), ("RM-OUD", "Oud Fragrance Oil", "RM", "KG"),
    ("PM-BOTTLE-200", "Body Wash Bottle 200 ml", "PM", "PCS"), ("PM-JAR-150", "Jar 150 gm, wooden cap", "PM", "PCS"), ("PM-JAR-60", "Jar 60 gm", "PM", "PCS"),
]
SUPPLIERS = [("DM-SUP-1", "Himalayan Botanicals Pvt Ltd", "RM", "Dehradun", "UK"), ("DM-SUP-2", "Coastal Oils & Butters LLP", "RM", "Kochi", "KL"), ("DM-SUP-3", "Prime Pack Industries", "PM", "Daman", "DD")]
CUSTOMERS = [("DM-CUS-1", "Glow Retail Pvt Ltd", "B2B", "Pune", "MH"), ("DM-CUS-2", "Aura Salon Chain", "DISTRIBUTOR", "Mumbai", "MH"),
             ("DM-CUS-3", "Mintree Online Store", "BRAND", "Gurugram", "HR"), ("DM-CUS-4", "Nykaa Marketplace", "BRAND", "Mumbai", "MH")]
# product -> (batch size in pcs, [(raw code, qty per batch)])
BOMS = {
    "FG-BW-OUD-200": (200, [("RM-SURF", "30"), ("RM-ALOE", "8"), ("RM-OUD", "0.6"), ("PM-BOTTLE-200", "200")]),
    "FG-BB-OLIVE-150": (150, [("RM-SHEA", "20"), ("RM-OLIVE", "10"), ("RM-ALOE", "2"), ("PM-JAR-150", "150")]),
    "FG-BS-OUD-150": (150, [("RM-KAOLIN", "15"), ("RM-OLIVE", "6"), ("RM-OUD", "0.4"), ("PM-JAR-150", "150")]),
    "FG-DETAN-60": (100, [("RM-KAOLIN", "8"), ("RM-ALOE", "3"), ("PM-JAR-60", "100")]),
}
POS = [  # supplier, [(raw code, qty, unit price)], action
    ("DM-SUP-1", [("RM-ALOE", "120", "85"), ("RM-SURF", "200", "140"), ("RM-KAOLIN", "80", "60")], "release"),
    ("DM-SUP-2", [("RM-OLIVE", "150", "420"), ("RM-SHEA", "100", "510"), ("RM-OUD", "10", "6200")], "release"),
    ("DM-SUP-3", [("PM-BOTTLE-200", "600", "14"), ("PM-JAR-150", "400", "22"), ("PM-JAR-60", "300", "18")], "release"),
    ("DM-SUP-2", [("RM-SHEA", "20", "505")], "quarantine"),      # received, QC decision still open
    ("DM-SUP-1", [("RM-SURF", "300", "138")], "approval"),       # submitted, waiting for approval
]


def k() -> dict:
    return {"Idempotency-Key": f"demo-{uuid.uuid4().hex[:16]}"}


def login(email: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def ok(r, code=(200, 201)):
    if r.status_code not in code:
        raise SystemExit(f"FAILED {r.request.method} {r.request.url.path}: {r.status_code} {r.text[:400]}")
    return r.json()


def money(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), ROUND_HALF_UP))


def main() -> None:
    logging.disable(logging.ERROR)                     # the API logs every request as JSON; this script prints its own progress
    with Session(engine) as s:
        if s.scalar(select(Supplier).where(Supplier.code == "DM-SUP-1")):
            raise SystemExit("Demo data is already present (supplier DM-SUP-1 exists). Use a fresh database.")
        uom = {u.code: u.id for u in s.scalars(select(UnitOfMeasure))}
        wh = {w.code: w.id for w in s.scalars(select(Warehouse))}
        roles = {r.code: r for r in s.scalars(select(Role))}
        people = {"admin": "system_admin", "buyer": "purchase_manager", "stores": "stores_manager", "qa": "quality_manager", "pm": "production_manager",
                  "pm2": "production_manager", "op": "production_operator", "disp": "dispatch_manager", "sales": "sales_operator", "acct": "accounts_manager",
                  "mgmt": "management"}
        for key, role in people.items():
            s.add(User(email=f"demo.{key}@example.com", full_name=f"Demo {key.title()}", password_hash=hash_password(PASSWORD), roles=[roles[role]], must_change_password=False))
        for code, name, cat, city, st in SUPPLIERS:
            s.add(Supplier(code=code, name=name, category=cat, city=city, state=st, approval_status="APPROVED", payment_terms_days=30))
        for code, name, ctype, city, st in CUSTOMERS:
            s.add(Customer(code=code, name=name, customer_type=ctype, state=st, billing_address=f"{city}, {st}", shipping_address=f"Warehouse Road, {city}", credit_days=30))
        for code, name, kind, u in RAW:
            s.add(Item(item_code=code, name=name, kind=kind, uom_id=uom[u], shelf_life_days=730, gst_rate=18, is_batch_tracked=True))
        for code, name, price, produced in CATALOGUE:
            s.add(Item(item_code=code, name=name, kind="FG", uom_id=uom["PCS"], shelf_life_days=730, gst_rate=18, hsn_code="33049990",
                       reorder_level=20 if produced else None, description=f"Storefront price Rs {price} incl. GST"))
        s.commit()
        item = {i.item_code: i.id for i in s.scalars(select(Item))}
        sup = {x.code: x.id for x in s.scalars(select(Supplier))}
        cus = {x.code: x.id for x in s.scalars(select(Customer))}
    W = wh["RM-WH"]                                    # one working store keeps issue / output / dispatch on a single stock location, as the tested flows do
    u = {key: login(f"demo.{key}@example.com") for key in people}
    print("Master data: 12 products, 9 materials, 3 suppliers, 4 customers, 11 demo users")

    # ---- procurement: PR, POs, receipts, QC ----
    pr = ok(u["buyer"].post("/api/procurement/purchase-requests", json={"items": [{"item_id": item["RM-ALOE"], "qty": "60"}], "required_by": str(date.today() + timedelta(days=14)), "notes": "Aloe restock"}))
    ok(u["buyer"].post(f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]}))
    for sup_code, lines, action in POS:
        po = ok(u["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": sup[sup_code], "expected_date": str(date.today() + timedelta(days=7)),
                                                                          "items": [{"item_id": item[c], "qty": q, "unit_price": p} for c, q, p in lines]}))
        po = ok(u["buyer"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
        if action == "approval":
            continue
        po = ok(u["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
        gr = ok(u["stores"].post("/api/procurement/goods-receipts", headers=k(), json={
            "po_id": po["id"], "warehouse_id": W, "delivery_note_no": f"DN-{uuid.uuid4().hex[:6].upper()}",
            "lines": [{"po_item_id": pi["id"], "qty": pi["qty_ordered"], "supplier_lot_no": f"LOT-{uuid.uuid4().hex[:5].upper()}",
                       "mfg_date": str(date.today() - timedelta(days=12)), "expiry_date": str(date.today() + timedelta(days=500))} for pi in po["items"]]}))
        if action == "release":
            for ln in gr["lines"]:
                ok(u["qa"].post(f"/api/inventory/batches/{ln['batch_id']}/qc-decision", headers=k(), json={"decision": "RELEASED"}))
    print("Procurement: 1 purchase request, 5 purchase orders (3 received and QC-released, 1 in quarantine, 1 awaiting approval)")

    # ---- production: BOM -> order -> issue -> stages -> completion -> QC release ----
    fg_batch: dict[str, int] = {}
    boms = {}
    for code, (size, lines) in BOMS.items():
        b = ok(u["pm"].post("/api/production/boms/manual", json={"output_item_id": item[code], "output_qty": str(size), "lines": [{"item_id": item[c], "qty": q} for c, q in lines]}))
        ok(u["pm2"].post(f"/api/production/boms/{b['id']}/approve"))
        boms[code] = (b["id"], size)
    for code, (bom_id, size) in boms.items():
        o = ok(u["pm"].post("/api/production/orders", json={"bom_id": bom_id, "planned_qty": str(size), "warehouse_id": W, "planned_date": str(date.today() - timedelta(days=2))}))
        ok(u["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": o["version"]}))
        ok(u["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k()))
        n = len(o["stages"])
        for seq in range(1, n + 1):
            ok(u["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={}))
            ok(u["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={"parameters": {"temp_c": 72}}))
        actual = size - max(2, size // 50)                                                    # a realistic shortfall against plan
        ver = ok(u["pm"].get(f"/api/production/orders/{o['id']}"))["version"]
        done = ok(u["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": ver, "actual_qty": str(actual)}))
        ok(u["qa"].post(f"/api/inventory/batches/{done['output_batch_id']}/qc-decision", headers=k(), json={"decision": "RELEASED"}))
        fg_batch[code] = done["output_batch_id"]
    # one order in progress and one only planned, so the board shows more than finished work
    o2 = ok(u["pm"].post("/api/production/orders", json={"bom_id": boms["FG-BW-OUD-200"][0], "planned_qty": "200", "warehouse_id": W, "planned_date": str(date.today() - timedelta(days=1))}))
    ok(u["pm"].post(f"/api/production/orders/{o2['id']}/release", headers=k(), json={"expected_version": o2["version"]}))
    ok(u["pm"].post(f"/api/production/orders/{o2['id']}/issue", headers=k()))
    ok(u["op"].post(f"/api/production/orders/{o2['id']}/stages/1/start", json={}))
    ok(u["pm"].post("/api/production/orders", json={"bom_id": boms["FG-DETAN-60"][0], "planned_qty": "100", "warehouse_id": W, "planned_date": str(date.today() + timedelta(days=5))}))
    print("Production: 4 BOMs approved, 4 orders completed and QC-released, 1 in progress (past planned date), 1 planned")

    # ---- sales and dispatch ----
    price = {c: Decimal(p) / GST for c, _, p, _ in CATALOGUE}
    def new_order(cust: str, lines: list[tuple[str, int]], trade: bool, channel="B2B") -> dict:
        body = {"customer_id": cus[cust], "warehouse_id": W, "channel": channel,
                "lines": [{"item_id": item[c], "qty": str(q), "unit_price": money(price[c] * (TRADE if trade else 1))} for c, q in lines]}
        return ok(u["disp"].post("/api/dispatch/orders", json=body))
    def advance(o: dict, to: str) -> dict:
        v = o["version"]
        ok(u["disp"].post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": v}))
        a = ok(u["disp"].post(f"/api/dispatch/orders/{o['id']}/allocate", headers=k(), json={"expected_version": v + 1}))
        if to == "ALLOCATED":
            return a
        return ok(u["disp"].post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": v + 2, "cartons": 2, "weight_kg": "6.5"}))
    def dispatch(o: dict) -> dict:
        d = ok(u["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": o["version"]}))
        ok(u["disp"].post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
        return d
    def pay(d: dict, cust: str, amount: str) -> None:
        ok(u["acct"].post("/api/accounts/payments", headers=k(), json={"direction": "RECEIVED", "party_id": cus[cust], "amount": amount, "payment_date": str(date.today()), "method": "BANK_TRANSFER",
                                                                       "bank_account_code": "1100", "allocations": [{"invoice_id": d["invoice"]["id"], "amount": amount}]}))

    so1 = new_order("DM-CUS-1", [("FG-BW-OUD-200", 100)], True)
    d1 = dispatch(advance(so1, "PACKED"))
    pay(d1, "DM-CUS-1", str(d1["invoice"]["total"]))                        # dispatched, invoiced, paid in full
    so2 = new_order("DM-CUS-2", [("FG-BB-OLIVE-150", 60), ("FG-BS-OUD-150", 40)], True, "DISTRIBUTOR")
    dispatch(advance(so2, "PACKED"))                                                                          # dispatched, invoice outstanding
    so3 = new_order("DM-CUS-3", [("FG-DETAN-60", 30)], False, "BRAND")
    d3 = dispatch(advance(so3, "PACKED"))
    sh = ok(u["disp"].get(f"/api/dispatch/shipments/{d3['shipment_id']}"))
    r = ok(u["disp"].post(f"/api/dispatch/returns?order_id={so3['id']}", headers=k(), json={"reason": "damaged jar lid on arrival", "lines": [{"shipment_line_id": sh["lines"][0]["id"], "qty": "2"}]}))
    ok(u["disp"].post(f"/api/dispatch/returns/{r['id']}/receive", headers=k()))
    ok(u["disp"].post(f"/api/dispatch/returns/{r['id']}/decide", headers=k(), json={"decision": "RESTOCK"}))
    inv3 = ok(u["disp"].get(f"/api/dispatch/invoices/{d3['invoice']['id']}"))                                    # the credit note reduces what the customer still owes
    pay(d3, "DM-CUS-3", money(Decimal(str(inv3["total"])) - Decimal(str(inv3.get("credited_amount") or 0)) - Decimal(str(inv3.get("amount_paid") or 0))))
    advance(new_order("DM-CUS-1", [("FG-BS-OUD-150", 50)], True), "PACKED")                                         # packed, awaiting dispatch
    advance(new_order("DM-CUS-2", [("FG-BW-OUD-200", 40)], True, "DISTRIBUTOR"), "ALLOCATED")                       # allocated
    new_order("DM-CUS-1", [("FG-DETAN-60", 20)], True)                                                             # draft
    print("Dispatch: 6 sales orders (paid, invoiced-unpaid, paid with a return and credit note, packed, allocated, draft)")

    # ---- marketplace import, CRM, maintenance ----
    acct = ok(u["disp"].post("/api/ecommerce/accounts", json={"platform": "NYKAA", "name": "Nykaa", "seller_id": "MT-NYK-01", "customer_id": cus["DM-CUS-4"], "warehouse_id": W}))
    ok(u["disp"].put(f"/api/ecommerce/accounts/{acct['id']}/mappings", json={"platform_sku": "MT-OUD-BW-200", "item_id": item["FG-BW-OUD-200"]}))
    today = str(datetime.now().date())
    imp = ok(u["disp"].post(f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [
        {"order_id": "NYK-DEMO-1", "order_date": today, "customer_name": "Online customer, Bengaluru", "shipping_address": "Indiranagar, Bengaluru", "items": [{"sku": "MT-OUD-BW-200", "qty": "3", "unit_price": money(price["FG-BW-OUD-200"])}]},
        {"order_id": "NYK-DEMO-2", "order_date": today, "customer_name": "Online customer, Delhi", "shipping_address": "Saket, Delhi", "items": [{"sku": "MT-UNMAPPED-X", "qty": "1", "unit_price": "500"}]}]}))
    for name, company, src in [("Priya Nair", "Studio Bloom Salon, Kochi", "Salon range enquiry"), ("Arjun Mehta", "Urban Spa Group", "Trade show"), ("Sana Qureshi", "Velvet Wellness, Hyderabad", "Website 'Partner With Us'")]:
        ok(u["sales"].post("/api/crm/leads", json={"name": name, "company": company, "source": src, "notes": "Demo lead"}))
    for aid, name, cat, loc in [("EQ-MIX-500", "Planetary Mixer 500 L", "Mixing", "Plant 1"), ("EQ-FILL-02", "Semi-automatic Filling Line", "Filling", "Plant 1")]:
        ok(u["admin"].post("/api/maintenance/equipment", json={"asset_id": aid, "name": name, "category": cat, "location": loc, "calibration_required": True, "calibration_due": str(date.today() + timedelta(days=45))}))
    print(f"Marketplace: Nykaa account, SKU mapping, {imp.get('created', '?')} order imported, unmapped SKU in the exception queue. CRM: 3 leads. Maintenance: 2 machines")

    # ---- verify ----
    from app.core.db import SessionLocal
    with SessionLocal() as s:
        report = run_checks(s.connection())
    bad = [c for c in report["checks"] if not c["ok"]]
    print(f"Integrity checks: {len(report['checks']) - len(bad)}/{len(report['checks'])} pass")
    if bad:
        raise SystemExit(f"Integrity violations: {bad}")
    summ = ok(u["mgmt"].get("/api/analytics/management-summary"))
    print("Management summary:", {"revenue_ex_tax": summ["sales"]["revenue_ex_tax"], "receivable": summ["sales"]["receivable_outstanding"], "inventory_value": summ["inventory"]["total_value"],
                                  "active_production": summ["production"]["active_orders"], "orders_by_status": {r["status"]: r["n"] for r in summ["orders_by_status"]}})
    print(f"\nSign in as demo.admin@example.com / {PASSWORD} (also demo.buyer, .stores, .qa, .pm, .pm2, .op, .disp, .sales, .acct, .mgmt @example.com).")


if __name__ == "__main__":
    sys.exit(main())
