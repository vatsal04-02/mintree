"""Demo at scale: ~120 sales orders over five months, ~110 marketplace orders, ~120 CRM records and extra procurement/production/quality history, all driven
through the real API (permissions, validation, ledger, journals, audit, outbox) exactly like the hand-written scenarios in steps.py.

Deterministic: one seeded RNG, fixed names. Idempotent: every step is recorded in demo_seed_steps and never repeats. `MINTREE_DEMO_SCALE` (default 1.0)
shrinks the volumes for quick tests; the status mix is kept."""
from __future__ import annotations

import os
import random
from datetime import timedelta
from decimal import Decimal

from scripts.demo.catalogue import CATALOGUE, GST, TRADE
from scripts.demo.core import Ctx, money, step
from scripts.demo.steps import _ago, _hook, cus_ids, item_ids

SCALE = float(os.environ.get("MINTREE_DEMO_SCALE", "1"))
n_ = lambda n: max(1, round(n * SCALE))
SEED = 20261107

PREFIX = ["Lotus", "Aaradhya", "Kesar", "Nilgiri", "Saffron", "Veda", "Rosewood", "Amaltas", "Kaveri", "Mogra", "Tulsi", "Jasmine", "Indigo", "Himalaya", "Banyan", "Chandan", "Ritu", "Sundari",
          "Neem", "Zara", "Aashna", "Bhavya", "Devika", "Ishani", "Kalpana", "Meghna", "Nandini", "Pooja", "Rangoli", "Shreya"]
KINDS = [("Beauty Studio", "B2B"), ("Salon & Spa", "B2B"), ("Wellness Retail", "B2B"), ("Cosmetics Traders", "DISTRIBUTOR"), ("Distribution Co", "DISTRIBUTOR"), ("Naturals Pvt Ltd", "B2B"),
         ("Skin Clinic", "B2B"), ("Gifting House", "BRAND")]
CITIES = [("Mumbai", "MH"), ("Pune", "MH"), ("Delhi", "DL"), ("Gurugram", "HR"), ("Bengaluru", "KA"), ("Mysuru", "KA"), ("Chennai", "TN"), ("Coimbatore", "TN"), ("Hyderabad", "TS"),
          ("Kochi", "KL"), ("Ahmedabad", "GJ"), ("Surat", "GJ"), ("Jaipur", "RJ"), ("Lucknow", "UP"), ("Kolkata", "WB"), ("Bhopal", "MP"), ("Chandigarh", "CH"), ("Indore", "MP")]


def _fg(c: Ctx) -> list[str]:
    return [code for code, *_ in CATALOGUE]


@step("scale_masters")
def scale_masters(c: Ctx) -> dict:
    """~45 more customers (new and repeat buyers of different kinds) and loaded finished-goods stock in two lots per product (FEFO), one lot near expiry and QC-held lots."""
    rng = random.Random(SEED)
    adm = c.u("admin")
    items = item_ids(c)
    out: dict = {"customers": [], "scale": SCALE}
    combos = [(p, k, ct) for p in PREFIX for k, ct in KINDS]
    rng.shuffle(combos)
    for i, (p, kind, ctype) in enumerate(combos[:n_(45)], 1):
        city, st = CITIES[i % len(CITIES)]
        code = f"SC-CUS-{i:03d}"
        r = c.ok(adm.post("/api/masters/customers", json={"code": code, "name": f"{p} {kind}, {city}", "customer_type": ctype, "state": st, "billing_address": f"{city}, {st}", "shipping_address": f"Market Road, {city}",
                                                         "credit_days": 30 if ctype != "BRAND" else 15, "contact_name": f"{p} Contact", "email": f"{code.lower()}@example.com"}))
        out["customers"].append(r["id"])
    W = c.refs["foundation"]["wh_rm"]
    cost = {code: (Decimal(p) / GST * TRADE * Decimal("0.42")) for code, _, p, _ in CATALOGUE}
    lots = []
    for tag, qty_each, expiry, status in (("A", "2600", 330, "RELEASED"), ("B", "2200", 600, "RELEASED"), ("C", "1200", 450, "RELEASED")):
        lots.append(c.ok(adm.post("/api/inventory/opening-stock", headers=c.k("os" + tag), json={"warehouse_id": W, "qc_status": status, "note": f"Demo opening FG stock, lot {tag}", "lines": [
            {"item_id": items[code], "qty": qty_each, "unit_cost": money(cost[code]), "supplier_lot_no": f"OPN-{code[-6:]}-{tag}", "mfg_date": c.d(-120), "expiry_date": c.d(expiry)}
            for code in _fg(c)]}))["number"])
    # one lot close to expiry (reported by the expiring-batches report) and QC-held lots for two products (visible as blocked stock)
    lots.append(c.ok(adm.post("/api/inventory/opening-stock", headers=c.k("osx"), json={"warehouse_id": W, "qc_status": "RELEASED", "note": "Demo: lot close to expiry", "lines": [
        {"item_id": items["FG-HC-5X30"], "qty": "140", "unit_cost": money(cost["FG-HC-5X30"]), "supplier_lot_no": "OPN-HC-OLD", "mfg_date": c.d(-700), "expiry_date": c.d(24)}]}))["number"])
    lots.append(c.ok(adm.post("/api/inventory/opening-stock", headers=c.k("osq"), json={"warehouse_id": W, "qc_status": "QUARANTINE", "note": "Demo: lot waiting for QC", "lines": [
        {"item_id": items[code], "qty": "400", "unit_cost": money(cost[code]), "supplier_lot_no": f"OPN-{code[-6:]}-Q", "mfg_date": c.d(-5), "expiry_date": c.d(700)} for code in ("FG-BL-OUD-200", "FG-FALLSTOP-200")]}))["number"])
    out["opening_documents"] = lots
    return out


# ------------------------------------------------------------------ sales orders through the whole dispatch chain
PLAN = [("DELIVERED_PAID", 36), ("DELIVERED_PARTIAL", 12), ("DELIVERED_UNPAID", 14), ("IN_TRANSIT", 12), ("PACKED", 6), ("ALLOCATED", 6), ("CONFIRMED", 7), ("DRAFT", 7),
        ("CANCELLED", 6), ("PARTIAL_SHIP", 6), ("RETURNED", 6)]


@step("scale_orders")
def scale_orders(c: Ctx) -> dict:
    rng = random.Random(SEED + 1)
    items, cus = item_ids(c), cus_ids(c)
    disp, acct = c.u("disp"), c.u("acct")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    customers = list(c.refs["scale_masters"]["customers"])
    sc = [i for code, i in sorted(cus.items()) if code.startswith("SC-CUS")]          # no credit limit on the generated customers
    pool = sc + [cus[k] for k in ("DM-CUS-1", "DM-CUS-2", "DM-CUS-5", "DM-CUS-6") if k in cus]   # the hand-made ones have credit limits and sometimes trip them
    heavy = sc[:8]                                                      # repeat customers: a few buy much more often
    fg = [code for code, *_ in CATALOGUE]
    counts: dict[str, int] = {}
    out: dict = {"orders": {}, "invoices": [], "shipments": [], "returns": [], "duplicate_webhooks": 0, "credit_holds": 0}
    seq = [0]

    def new_order(age: int) -> tuple[dict, dict]:
        cust = rng.choice(heavy) if rng.random() < 0.45 else rng.choice(pool)
        channel = rng.choice(["B2B", "B2B", "DISTRIBUTOR", "BRAND", "MANUAL"])
        trade = channel in ("B2B", "DISTRIBUTOR")
        codes = rng.sample(fg, rng.choice([1, 1, 2, 2, 3]))
        qty_range = {"B2B": (20, 120), "DISTRIBUTOR": (60, 260), "BRAND": (10, 60), "MANUAL": (5, 30)}[channel]
        lines = [{"item_id": items[code], "qty": str(rng.randint(*qty_range)), "unit_price": money(price[code] * (TRADE if trade else 1))} for code in codes]
        o = c.ok(disp.post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": W, "channel": channel, "lines": lines}))
        return o, {"customer_id": cust, "channel": channel}

    class CreditHold(Exception):
        pass

    def advance(o: dict, to: str) -> dict:
        v = o["version"]
        r = disp.post(f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": v})
        if r.status_code == 409 and "credit_limit_exceeded" in r.text:      # the system refuses: the order stays a draft (shown in the demo as a credit hold)
            raise CreditHold()
        c.ok(r)
        if to == "CONFIRMED":
            return {"version": v + 1}
        a = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/allocate", headers=c.k("al"), json={"expected_version": v + 1}))
        if to == "ALLOCATED":
            return a
        return c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/pack", json={"expected_version": v + 2, "cartons": rng.randint(1, 6), "weight_kg": money(rng.uniform(2, 40))}))

    def ship(o: dict, version: int, lines=None) -> dict:
        body: dict = {"expected_version": version, "courier": "fake"}
        if lines:
            body["lines"] = lines
        d = c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=c.k("dp"), json=body))
        c.ok(disp.post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
        d["awb"] = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
        out["shipments"].append(d["shipment_id"])
        out["invoices"].append(d["invoice"]["id"])
        return d

    def track(awb: str, age: int, delivered: bool, tag: str) -> None:
        _hook(c, awb, f"{tag}-pk", "PICKED_UP", _ago(max(age - 0.5, 0.2)))
        _hook(c, awb, f"{tag}-it", "IN_TRANSIT", _ago(max(age - 1, 0.1)))
        if delivered:
            _hook(c, awb, f"{tag}-dl", "DELIVERED", _ago(max(age - rng.uniform(2, 4), 0.05)))
            if seq[0] % 7 == 0:                                         # the courier re-sends an event: it must not duplicate anything
                _hook(c, awb, f"{tag}-dl", "DELIVERED", _ago(max(age - 3, 0.05)))
                out["duplicate_webhooks"] += 1

    def pay(inv: dict, cust_id: int, amount: Decimal) -> None:
        c.ok(acct.post("/api/accounts/payments", headers=c.k("pay"), json={"direction": "RECEIVED", "party_id": cust_id, "amount": money(amount), "payment_date": c.d(0), "method": rng.choice(["BANK_TRANSFER", "UPI", "CHEQUE"]),
                                                                           "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": money(amount)}]}))

    def one(kind: str, age: int) -> None:
        with c.days_ago(age):
            o, meta = new_order(age)
            tag = f"sc{seq[0]}"
            if kind == "DRAFT":
                return
            if kind == "CONFIRMED":
                advance(o, "CONFIRMED"); return
            if kind == "ALLOCATED":
                advance(o, "ALLOCATED"); return
            if kind == "CANCELLED":
                st = rng.choice(["DRAFT", "CONFIRMED", "ALLOCATED"])
                if st != "DRAFT":
                    advance(o, st)
                c.ok(disp.post(f"/api/dispatch/orders/{o['id']}/cancel", headers=c.k("cx"), json={"reason": rng.choice(["Customer cancelled the order", "Credit hold at the customer", "Duplicate order placed by mistake", "Requested date cannot be met"])}))
                return
            packed = advance(o, "PACKED")
            if kind == "PACKED":
                return
            if kind == "PARTIAL_SHIP":
                full = c.ok(disp.get(f"/api/dispatch/orders/{o['id']}"))
                first = full["items"][0]
                half = (Decimal(str(first["qty"])) / 2).quantize(Decimal("1"))
                d = ship(o, packed["version"], [{"so_item_id": first["id"], "qty": str(max(half, Decimal(1)))}])
                track(d["awb"], age, delivered=True, tag=tag)
                return
            d = ship(o, packed["version"])
            if kind == "IN_TRANSIT":
                track(d["awb"], age, delivered=False, tag=tag); return
            track(d["awb"], age, delivered=True, tag=tag)
            total = Decimal(str(d["invoice"]["total"]))
            if kind == "DELIVERED_PAID":
                pay(d["invoice"], meta["customer_id"], total)
            elif kind == "DELIVERED_PARTIAL":
                pay(d["invoice"], meta["customer_id"], (total * Decimal(str(rng.choice([0.3, 0.5, 0.6])))).quantize(Decimal("0.01")))
            elif kind == "RETURNED":
                sh = c.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))
                ln = sh["lines"][0]
                rq = max(1, int(Decimal(str(ln["qty"])) * Decimal("0.1")))
                r = c.ok(disp.post(f"/api/dispatch/returns?order_id={o['id']}", headers=c.k("rt"), json={"reason": rng.choice(["damaged on arrival", "wrong variant delivered", "near-expiry stock refused", "leaking cap"]), "lines": [{"shipment_line_id": ln["id"], "qty": str(rq)}]}))
                c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/receive", headers=c.k("rc")))
                c.ok(disp.post(f"/api/dispatch/returns/{r['id']}/decide", headers=c.k("dc"), json={"decision": rng.choice(["RESTOCK", "RESTOCK", "REJECT"])}))
                out["returns"].append(r["id"])
                inv = c.ok(disp.get(f"/api/dispatch/invoices/{d['invoice']['id']}"))
                due = Decimal(str(inv["total"])) - Decimal(str(inv.get("credited_amount") or 0)) - Decimal(str(inv.get("amount_paid") or 0))
                if due > 0:
                    pay(d["invoice"], meta["customer_id"], due)

    for kind, n in PLAN:
        for _ in range(n_(n)):
            seq[0] += 1
            counts[kind] = counts.get(kind, 0) + 1
            age = {"DELIVERED_PAID": rng.randint(25, 150), "DELIVERED_PARTIAL": rng.randint(20, 100), "DELIVERED_UNPAID": rng.randint(35, 120), "RETURNED": rng.randint(20, 90),
                   "IN_TRANSIT": rng.randint(2, 6), "PACKED": rng.randint(1, 3), "ALLOCATED": rng.randint(1, 4), "CONFIRMED": rng.randint(1, 6), "DRAFT": rng.randint(0, 5),
                   "CANCELLED": rng.randint(5, 60), "PARTIAL_SHIP": rng.randint(8, 30)}[kind]
            try:
                one(kind, age)
            except CreditHold:
                out["credit_holds"] += 1
    out["by_kind"] = counts
    return out


# ------------------------------------------------------------------ marketplaces: ~110 imported orders, fulfilment, failed syncs and retries, returns, settlements
MKT_PLATFORMS = ["AMAZON", "FLIPKART", "MYNTRA", "NYKAA"]
CITY_NAMES = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune", "Kolkata", "Jaipur", "Ahmedabad", "Kochi"]


@step("scale_marketplace")
def scale_marketplace(c: Ctx) -> dict:
    rng = random.Random(SEED + 2)
    items = item_ids(c)
    mgr, ops, acct = c.u("disp"), c.u("sales"), c.u("acct")
    W = c.refs["foundation"]["wh_rm"]
    accounts = c.refs["marketplaces"]["accounts"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    fg = [code for code, *_ in CATALOGUE]
    out: dict = {"imported": 0, "exceptions": 0, "retried_ok": 0, "fulfilled": 0, "delivered": 0, "returns": 0, "settlements": []}
    sku_of = {}
    for plat in MKT_PLATFORMS:
        for code in fg:                                                  # every product is listed on every marketplace under that marketplace's own SKU
            sku = f"MT-{plat[:3]}-{code[3:]}"
            c.ok(mgr.put(f"/api/ecommerce/accounts/{accounts[plat]}/mappings", json={"platform_sku": sku, "item_id": items[code]}))
            sku_of[(plat, code)] = sku
    per_platform = n_(28)
    by_plat: dict[str, list[str]] = {p: [] for p in MKT_PLATFORMS}
    ages: dict[str, int] = {}
    for plat in MKT_PLATFORMS:
        for i in range(1, per_platform + 1):
            age = rng.randint(2, 62)
            code = rng.choice(fg)
            qty = rng.choice([1, 1, 2, 2, 3, 4])
            oid = f"SC-{plat[:3]}-{i:04d}"
            body = {"order_id": oid, "order_date": c.d(-age), "customer_name": f"{rng.choice(['Aarav', 'Diya', 'Kabir', 'Anaya', 'Vihaan', 'Ira', 'Reyansh', 'Myra'])} {rng.choice(['Sharma', 'Iyer', 'Khan', 'Patel', 'Singh', 'Nair', 'Das', 'Reddy'])}, {rng.choice(CITY_NAMES)}",
                    "shipping_address": f"{rng.randint(1, 240)}, {rng.choice(CITY_NAMES)} main road", "items": [{"sku": sku_of[(plat, code)], "qty": str(qty), "unit_price": money(price[code])}]}
            with c.days_ago(age):
                r = c.ok(ops.post(f"/api/ecommerce/accounts/{accounts[plat]}/orders", json={"orders": [body]}))
            assert r["created"] + r["duplicate"] == 1, r               # a re-run after a partial failure meets its own earlier rows as duplicates
            out["imported"] += 1
            by_plat[plat].append(oid)
            ages[oid] = age
            if i % 9 == 0:                                               # the platform delivers the same order twice: no duplicate
                again = c.ok(ops.post(f"/api/ecommerce/accounts/{accounts[plat]}/orders", json={"orders": [body]}))
                assert again["duplicate"] == 1 and again["created"] == 0, again
    # failed synchronisation: orders whose SKU is not mapped land in the exception queue; mapping the SKU and retrying creates the sales order (two platforms), the rest stay open
    for plat in MKT_PLATFORMS:
        for j in range(1, n_(3) + 1):
            sku = f"MT-{plat[:3]}-NEWLISTING-{j}"
            ops.post(f"/api/ecommerce/accounts/{accounts[plat]}/orders", json={"orders": [{"order_id": f"SC-{plat[:3]}-X{j:03d}", "order_date": c.d(-rng.randint(1, 6)), "customer_name": "Customer, Mumbai",
                                                                                            "items": [{"sku": sku, "qty": "1", "unit_price": "700.00"}]}]})
            out["exceptions"] += 1
    ext = _all_ext(c, mgr, "EXCEPTION")
    for e in ext:
        plat = next((p for p in MKT_PLATFORMS if f"SC-{p[:3]}-X" in e["external_order_id"]), None)
        if plat in ("AMAZON", "FLIPKART"):
            c.ok(mgr.put(f"/api/ecommerce/accounts/{accounts[plat]}/mappings", json={"platform_sku": f"MT-{plat[:3]}-NEWLISTING-{e['external_order_id'][-1]}", "item_id": items[rng.choice(fg)]}))
            c.ok(ops.post(f"/api/ecommerce/external-orders/{e['id']}/retry"))
            out["retried_ok"] += 1
    # fulfilment of ~60% of the imported orders; the rest stay open at different stages
    allx = [e for e in _all_ext(c, mgr, "SO_CREATED") if e["external_order_id"].startswith("SC-") and "-X" not in e["external_order_id"]]
    delivered_by_plat: dict[str, list[dict]] = {p: [] for p in MKT_PLATFORMS}
    for k, e in enumerate(sorted(allx, key=lambda x: x["external_order_id"])):
        roll = (k * 7) % 10
        if e["sales_order_id"] is None:
            continue
        if roll >= 6:
            continue
        plat = next(p for p in MKT_PLATFORMS if p[:3] in e["external_order_id"])
        age = ages.get(e["external_order_id"], 6)
        with c.days_ago(max(age - 1, 0)):
            so = c.ok(mgr.get(f"/api/dispatch/orders/{e['sales_order_id']}"))
            v = so["version"]
            c.ok(mgr.post(f"/api/dispatch/orders/{so['id']}/confirm", json={"expected_version": v}))
            if roll == 5:
                continue                                                  # confirmed only
            c.ok(mgr.post(f"/api/dispatch/orders/{so['id']}/allocate", headers=c.k("mal"), json={"expected_version": v + 1}))
            if roll == 4:
                continue                                                  # allocated, not packed
            pk = c.ok(mgr.post(f"/api/dispatch/orders/{so['id']}/pack", json={"expected_version": v + 2, "cartons": 1, "weight_kg": "0.8"}))
            d = c.ok(mgr.post(f"/api/dispatch/orders/{so['id']}/dispatch", headers=c.k("mdp"), json={"expected_version": pk["version"], "courier": "fake"}))
            c.ok(mgr.post(f"/api/dispatch/shipments/{d['shipment_id']}/book"))
            awb = c.ok(mgr.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
        out["fulfilled"] += 1
        if roll >= 2 or age < 4:
            if age >= 4:
                _hook(c, awb, f"smk-{k}-pk", "PICKED_UP", _ago(max(age - 1.5, 0.2)))
                _hook(c, awb, f"smk-{k}-dl", "DELIVERED", _ago(max(age - 3, 0.1)))
                out["delivered"] += 1
                delivered_by_plat[plat].append({"ext": e["external_order_id"], "total": Decimal(str(d["invoice"]["total"])), "sku": e["external_order_id"]})
    # returns on a few delivered orders
    for plat in MKT_PLATFORMS:
        for j, dv in enumerate(delivered_by_plat[plat][: n_(3)], 1):
            full = next(e for e in allx if e["external_order_id"] == dv["ext"])
            sku = full["items"][0]["sku"] if full["items"] else None
            if sku:
                c.ok(mgr.post("/api/ecommerce/returns", json={"account_id": accounts[plat], "platform_return_ref": f"SC-{plat[:3]}-RET-{j:03d}", "external_order_id": dv["ext"], "reason": rng.choice(["Customer changed mind", "Product damaged in transit", "Wrong item received"]), "lines": [{"sku": sku, "qty": "1"}]}))
                out["returns"] += 1
    # settlements: two periods per platform; the first is received and posted to the books, the second is received but left for the accountant (reconciliation to do)
    for plat in MKT_PLATFORMS:
        gross_total = sum((d["total"] for d in delivered_by_plat[plat]), Decimal(0))
        if gross_total <= 0:
            continue
        half = (gross_total / 2).quantize(Decimal("0.01"))
        for n_s, (gross, start, end) in enumerate(((half, -60, -31), (gross_total - half, -30, -1)), 1):
            commission = (gross * Decimal("0.14")).quantize(Decimal("0.01"))
            ship_fee, ad_fee = (gross * Decimal("0.04")).quantize(Decimal("0.01")), (gross * Decimal("0.02")).quantize(Decimal("0.01"))
            st = c.ok(mgr.post(f"/api/ecommerce/accounts/{accounts[plat]}/settlements", json={"reference": f"SC-{plat[:3]}-SET-{n_s:02d}", "period_start": c.d(start), "period_end": c.d(end),
                                                                                           "gross_sales": str(gross), "commission": str(commission), "shipping_fees": str(ship_fee), "ad_fees": str(ad_fee)}))
            c.ok(mgr.post(f"/api/ecommerce/settlements/{st['id']}/received", json={"received_amount": st["net_settlement"]}))
            if n_s == 1:
                c.ok(acct.post(f"/api/accounts/settlements/{st['id']}/post", headers=c.k("mset"), json={"bank_account_code": "1100"}))
            out["settlements"].append(st["id"])
    return out


def _all_ext(c: Ctx, client, status: str) -> list[dict]:
    out, page = [], 1
    while True:
        items = c.ok(client.get("/api/ecommerce/external-orders", params={"page_size": 100, "status": status, "page": page}))["items"]
        out += items
        if len(items) < 100:
            return out
        page += 1


# ------------------------------------------------------------------ CRM: ~70 leads and ~50 opportunities in every stage, with activities, follow-ups, quotations and complaints
SALON_NAMES = ["Glam Room", "The Skin Bar", "Aura Studio", "Pure Bliss Spa", "Velvet Touch", "Urban Roots", "Blossom Salon", "Glow Lab", "Serene Spa", "Bella Vita", "Charm Studio", "Luxe Locks"]
FIRST = ["Aditi", "Rohan", "Neha", "Karan", "Simran", "Vikram", "Pallavi", "Arnav", "Tanvi", "Harsh", "Divya", "Manish", "Ritika", "Sahil", "Kavya", "Nitin"]
LAST = ["Shah", "Kulkarni", "Banerjee", "Menon", "Gupta", "Chopra", "Rao", "Joshi", "Verma", "Bose", "Kapoor", "Pillai"]
LOST_REASONS = ["Price sensitive", "Chose an imported brand", "Minimum order too high", "No budget this quarter", "Needs private label, not offered", "Went quiet after sampling"]


@step("scale_crm")
def scale_crm(c: Ctx) -> dict:
    rng = random.Random(SEED + 3)
    items = item_ids(c)
    sales = c.u("sales")
    W = c.refs["foundation"]["wh_rm"]
    price = {code: Decimal(p) / GST for code, _, p, _ in CATALOGUE}
    fg = [code for code, *_ in CATALOGUE]
    adm = c.u("admin")
    src = {s["code"]: s["id"] for s in c.ok(adm.get("/api/crm/lead-sources"))["items"]}
    srcs = list(src.values())
    me_id = c.ok(sales.get("/api/auth/me"))["id"]
    out = {"leads": 0, "opportunities": 0, "customers": 0, "quotations": 0, "orders": 0, "activities": 0, "followups": 0, "complaints": 0, "stage_counts": {}}
    plan = ["NEW"] * 20 + ["CONTACTED"] * 14 + ["QUALIFIED"] * 10 + ["LOST"] * 8 + ["CONVERT"] * 18
    rng.shuffle(plan)                                                   # a smaller scale keeps the stage mix instead of only the first kind
    plan = plan[: n_(70)] if SCALE < 1 else plan
    for i, kind in enumerate(plan, 1):
        age = rng.randint(3, 130)
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        company = f"{rng.choice(SALON_NAMES)} {rng.choice(CITY_NAMES)} #{i}"
        with c.days_ago(age):
            lead = c.ok(sales.post("/api/crm/leads", json={"name": name, "company": company, "email": f"lead{i:03d}@example.com", "phone": f"+9100000{i:05d}", "lead_source_id": rng.choice(srcs), "source": "Demo", "notes": "Demo lead"}))
            out["leads"] += 1
            if kind in ("CONTACTED", "QUALIFIED", "LOST", "CONVERT"):
                c.ok(sales.post("/api/crm/activities", json={"kind": rng.choice(["CALL", "EMAIL", "VISIT"]), "subject": rng.choice(["Intro call", "Sent catalogue", "Store visit", "Shared price list"]), "duration_min": rng.choice([10, 20, 30]), "lead_id": lead["id"]}))
                out["activities"] += 1
                c.ok(sales.post(f"/api/crm/leads/{lead['id']}/status", json={"status": "CONTACTED"}))
            if kind in ("QUALIFIED", "CONVERT", "LOST"):
                c.ok(sales.post("/api/crm/activities", json={"kind": "MEETING", "subject": "Product demo and sampling", "duration_min": 45, "lead_id": lead["id"]}))
                out["activities"] += 1
                c.ok(sales.post(f"/api/crm/leads/{lead['id']}/status", json={"status": "QUALIFIED"}))
            if kind == "LOST":
                reason = rng.choice(LOST_REASONS)
                opp = c.ok(sales.post("/api/crm/opportunities", json={"name": f"{company}: starter range", "lead_id": lead["id"], "expected_value": str(rng.randint(15, 90) * 1000), "expected_close": c.d(rng.randint(5, 25))}))
                c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "LOST", "expected_version": opp["version"], "lost_reason": reason}))
                c.ok(sales.post(f"/api/crm/leads/{lead['id']}/status", json={"status": "LOST", "reason": reason}))
                out["opportunities"] += 1
                out["stage_counts"]["LOST"] = out["stage_counts"].get("LOST", 0) + 1
            if kind == "QUALIFIED":
                opp = c.ok(sales.post("/api/crm/opportunities", json={"name": f"{company}: backbar + retail range", "lead_id": lead["id"], "expected_value": str(rng.randint(20, 150) * 1000), "expected_close": c.d(rng.randint(10, 40))}))
                stage = rng.choice(["PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION"])
                out["opportunities"] += 1
                path = ["QUALIFIED", "PROPOSAL", "NEGOTIATION"]
                for step_to in path[: path.index(stage) + 1] if stage != "PROSPECTING" else []:     # stages move one at a time, as the system enforces
                    opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": step_to, "expected_version": opp["version"]}))
                out["stage_counts"][stage] = out["stage_counts"].get(stage, 0) + 1
            if kind == "CONVERT":
                conv = c.ok(sales.post(f"/api/crm/leads/{lead['id']}/convert", json={"customer_type": rng.choice(["B2B", "B2B", "DISTRIBUTOR"])}))
                out["customers"] += 1
                opp = c.ok(sales.post("/api/crm/opportunities", json={"name": f"{company}: launch order", "lead_id": lead["id"], "expected_value": str(rng.randint(30, 200) * 1000), "expected_close": c.d(rng.randint(3, 20))}))
                out["opportunities"] += 1
                codes = rng.sample(fg, 2)
                q = c.ok(sales.post("/api/sales/quotations", json={"customer_id": conv["customer_id"], "valid_until": c.d(30), "lines": [{"item_id": items[code], "qty": str(rng.randint(20, 100)), "unit_price": money(price[code] * TRADE)} for code in codes]}))
                out["quotations"] += 1
                opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/quotation", json={"quotation_id": q["id"], "expected_version": opp["version"]}))
                q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/send", json={"expected_version": q["version"]}))
                fate = rng.choice(["WON", "WON", "WON", "NEGOTIATION", "PROPOSAL", "LOST"])
                if fate == "WON":
                    q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/accept", json={"expected_version": q["version"]}))
                    opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "NEGOTIATION", "expected_version": opp["version"]}))
                    opp = c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "WON", "expected_version": opp["version"], "note": "Order confirmed by e-mail"}))
                    q = c.ok(sales.post(f"/api/sales/quotations/{q['id']}/convert", json={"expected_version": q["version"], "warehouse_id": W}))
                    out["orders"] += 1
                elif fate == "LOST":
                    c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": "LOST", "expected_version": opp["version"], "lost_reason": rng.choice(LOST_REASONS)}))
                elif fate == "NEGOTIATION":
                    c.ok(sales.post(f"/api/crm/opportunities/{opp['id']}/stage", json={"stage": fate, "expected_version": opp["version"]}))
                out["stage_counts"][fate] = out["stage_counts"].get(fate, 0) + 1
            if kind in ("NEW", "CONTACTED") and rng.random() < 0.5:
                c.ok(sales.post("/api/sales/followups", json={"subject": rng.choice(["Call back about pricing", "Send salon catalogue", "Confirm sampling visit", "Share distributor terms"]),
                                                              "due_date": c.d(rng.randint(-2, 10)), "lead_id": lead["id"], "assigned_to": me_id}))
                out["followups"] += 1
    # complaints from customers who have received goods
    custs = c.ok(adm.get("/api/masters/customers", params={"page_size": 100, "q": "SC-CUS"}))["items"][: n_(8)]
    for k, cu in enumerate(custs):
        with c.days_ago(rng.randint(3, 60)):
            cp = c.ok(sales.post("/api/crm/complaints", json={"customer_id": cu["id"], "description": rng.choice(["Cap leaking on several units", "Fragrance differs from the previous lot", "Carton arrived crushed", "Label misprint on batch"])}))
            if k % 3 == 0:
                c.ok(sales.post(f"/api/crm/complaints/{cp['id']}/update", json={"status": "INVESTIGATING"}))
            if k % 3 == 1:
                c.ok(sales.post(f"/api/crm/complaints/{cp['id']}/update", json={"status": "INVESTIGATING"}))
                c.ok(sales.post(f"/api/crm/complaints/{cp['id']}/update", json={"status": "RESOLVED", "resolution": "Replacement dispatched and root cause shared"}))
            out["complaints"] += 1
    return out


# ------------------------------------------------------------------ supply chain history: purchase waves, receipts, QC, production runs, supplier invoices and payments
WAVES = [  # supplier, [(code, qty, price)]
    ("DM-SUP-1", [("RM-ALOE", "100", "84"), ("RM-SURF", "220", "139"), ("RM-KAOLIN", "90", "61")]),
    ("DM-SUP-2", [("RM-OLIVE", "120", "418"), ("RM-SHEA", "90", "508"), ("RM-OUD", "5", "6150")]),
    ("DM-SUP-3", [("PM-BOTTLE-200", "700", "14"), ("PM-JAR-150", "800", "22"), ("PM-JAR-60", "300", "18")]),
    ("DM-SUP-1", [("RM-ALOE", "80", "86"), ("RM-SURF", "180", "141")]),
    ("DM-SUP-2", [("RM-OLIVE", "80", "425"), ("RM-SHEA", "60", "512")]),
    ("DM-SUP-3", [("PM-BOTTLE-200", "500", "14.5"), ("PM-JAR-150", "500", "22.5")]),
]


@step("scale_supply")
def scale_supply(c: Ctx) -> dict:
    from datetime import timedelta as _td  # noqa: F401
    from sqlalchemy import text
    from sqlalchemy.orm import Session as _S
    from app.core.db import engine
    from scripts.demo.catalogue import BOMS
    from scripts.demo.steps import buy, inspect_batch, receive, run_order, sup_ids
    rng = random.Random(SEED + 4)
    items = item_ids(c)
    buyer, adm, acct = c.u("buyer"), c.u("admin"), c.u("acct")
    out: dict = {"pos": [], "partial_pos": 0, "qc_failed_batches": [], "invoices": 0, "payments": 0, "production_orders": [], "rejected_po": None}
    rounds = max(1, round(len(WAVES) * 2 * SCALE)) if SCALE < 1 else len(WAVES) * 2
    sup_pos = []
    for n in range(rounds):
        supplier, lines = WAVES[n % len(WAVES)]
        age = 105 - n * (70 // max(rounds, 1))
        with c.days_ago(max(age, 25)):
            po = buy(c, supplier, lines, expected=7, note=f"Demo wave purchase {n + 1}")
            out["pos"].append(po["id"])
            partial = n % 5 == 2
            first = [(pi["id"], str((Decimal(pi["qty_ordered"]) * Decimal("0.6")).quantize(Decimal("0.001"))) if partial else pi["qty_ordered"]) for pi in po["items"]]
            gr = receive(c, po, first, f"DN-SC-{n + 1:03d}", f"SC{n + 1}", mfg_days=-20)
            if partial:
                out["partial_pos"] += 1
                rest = [(pi["id"], str((Decimal(pi["qty_ordered"]) * Decimal("0.4")).quantize(Decimal("0.001")))) for pi in po["items"]]
                gr2 = receive(c, po, rest, f"DN-SC-{n + 1:03d}B", f"SC{n + 1}B", mfg_days=-18)
                for ln in gr2["lines"]:
                    inspect_batch(c, ln["batch_id"], "pass")
            for k, ln in enumerate(gr["lines"]):
                if n % 6 == 5 and k == 0:
                    inspect_batch(c, ln["batch_id"], "fail", reason="Failed identity / assay test at incoming QC")
                    out["qc_failed_batches"].append(ln["batch_id"])
                else:
                    inspect_batch(c, ln["batch_id"], "pass")
            sup_pos.append((po["id"], supplier, age))
    # approval rejected / waiting
    with c.days_ago(20):
        po = buy(c, "DM-SUP-1", [("RM-ALOE", "40", "90")], approve=False, note="Demo: price above agreed rate")
        c.ok(adm.post(f"/api/procurement/purchase-orders/{po['id']}/reject", json={"expected_version": po["version"], "reason": "Price above the agreed rate; renegotiate"}))
        out["rejected_po"] = po["id"]
    # production runs: 4 products, repeated, some with wastage and one with a rejected finished batch; completion dates spread over ~100 days (documented fixture)
    runs = max(4, round(16 * SCALE))
    codes = list(BOMS)
    bom = c.refs["production_finished_goods"]["boms"]
    spread = []
    for r in range(runs):
        code = codes[r % len(codes)]
        size = BOMS[code][0]
        age = 95 - r * (80 // runs)
        with c.days_ago(max(age, 4)):
            low = r % 5 == 3
            actual = str(size - (10 if low else max(2, size // 40)))
            w = [{"category": "SPILLAGE", "qty": "6", "reason": "Spillage during filling line changeover", "department": "Production"}] if low else []
            res = run_order(c, bom[code], str(size), actual=actual, wastage=w)
            out["production_orders"].append(res["id"])
            spread.append((res["id"], max(age, 4)))
            if r % 7 == 6:
                inspect_batch(c, res["batch"], "fail", reason="Viscosity out of specification at finished-goods QC")
                out["qc_failed_batches"].append(res["batch"])
            else:
                inspect_batch(c, res["batch"], "pass")
    with _S(engine) as db:
        for oid, days in spread:
            db.execute(text("UPDATE production_orders SET completed_at = now() - make_interval(days => :d), started_at = now() - make_interval(days => :d, hours => 5) WHERE id = :i"), {"d": days, "i": oid})
        db.commit()
    # supplier invoices (three-way matched to what was received) and payments: ~60% paid in full, some half paid, the rest open (older ones overdue)
    sup = sup_ids(c)
    for k, (po_id, supplier, age) in enumerate(sup_pos):
        po = c.ok(buyer.get(f"/api/procurement/purchase-orders/{po_id}"))
        lines = [{"po_item_id": i["id"], "qty": i["qty_received"], "unit_price": i["unit_price"], "tax_rate": i["tax_rate"]} for i in po["items"] if Decimal(str(i["qty_received"])) > 0]
        if not lines:
            continue
        with c.days_ago(max(age - 3, 20)):
            inv = c.ok(acct.post("/api/accounts/supplier-invoices", headers=c.k("ssi"), json={"po_id": po_id, "supplier_invoice_no": f"SC/{supplier[-1]}/{k + 1:04d}", "invoice_date": c.d(0), "lines": lines}))
        out["invoices"] += 1
        if k % 5 in (0, 1, 2):
            amt, when = str(inv["total"]), max(age - 15, 3)
        elif k % 5 == 3:
            amt, when = money(Decimal(str(inv["total"])) / 2), max(age - 20, 3)
        else:
            continue
        with c.days_ago(when):
            c.ok(acct.post("/api/accounts/payments", headers=c.k("ssp"), json={"direction": "PAID", "party_id": sup[supplier], "amount": amt, "payment_date": c.d(0), "method": rng.choice(["BANK_TRANSFER", "UPI", "CHEQUE"]),
                                                                                  "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": amt}]}))
        out["payments"] += 1
    return out


# ------------------------------------------------------------------ Meta sandbox workspace: SIMULATED business/page/Instagram/ad accounts, campaigns and content. Nothing here is connected to Meta.
CAMPAIGNS = [  # name, objective, budget, start_age_days, length_days, final status
    ("Oud Body Wash launch (sandbox)", "AWARENESS", 60000, 85, 28, "DONE"), ("Olive Body Butter: winter care", "SALES", 90000, 70, 30, "DONE"), ("Salon partner leads: Q3", "LEADS", 45000, 66, 40, "DONE"),
    ("Detan Face Mask reels", "ENGAGEMENT", 30000, 52, 21, "DONE"), ("Festive gifting: hand creams", "SALES", 120000, 40, 35, "RUNNING"), ("Website traffic: ingredients story", "TRAFFIC", 25000, 38, 30, "PAUSED"),
    ("Fantasea range teaser", "AWARENESS", 40000, 30, 25, "RUNNING"), ("Salon backbar leads: Tier-2 cities", "LEADS", 55000, 26, 45, "RUNNING"), ("Fall Stop shampoo trial offer", "SALES", 70000, 22, 20, "PAUSED"),
    ("Brand story: made in small batches", "ENGAGEMENT", 20000, 18, 30, "RUNNING"), ("Retargeting: cart abandoners", "SALES", 35000, 14, 30, "RUNNING"), ("Calm-A-Sutra relaunch", "AWARENESS", 50000, 9, 30, "RUNNING"),
    ("Spring scrub collection", "TRAFFIC", 30000, -6, 30, "PLANNED"), ("Distributor recruitment", "LEADS", 65000, -12, 45, "PLANNED")]
CONTENT = [  # title, channel, target status
    ("Oud wash: from batch to bottle", "INSTAGRAM", "PUBLISHED"), ("Why we avoid sulphates", "INSTAGRAM", "PUBLISHED"), ("Olive butter texture reel", "INSTAGRAM", "PUBLISHED"), ("Salon partner spotlight: Kochi", "FACEBOOK", "PUBLISHED"),
    ("Behind the scenes: our filling line", "INSTAGRAM", "PUBLISHED"), ("Detan mask how-to", "INSTAGRAM", "PUBLISHED"), ("Winter skin care checklist", "FACEBOOK", "PUBLISHED"), ("Customer review roundup", "FACEBOOK", "PUBLISHED"),
    ("Festive gift box reveal", "INSTAGRAM", "SCHEDULED"), ("Diwali gifting guide", "FACEBOOK", "SCHEDULED"), ("Hand cream pack-of-5 offer", "INSTAGRAM", "SCHEDULED"), ("Fantasea teaser 1", "INSTAGRAM", "SCHEDULED"),
    ("New-year skin reset", "INSTAGRAM", "APPROVED"), ("Ingredient glossary: kaolin", "FACEBOOK", "APPROVED"), ("Founder note on small batches", "INSTAGRAM", "APPROVED"),
    ("Scrub vs mask: which when?", "INSTAGRAM", "IN_REVIEW"), ("Distributor FAQ carousel", "FACEBOOK", "IN_REVIEW"), ("Pack-of-5 unboxing", "INSTAGRAM", "IN_REVIEW"),
    ("Spring scrub concept A", "INSTAGRAM", "REJECTED"), ("Competitor comparison post", "FACEBOOK", "REJECTED"),
    ("Fall Stop shampoo story", "INSTAGRAM", "DRAFT"), ("Calm-A-Sutra moodboard", "INSTAGRAM", "DRAFT"), ("Salon tips: backbar sizes", "FACEBOOK", "DRAFT"), ("Q&A with our chemist", "INSTAGRAM", "DRAFT"), ("Packaging sustainability note", "FACEBOOK", "DRAFT")]


@step("scale_meta")
def scale_meta(c: Ctx) -> dict:
    from datetime import datetime as _dt, timezone as _tz
    mkt, adm = c.u("mkt"), c.u("admin")
    out: dict = {"campaigns": [], "content": {}, "connections": {}}
    mk = lambda kind, name, ref, parent=None: c.ok(mkt.post("/api/branding/meta/connections", json={"kind": kind, "name": name, "external_ref": ref, "mode": "DEMO", "parent_id": parent}))["id"]
    biz = mk("BUSINESS", "Mintree Naturals (SANDBOX business account)", "DEMO-BM-0001")
    page = mk("PAGE", "Mintree Naturals (SANDBOX Facebook page)", "DEMO-PG-0001", biz)
    ig = mk("INSTAGRAM", "@mintree.demo (SANDBOX Instagram professional account)", "DEMO-IG-0001", biz)
    ads = mk("AD_ACCOUNT", "Mintree Ads (SANDBOX ad account)", "DEMO-ACT-0001", biz)
    out["connections"] = {"business": biz, "page": page, "instagram": ig, "ad_account": ads}
    camp_ids = []
    for name, objective, budget, age, length, final in CAMPAIGNS[: n_(14)]:
        start = c.d(-age)
        end = c.d(-age + length)
        with c.days_ago(max(age, 0)):
            camp = c.ok(mkt.post("/api/branding/campaigns", json={"name": name, "objective": objective, "budget": str(budget), "start_date": start, "end_date": end, "meta_ad_account_id": ads}))
            if final != "PLANNED":
                c.ok(mkt.post(f"/api/branding/campaigns/{camp['id']}/status", json={"status": "RUNNING"}))
            if final in ("PAUSED", "DONE"):
                c.ok(mkt.post(f"/api/branding/campaigns/{camp['id']}/status", json={"status": final}))
        camp_ids.append(camp["id"])
    out["campaigns"] = camp_ids
    counts: dict[str, int] = {}
    now = _dt.now(_tz.utc)
    for i, (title, channel, target) in enumerate(CONTENT[: n_(25)]):
        camp = camp_ids[i % len(camp_ids)] if camp_ids else None
        page_ref = page if channel == "FACEBOOK" else ig
        body = {"campaign_id": camp, "channel": channel, "title": title, "caption": f"{title}. #mintree #naturalcare (sandbox sample caption)", "meta_page_id": page_ref}
        age = 40 - i * 1
        with c.days_ago(max(age, 1)):
            item = c.ok(mkt.post("/api/branding/content", json=body))
            if target != "DRAFT":
                c.ok(mkt.post(f"/api/branding/content/{item['id']}/submit"))
            if target == "REJECTED":
                c.ok(adm.post(f"/api/branding/content/{item['id']}/reject", json={"reason": "Claims need a source; revise the copy"}))
            elif target in ("APPROVED", "SCHEDULED", "PUBLISHED"):
                c.ok(adm.post(f"/api/branding/content/{item['id']}/approve"))
            if target == "SCHEDULED":
                c.ok(mkt.post(f"/api/branding/content/{item['id']}/schedule", json={"scheduled_for": (now + timedelta(days=3 + i)).isoformat()}))
            if target == "PUBLISHED":
                c.ok(mkt.post(f"/api/branding/content/{item['id']}/publish", json={"external_url": f"https://example.invalid/sandbox/post/{item['id']}"}))
        counts[target] = counts.get(target, 0) + 1
    out["content"] = counts
    out["metrics"] = c.ok(mkt.post("/api/branding/meta/sync-sandbox"))
    return out
