"""Full-scale demo data (docs/DEMO_RUNBOOK.md, "Seed spec"): thousands of customers, tens of thousands of orders, a year of history.

Masters (customers, suppliers, raw/packaging items) are inserted directly, exactly as the demo `foundation` step does. EVERY business document (stock
receipts, sales and marketplace orders, shipments, invoices, payments, returns, purchase orders, goods receipts, production orders, QC inspections) is
created by calling the real API in-process, so validation, permissions, ledger/journal postings, audit rows and outbox events are real and the
integrity invariants hold by construction. Deterministic (fixed seeds), resumable (`run` tops up to the targets), and refuses any non-demo database.

    python -m scripts.demo.bulk masters
    python -m scripts.demo.bulk stock
    python -m scripts.demo.bulk run --workers 4 --orders 8000 --market 17000 --purchases 1500 --production 4000
    python -m scripts.demo.bulk status
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import subprocess
import sys
import time
import zlib
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from scripts.demo.catalogue import BOMS, CATALOGUE, GST, TRADE
from scripts.demo.core import Ctx, load_refs, money
from scripts.demo.guard import check_demo_target

SEED = 20261112
MONTH_W = {1: 1.0, 2: 0.9, 3: 1.1, 4: 1.0, 5: 0.9, 6: 0.8, 7: 0.8, 8: 1.0, 9: 1.1, 10: 1.5, 11: 1.7, 12: 1.3}       # festive-season peak (Oct to Dec), monsoon dip
FIRST = ["Aarav", "Diya", "Kabir", "Anaya", "Vihaan", "Ira", "Reyansh", "Myra", "Arjun", "Saanvi", "Kiara", "Rohan", "Meera", "Aditya", "Tara", "Neel", "Zoya", "Dev", "Isha", "Veer"]
LAST = ["Sharma", "Iyer", "Khan", "Patel", "Singh", "Nair", "Das", "Reddy", "Mehta", "Joshi", "Kulkarni", "Gupta", "Menon", "Bose", "Shah", "Rao", "Chopra", "Pillai", "Verma", "Desai"]
KINDS = [("Beauty Studio", "B2B"), ("Salon & Spa", "B2B"), ("Wellness Retail", "B2B"), ("Cosmetics Traders", "DISTRIBUTOR"), ("Distribution Co", "DISTRIBUTOR"), ("Naturals", "B2B"), ("Skin Clinic", "B2B"), ("Gifting House", "BRAND")]
CITIES = [("Mumbai", "MH"), ("Pune", "MH"), ("Delhi", "DL"), ("Gurugram", "HR"), ("Bengaluru", "KA"), ("Mysuru", "KA"), ("Chennai", "TN"), ("Coimbatore", "TN"), ("Hyderabad", "TS"), ("Kochi", "KL"),
          ("Ahmedabad", "GJ"), ("Surat", "GJ"), ("Jaipur", "RJ"), ("Lucknow", "UP"), ("Kolkata", "WB"), ("Bhopal", "MP"), ("Chandigarh", "CH"), ("Indore", "MP"), ("Nagpur", "MH"), ("Patna", "BR")]
BRANDS = ["Lotus", "Kesar", "Nilgiri", "Saffron", "Veda", "Rosewood", "Amaltas", "Kaveri", "Mogra", "Tulsi", "Jasmine", "Indigo", "Himalaya", "Banyan", "Chandan", "Ritu", "Sundari", "Neem", "Zara", "Bhavya"]
RM_NAMES = ["Glycerin", "Niacinamide", "Panthenol", "Hyaluronic acid", "Cetyl alcohol", "Shea butter", "Cocoa butter", "Jojoba oil", "Argan oil", "Rose water", "Vitamin E", "Xanthan gum",
            "Coconut surfactant", "Betaine", "Allantoin", "Zinc oxide", "Kojic acid", "Bakuchiol", "Squalane", "Tea tree oil", "Lavender oil", "Sandalwood oil", "Turmeric extract", "Neem extract"]
PM_NAMES = ["Pump dispenser", "Flip-top cap", "Airless bottle", "Tube", "Carton", "Shipper box", "Label roll", "Sachet film", "Tamper seal", "Jar lid", "Dropper", "Spray mist cap"]
RAW_PRICE = {"RM-ALOE": "85", "RM-SURF": "140", "RM-KAOLIN": "60", "RM-OLIVE": "420", "RM-SHEA": "510", "RM-OUD": "6200", "PM-BOTTLE-200": "14", "PM-JAR-150": "22", "PM-JAR-60": "18"}
FG_CODES = [c[0] for c in CATALOGUE]
KIND_PLAN = [("DELIVERED_PAID", 50), ("DELIVERED_PARTIAL", 10), ("DELIVERED_UNPAID", 14), ("IN_TRANSIT", 4), ("PACKED", 3), ("ALLOCATED", 3), ("CONFIRMED", 4), ("DRAFT", 3), ("CANCELLED", 4), ("RETURNED", 5)]
MKT_PLATFORMS = ["AMAZON", "FLIPKART", "MYNTRA", "NYKAA"]
log = logging.getLogger("bulk")


def guard() -> str:
    s = get_settings()
    return check_demo_target(s.environment, s.database_url)


def pick_age(rng: random.Random, lo: int, hi: int) -> int:
    """A day offset in [lo, hi] weighted by season and a gentle growth trend (more recent = busier)."""
    today = clock.business_today()
    days = list(range(lo, hi + 1))
    w = [MONTH_W[(today - timedelta(days=d)).month] * (1 + 0.6 * (1 - d / 365)) for d in days]
    return rng.choices(days, w)[0]


def months_hist(rng: random.Random) -> int:
    return pick_age(rng, 3, 364)


# ------------------------------------------------------------------------------------------------ masters (direct insert, like the foundation step)
def masters() -> None:
    from app.masters.models import Customer, Item, Supplier, UnitOfMeasure
    rng = random.Random(SEED)
    with Session(engine) as s:
        have_c = s.scalar(text("SELECT count(*) FROM customers WHERE code LIKE 'BK-CUS-%'")) or 0
        for i in range(have_c + 1, 2501):
            city, st = CITIES[i % len(CITIES)]
            kind, ctype = KINDS[i % len(KINDS)]
            s.add(Customer(code=f"BK-CUS-{i:05d}", name=f"{BRANDS[i % len(BRANDS)]} {kind}, {city} #{i}", customer_type=ctype, state=st, billing_address=f"{city}, {st}", shipping_address=f"Market Road, {city}",
                           credit_days=rng.choice([15, 30, 30, 45]), contact_name=f"{rng.choice(FIRST)} {rng.choice(LAST)}", email=f"bk-cus-{i}@example.com"))
        have_s = s.scalar(text("SELECT count(*) FROM suppliers")) or 0
        for i in range(have_s + 1, 61):
            city, st = CITIES[(i * 3) % len(CITIES)]
            s.add(Supplier(code=f"BK-SUP-{i:03d}", name=f"{rng.choice(BRANDS)} {rng.choice(['Chemicals', 'Packaging', 'Naturals', 'Industries', 'Extracts'])} {i}", category=rng.choice(["RM", "PM", "RM_PM"]),
                           city=city, state=st, approval_status="APPROVED", payment_terms_days=rng.choice([15, 30, 45])))
        uom = {u.code: u.id for u in s.scalars(select(UnitOfMeasure))}
        have_i = s.scalar(text("SELECT count(*) FROM items")) or 0
        n = 0
        for i in range(1, 301):
            if have_i + n >= 330:
                break
            code = f"BK-RM-{i:03d}" if i <= 200 else f"BK-PM-{i - 200:03d}"
            if s.scalar(text("SELECT 1 FROM items WHERE item_code = :c"), {"c": code}):
                continue
            rm = i <= 200
            name = f"{RM_NAMES[i % len(RM_NAMES)]} grade {i}" if rm else f"{PM_NAMES[i % len(PM_NAMES)]} {i - 200}"
            s.add(Item(item_code=code, name=name, kind="RM" if rm else "PM", uom_id=uom["KG" if rm else "PCS"], shelf_life_days=730, gst_rate=18, is_batch_tracked=True,
                       reorder_level=Decimal(rng.choice([50, 100, 200])), description="Demo bulk master"))
            n += 1
        s.commit()
        s.execute(text("UPDATE items SET tax_class_id = (SELECT id FROM tax_classes WHERE code = 'GST18-COSM') WHERE gst_rate = 18 AND tax_class_id IS NULL"))
        s.commit()
    print("masters ok")


# ------------------------------------------------------------------------------------------------ opening stock (through the real inventory API)
def stock() -> None:
    c = Ctx()
    load_refs(c)
    c.step_key = f"bulk-stock-{int(time.time())}"
    adm = c.u("admin")
    ids = {r[0]: r[1] for r in SessionLocal().execute(text("SELECT item_code, id FROM items")).all()}
    W = c.refs["foundation"]["wh_rm"]
    cost = {code: Decimal(p) / GST * TRADE * Decimal("0.42") for code, _, p, _ in CATALOGUE}
    docs = []
    for tag, qty, expiry in (("A", "60000", 330), ("B", "40000", 600), ("C", "20000", 450)):
        docs.append(("FG", tag, [{"item_id": ids[code], "qty": qty, "unit_cost": money(cost[code]), "supplier_lot_no": f"BKO-{code[-8:]}-{tag}", "mfg_date": c.d(-120), "expiry_date": c.d(expiry)} for code in FG_CODES]))
    rawlines = []
    for code, price in RAW_PRICE.items():
        q = "400000" if code.startswith("PM-") else "8000" if code == "RM-OUD" else "90000"
        rawlines.append({"item_id": ids[code], "qty": q, "unit_cost": price, "supplier_lot_no": f"BKO-{code}", "mfg_date": c.d(-60), "expiry_date": c.d(700)})
    docs.append(("RAW", "A", rawlines))
    bulk_items = [r for r in ids if r.startswith("BK-")]
    for k in range(0, len(bulk_items), 100):
        docs.append(("BULK", str(k), [{"item_id": ids[code], "qty": "3000", "unit_cost": str(round(random.Random(code).uniform(40, 900), 2)), "supplier_lot_no": f"BKO-{code}", "mfg_date": c.d(-90), "expiry_date": c.d(random.Random(code).choice([60, 150, 300, 500, 700]))}
                                          for code in bulk_items[k:k + 100]]))
    for kind, tag, lines in docs:
        r = c.ok(adm.post("/api/inventory/opening-stock", headers=c.k(f"os{kind}{tag}"), json={"warehouse_id": W, "qc_status": "RELEASED", "note": f"Demo bulk opening stock ({kind} {tag})", "lines": lines}))
        print("opening stock", kind, tag, r.get("number"))


# ------------------------------------------------------------------------------------------------ workers
def _ctx(kind: str, wid: int) -> tuple[Ctx, random.Random]:
    logging.disable(logging.ERROR)
    c = Ctx()
    load_refs(c)
    c.step_key = f"bulk-{kind}-{wid}-{int(time.time())}"
    return c, random.Random(SEED + zlib.crc32(kind.encode()) % 1000 * 31 + wid)


def _maps():
    with SessionLocal() as db:
        items = {r[0]: r[1] for r in db.execute(text("SELECT item_code, id FROM items"))}
        cust = [r[0] for r in db.execute(text("SELECT id FROM customers WHERE code LIKE 'BK-CUS-%' ORDER BY id"))]
        sup = {r[0]: r[1] for r in db.execute(text("SELECT code, id FROM suppliers"))}
    return items, cust, sup


def work_orders(count: int, wid: int, workers: int) -> None:
    from scripts.demo.steps import _ago, _hook
    c, rng = _ctx("orders", wid)
    items, cust, _ = _maps()
    disp, acct = c.u("disp"), c.u("acct")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    mine = [code for i, code in enumerate(FG_CODES) if i % workers == wid % workers] or FG_CODES      # each worker sells its own products: far fewer stock-row collisions
    kinds, kw = zip(*KIND_PLAN)
    done = failed = 0
    t0 = time.time()
    for n in range(count):
        kind = rng.choices(kinds, kw)[0]
        age = {"IN_TRANSIT": rng.randint(2, 6), "PACKED": rng.randint(1, 3), "ALLOCATED": rng.randint(1, 4), "CONFIRMED": rng.randint(1, 6), "DRAFT": rng.randint(0, 5), "CANCELLED": rng.randint(5, 120)}.get(kind) or months_hist(rng)
        try:
            with c.days_ago(age):
                channel = rng.choice(["B2B", "B2B", "DISTRIBUTOR", "BRAND", "MANUAL"])
                trade = channel in ("B2B", "DISTRIBUTOR")
                lo, hi = {"B2B": (6, 40), "DISTRIBUTOR": (20, 90), "BRAND": (4, 24), "MANUAL": (2, 12)}[channel]
                lines = [{"item_id": items[code], "qty": str(rng.randint(lo, hi)), "unit_price": money(price[code] * (TRADE if trade else 1))} for code in rng.sample(mine, min(len(mine), rng.choice([1, 1, 2, 3])))]
                cu = rng.choice(cust)
                o = c.ok(disp.post("/api/dispatch/orders", json={"customer_id": cu, "warehouse_id": W, "channel": channel, "lines": lines}))
                if kind == "DRAFT":
                    done += 1
                    continue
                v = o["version"]
                c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": v}))
                if kind == "CONFIRMED":
                    done += 1
                    continue
                if kind == "CANCELLED":
                    c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/cancel", headers=c.k("cx"), json={"reason": rng.choice(["Customer cancelled the order", "Duplicate order placed by mistake", "Requested date cannot be met"])}))
                    done += 1
                    continue
                c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/allocate", headers=c.k("al"), json={"expected_version": v + 1}))
                if kind == "ALLOCATED":
                    done += 1
                    continue
                pk = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": v + 2, "cartons": rng.randint(1, 5), "weight_kg": money(rng.uniform(2, 30))}))
                if kind == "PACKED":
                    done += 1
                    continue
                d = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=c.k("dp"), json={"expected_version": pk["version"], "courier": "fake"}))
                c.ok(disp.post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
                awb = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
                _hook(c, awb, f"{awb}-pk", "PICKED_UP", _ago(max(age - 0.5, 0.2)))
                _hook(c, awb, f"{awb}-it", "IN_TRANSIT", _ago(max(age - 1, 0.1)))
                if kind != "IN_TRANSIT":
                    _hook(c, awb, f"{awb}-dl", "DELIVERED", _ago(max(age - rng.uniform(2, 4), 0.05)))
                    total = Decimal(str(d["invoice"]["total"]))
                    delay = min(rng.randint(3, 50), max(age - 1, 0))
                    with c.days_ago(-delay):                                  # customers pay some days after the invoice
                        if kind in ("DELIVERED_PAID", "DELIVERED_PARTIAL", "RETURNED"):
                            amount = total if kind != "DELIVERED_PARTIAL" else (total * Decimal(str(rng.choice([0.3, 0.5, 0.6])))).quantize(Decimal("0.01"))
                            if kind == "RETURNED":
                                sh = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))
                                ln = sh["lines"][0]
                                r = c.ok(disp.post(f"/api/dispatch/returns?order_id={o['id']}", headers=c.k("rt"), json={"reason": rng.choice(["damaged on arrival", "wrong variant delivered", "leaking cap"]),
                                                                                                                          "lines": [{"shipment_line_id": ln["id"], "qty": str(max(1, int(Decimal(str(ln["qty"])) * Decimal("0.1"))))}]}))
                                c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/receive", headers=c.k("rc")))
                                c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/decide", headers=c.k("dc"), json={"decision": rng.choice(["RESTOCK", "RESTOCK", "REJECT"])}))
                                inv = c.ok(disp.get(f"/api/dispatch/invoices/{d['invoice']['id']}"))
                                amount = Decimal(str(inv["total"])) - Decimal(str(inv.get("credited_amount") or 0)) - Decimal(str(inv.get("amount_paid") or 0))
                            if amount > 0:
                                c.ok(acct.post("/api/accounts/payments", headers=c.k("pay"), json={"direction": "RECEIVED", "party_id": cu, "amount": money(amount), "payment_date": c.d(0), "method": rng.choice(["BANK_TRANSFER", "UPI", "CHEQUE"]),
                                                                                                  "bank_account_code": "1100", "allocations": [{"invoice_id": d["invoice"]["id"], "amount": money(amount)}]}))
            done += 1
        except SystemExit as e:                                               # Ctx.ok raises SystemExit on an unexpected HTTP status; one bad order must not end the run
            failed += 1
            print(f"[orders {wid}] skipped: {str(e)[:160]}", flush=True)
        if (n + 1) % 100 == 0:
            print(f"[orders {wid}] {n + 1}/{count} ok={done} failed={failed} {done / (time.time() - t0):.1f}/s", flush=True)
    print(f"[orders {wid}] finished ok={done} failed={failed}", flush=True)


def work_market(count: int, wid: int, workers: int) -> None:
    c, rng = _ctx("market", wid)
    items, _, _ = _maps()
    ops, mgr = c.u("sales"), c.u("disp")
    accounts = c.refs["marketplaces"]["accounts"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    mine = [code for i, code in enumerate(FG_CODES) if i % workers == wid % workers] or FG_CODES
    for plat in MKT_PLATFORMS:                                               # SKU mappings exist from the base demo; re-asserting them is idempotent
        for code in FG_CODES:
            mgr.put(f"/api/ecommerce/accounts/{accounts[plat]}/mappings", json={"platform_sku": f"MT-{plat[:3]}-{code[3:]}", "item_id": items[code]})
    ok = batch = 0
    t0 = time.time()
    pending: dict[str, list] = {p: [] for p in MKT_PLATFORMS}
    for n in range(count):
        plat = rng.choice(MKT_PLATFORMS)
        age = months_hist(rng)
        code = rng.choice(mine)
        qty = rng.choice([1, 1, 1, 2, 2, 3, 4])
        oid = f"BK-{plat[:3]}-{wid}-{n:06d}"
        pending[plat].append({"order_id": oid, "order_date": str(clock.business_today() - timedelta(days=age)), "customer_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}, {rng.choice(CITIES)[0]}",
                              "shipping_address": f"{rng.randint(1, 240)}, {rng.choice(CITIES)[0]} main road", "items": [{"sku": f"MT-{plat[:3]}-{code[3:]}", "qty": str(qty), "unit_price": money(price[code])}]})
        if len(pending[plat]) >= 25 or n == count - 1:
            for p, orders in pending.items():
                if orders:
                    with c.days_ago(0):
                        r = c.ok(ops.post(f"/api/ecommerce/accounts/{accounts[p]}/orders", json={"orders": orders}))
                    ok += r["created"] + r["duplicate"]
                    pending[p] = []
            batch += 1
        if (n + 1) % 500 == 0:
            print(f"[market {wid}] {n + 1}/{count} {ok / (time.time() - t0):.1f}/s", flush=True)
    print(f"[market {wid}] finished imported={ok}", flush=True)


def work_supply(count: int, wid: int, workers: int) -> None:
    """Purchase orders through approval, goods receipt, incoming QC, supplier invoice and payment (~60% paid)."""
    from scripts.demo.steps import inspect_batch
    c, rng = _ctx("supply", wid)
    items, _, sup = _maps()
    buyer, adm, acct, stores = c.u("buyer"), c.u("admin"), c.u("acct"), c.u("stores")
    W = c.refs["foundation"]["wh_rm"]
    rm = [k for k in items if k.startswith(("BK-RM", "BK-PM")) or k in RAW_PRICE]
    suppliers = [v for k, v in sup.items() if k.startswith("BK-SUP")] or list(sup.values())
    done = 0
    t0 = time.time()
    for n in range(count):
        age = months_hist(rng)
        try:
            with c.days_ago(age):
                lines = [{"item_id": items[k], "qty": str(rng.choice([50, 100, 200, 400])), "unit_price": money(rng.uniform(20, 900))} for k in rng.sample(rm, rng.choice([2, 4, 6, 8]))]
                po = c.ok(buyer.post("/api/procurement/purchase-orders", json={"supplier_id": rng.choice(suppliers), "expected_date": c.d(7), "notes": "Demo bulk purchase", "items": lines}))
                po = c.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
                po = c.ok(adm.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
                if rng.random() < 0.08:
                    done += 1
                    continue                                                # approved, not yet delivered
                frac = Decimal("1") if rng.random() > 0.15 else Decimal("0.6")
                gr = c.ok(stores.post("/api/procurement/goods-receipts", headers=c.k("gr"), json={
                    "po_id": po["id"], "warehouse_id": W, "delivery_note_no": f"DN-BK-{wid}-{n:06d}", "lines": [
                        {"po_item_id": pi["id"], "qty": str((Decimal(pi["qty_ordered"]) * frac).quantize(Decimal("0.001"))), "supplier_lot_no": f"BKL-{wid}-{n}-{k}", "mfg_date": c.d(-15), "expiry_date": c.d(540)} for k, pi in enumerate(po["items"], 1)]}))
                for ln in gr["lines"]:
                    inspect_batch(c, ln["batch_id"], "fail" if rng.random() < 0.04 else "pass", reason="Failed identity / assay test at incoming QC")
                if rng.random() < 0.7:
                    po2 = c.ok(buyer.get(f"/api/procurement/purchase-orders/{po['id']}"))
                    inv_lines = [{"po_item_id": i["id"], "qty": i["qty_received"], "unit_price": i["unit_price"], "tax_rate": i["tax_rate"]} for i in po2["items"] if Decimal(str(i["qty_received"])) > 0]
                    inv = c.ok(acct.post("/api/accounts/supplier-invoices", headers=c.k("ssi"), json={"po_id": po["id"], "supplier_invoice_no": f"BK/{wid}/{n:06d}", "invoice_date": c.d(0), "lines": inv_lines}))
                    if rng.random() < 0.6:
                        with c.days_ago(-min(rng.randint(5, 40), max(age - 1, 0))):
                            amt = Decimal(str(inv["total"]))
                            c.ok(acct.post("/api/accounts/payments", headers=c.k("sp"), json={"direction": "PAID", "party_id": po["supplier_id"], "amount": money(amt), "payment_date": c.d(0), "method": rng.choice(["BANK_TRANSFER", "UPI", "CHEQUE"]),
                                                                                              "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": money(amt)}]}))
            done += 1
        except SystemExit as e:
            print(f"[supply {wid}] skipped: {str(e)[:200]}", flush=True)
        if (n + 1) % 25 == 0:
            print(f"[supply {wid}] {n + 1}/{count} ok={done} {done / (time.time() - t0):.2f}/s", flush=True)
    print(f"[supply {wid}] finished ok={done}", flush=True)


def work_production(count: int, wid: int, workers: int) -> None:
    from scripts.demo.steps import inspect_batch, run_order
    c, rng = _ctx("production", wid)
    bom = c.refs["production_finished_goods"]["boms"]
    codes = [k for i, k in enumerate(BOMS) if i % workers == wid % workers] or list(BOMS)
    done = 0
    t0 = time.time()
    for n in range(count):
        code = codes[n % len(codes)]
        size = BOMS[code][0]
        age = months_hist(rng)
        try:
            with c.days_ago(age):
                low = rng.random() < 0.12
                actual = str(size - (10 if low else max(2, size // 40)))
                w = [{"category": "SPILLAGE", "qty": "4", "reason": "Spillage during filling line changeover", "department": "Production"}] if low else []
                res = run_order(c, bom[code], str(size), actual=actual, wastage=w)
                r = rng.random()
                if r < 0.02:
                    pass                                                    # left in quarantine: waiting for QC (a live "needs attention" item)
                elif r < 0.07:
                    inspect_batch(c, res["batch"], "fail", reason="Viscosity out of specification at finished-goods QC")
                else:
                    inspect_batch(c, res["batch"], "pass")
            done += 1
        except SystemExit as e:
            print(f"[production {wid}] skipped: {str(e)[:200]}", flush=True)
        if (n + 1) % 25 == 0:
            print(f"[production {wid}] {n + 1}/{count} ok={done} {done / (time.time() - t0):.2f}/s", flush=True)
    print(f"[production {wid}] finished ok={done}", flush=True)


WORKERS = {"orders": work_orders, "market": work_market, "supply": work_supply, "production": work_production}


# ------------------------------------------------------------------------------------------------ orchestration
def counts() -> dict:
    q = {"customers": "SELECT count(*) FROM customers", "suppliers": "SELECT count(*) FROM suppliers", "items": "SELECT count(*) FROM items",
         "sales_orders": "SELECT count(*) FROM sales_orders", "marketplace_orders": "SELECT count(*) FROM external_orders", "purchase_lines": "SELECT count(*) FROM purchase_order_items",
         "stock_movements": "SELECT count(*) FROM stock_ledger", "production_batches": "SELECT count(*) FROM production_orders", "qc_readings": "SELECT (SELECT count(*) FROM inspection_results) + (SELECT count(*) FROM process_checks)",
         "ledger_entries": "SELECT count(*) FROM journal_entries", "invoices": "SELECT count(*) FROM sales_invoices", "warehouses": "SELECT count(*) FROM warehouses", "bins": "SELECT count(*) FROM storage_locations"}
    with SessionLocal() as db:
        return {k: db.execute(text(v)).scalar() for k, v in q.items()}


def run(a) -> None:
    guard()
    have = counts()
    plan: list[tuple[str, int]] = []
    for kind, target, now in (("orders", a.orders, have["sales_orders"]), ("market", a.market, have["marketplace_orders"]), ("supply", a.purchases, have["purchase_lines"] // 5), ("production", a.production, have["production_batches"])):
        need = max(0, target - now) if kind != "orders" else max(0, target)
        if need:
            plan.append((kind, need))
    procs = []
    for kind, need in plan:
        w = a.workers if kind in ("orders", "production") else max(1, a.workers // 2)
        per = -(-need // w)
        for i in range(w):
            procs.append(subprocess.Popen([sys.executable, "-m", "scripts.demo.bulk", "work", kind, "--count", str(per), "--id", str(i), "--workers", str(w)], env=dict(os.environ)))
    for p in procs:
        p.wait()
    print(counts())


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("masters")
    sub.add_parser("stock")
    sub.add_parser("status")
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--orders", type=int, default=8000)
    r.add_argument("--market", type=int, default=17000)
    r.add_argument("--purchases", type=int, default=1500)
    r.add_argument("--production", type=int, default=4000)
    w = sub.add_parser("work")
    w.add_argument("kind", choices=list(WORKERS))
    w.add_argument("--count", type=int, required=True)
    w.add_argument("--id", type=int, default=0)
    w.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()
    guard()
    if a.cmd == "masters":
        masters()
    elif a.cmd == "stock":
        stock()
    elif a.cmd == "status":
        print(counts())
    elif a.cmd == "run":
        run(a)
    else:
        WORKERS[a.kind](a.count, a.id, a.workers)


if __name__ == "__main__":
    main()
