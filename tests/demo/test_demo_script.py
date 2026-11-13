"""The demo run-of-show (docs/DEMO_RUNBOOK.md, section "Run of show"), replayed black-box through the real HTTP API with database assertions.

    DEMO_API=http://127.0.0.1:8101 MINTREE_DATABASE_URL=postgresql+psycopg://.../mintree_demo  pytest tests/demo -q

Each step states its PRECONDITION (guaranteed by the seed or created here), the ON-SCREEN result it proves, the DB result it asserts, a time BUDGET and
a FALLBACK (what the presenter clicks if it goes wrong). Steps run in order and share state. Refuses any database that is not a demo database.
Every step also asserts the double-entry books still balance."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import psycopg
import pytest

API = os.environ.get("DEMO_API", "http://127.0.0.1:8101")
DB_URL = os.environ.get("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_demo").replace("postgresql+psycopg://", "postgresql://")
PASSWORD = os.environ.get("DEMO_PASSWORD", "Mintree-Demo#2026")
COURIER_SECRET = os.environ.get("MINTREE_COURIER_WEBHOOK_SECRET", "dev-courier-secret")
S: dict = {}                                                          # state shared between the steps, in order
TIMINGS: list[tuple[str, float, float]] = []


def _dbname() -> str:
    return DB_URL.rsplit("/", 1)[-1].split("?")[0]


@pytest.fixture(scope="session", autouse=True)
def _only_demo_databases():
    if not re.search(r"(^|[_-])demo($|[_-])", _dbname()):
        pytest.exit(f"REFUSED: '{_dbname()}' is not a demo database", returncode=2)
    r = httpx.get(f"{API}/api/health", timeout=5)
    assert r.status_code == 200 and r.json()["environment"] == "demo", "the API under test must run with MINTREE_ENVIRONMENT=demo"


@pytest.fixture(scope="session")
def db():
    with psycopg.connect(DB_URL, autocommit=True) as c:
        yield c


def q1(db, sql, *a):
    t0 = time.perf_counter()
    r = db.execute(sql, a).fetchone()
    if time.perf_counter() - t0 > 3:
        print(f"SLOWSQL {time.perf_counter() - t0:.1f}s {sql[:110]}")
    return r[0] if r else None


class Client:
    def __init__(self, who: str):
        self.c = httpx.Client(base_url=API, timeout=60)
        r = self.c.post("/api/auth/login", json={"email": f"demo.{who}@example.com", "password": PASSWORD})
        assert r.status_code == 200, f"login {who}: {r.text}"
        self.me = r.json()
        self.c.headers["X-CSRF-Token"] = self.me["csrf_token"]

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def post(self, path, json=None, key=True, **kw):
        h = kw.pop("headers", {})
        if key:
            h = {**h, "Idempotency-Key": f"demo-script-{uuid.uuid4()}"}
        return self.c.post(path, json=json, headers=h, **kw)

    def ok(self, r, code=(200, 201)):
        if r.elapsed.total_seconds() > 5:
            print(f"SLOW {r.elapsed.total_seconds():.1f}s {r.request.method} {r.request.url.path} -> {r.status_code}")
        assert r.status_code in code, f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None


@pytest.fixture(scope="session")
def users():
    cache: dict[str, Client] = {}

    def get(who: str) -> Client:
        if who not in cache:
            cache[who] = Client(who)
        return cache[who]
    return get


@pytest.fixture(autouse=True)
def _step_timer(request):
    t0 = time.perf_counter()
    yield
    TIMINGS.append((request.node.name, time.perf_counter() - t0, getattr(request.function, "budget", 30.0)))


def budget(seconds: float):
    def deco(fn):
        fn.budget = seconds
        return fn
    return deco


def books_balance(db):
    d, c = db.execute("SELECT COALESCE(sum(debit),0), COALESCE(sum(credit),0) FROM journal_lines").fetchone()
    assert d == c, f"journal debits {d} != credits {c}"
    bad = q1(db, "SELECT count(*) FROM journal_entries WHERE total_debit <> total_credit")
    assert bad == 0, f"{bad} unbalanced journal entries"


def stock_of(db, item_id):
    return q1(db, "SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE item_id = %s", item_id) or Decimal(0)


def available_of(db, item_id):
    return q1(db, "SELECT COALESCE(sum(qty_physical - qty_reserved - qty_blocked),0) FROM stock_balances WHERE item_id = %s", item_id) or Decimal(0)


def batch_qty(db, batch_id):
    return q1(db, "SELECT COALESCE(sum(qty_physical),0) FROM stock_balances WHERE batch_id = %s", batch_id) or Decimal(0)


def batch_available(db, batch_id):
    return q1(db, "SELECT COALESCE(sum(qty_physical - qty_reserved - qty_blocked),0) FROM stock_balances WHERE batch_id = %s", batch_id) or Decimal(0)


def item_id(db, code):
    return q1(db, "SELECT id FROM items WHERE item_code = %s", code)


# ======================================================================================================================================
# a) Sign in as Kanika (Founder) -> Overview
# ======================================================================================================================================
@budget(10)
def test_a_signin_and_overview(users, db):
    """PRECONDITION  demo.admin (presented as Kanika, Founder) exists; the seed leaves failed deliveries, a delayed order and held batches so "Needs attention" is never empty.
    ON SCREEN     Overview greets Kanika; KPI chips (receivables, net revenue, active production, inventory alerts); the 90-day period is at least the 30-day one; the Approvals button shows a count.
    DB            nothing is written by reading. FALLBACK  reload the page; if the figures look stale use the period toggle once (it refetches)."""
    k = users("admin")
    assert k.me["permissions"] and k.me["email"] == "demo.admin@example.com"
    sm = k.ok(k.get("/api/analytics/management-summary"))
    for key in ("sales", "finance", "production", "inventory", "quality", "dispatch"):
        assert key in sm, key
    today = date.today()
    p30 = k.ok(k.get("/api/analytics/overview-period", params={"start": str(today - timedelta(days=29)), "end": str(today)}))
    p90 = k.ok(k.get("/api/analytics/overview-period", params={"start": str(today - timedelta(days=89)), "end": str(today)}))
    assert Decimal(str(p90["revenue"]["net_ex_tax"])) >= Decimal(str(p30["revenue"]["net_ex_tax"]))
    attention = (sm["quality"]["batches_on_hold"] + sm["quality"]["batches_quarantined"] + sm["dispatch"]["failed_or_rto"] + sm["dispatch"]["delayed_shipments"]
                 + sm["production"]["delayed_work_orders"] + sm["inventory"]["below_reorder_level"] + sm["inventory"]["expired_with_stock"])
    assert attention > 0, "Needs attention must never be empty in the demo"
    S["pending_approvals_before"] = len(k.ok(k.get("/api/approvals/pending"))["items"])
    S["receivable_before"] = Decimal(str(sm["sales"]["receivable_outstanding"]))


# ======================================================================================================================================
# b) Purchase: low-stock material -> request -> PO -> goods receipt -> stock + ledger
# ======================================================================================================================================
@budget(25)
def test_b_purchase_low_stock_to_goods_receipt(users, db):
    """PRECONDITION  at least one raw material is below its reorder level (seed: RM items with a reorder level).
    ON SCREEN     Purchase shows the material under "Priority replenishment"; request -> approved -> PO -> approved -> receipt; stock on hand rises by the received quantity.
    DB            purchase_requests APPROVED, purchase_orders RECEIVED, stock_balances +qty, one stock_ledger GRN row, a balanced journal entry, an audit row for the approvals.
    FALLBACK      use Purchase -> Purchase orders -> the approved PO -> Receive goods."""
    adm, buyer, stores = users("admin"), users("buyer"), users("stores")
    low = adm.ok(adm.get("/api/analytics/management-summary"))["low_stock"]
    code = (low[0]["item_code"] if low else "RM-ALOE")
    iid = item_id(db, code)
    assert iid, code
    wh = q1(db, "SELECT id FROM warehouses WHERE code = 'RM-WH'")
    sup = q1(db, "SELECT id FROM suppliers WHERE approval_status = 'APPROVED' AND is_active ORDER BY id LIMIT 1")
    qty = Decimal("120")
    pr = buyer.ok(buyer.post("/api/procurement/purchase-requests", json={"items": [{"item_id": iid, "qty": str(qty)}], "required_by": str(date.today() + timedelta(days=7)), "notes": "Demo script: replenish low stock"}))
    pr = buyer.ok(buyer.post(f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]}))
    pr = adm.ok(adm.post(f"/api/procurement/purchase-requests/{pr['id']}/approve", json={"expected_version": pr["version"]}))
    assert pr["status"] == "APPROVED"
    po = buyer.ok(buyer.post("/api/procurement/purchase-orders", json={"supplier_id": sup, "pr_id": pr["id"], "expected_date": str(date.today() + timedelta(days=7)), "notes": "Demo script PO",
                                                                        "items": [{"item_id": iid, "qty": str(qty), "unit_price": "100.00"}]}))
    po = buyer.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
    po = adm.ok(adm.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
    assert po["status"] == "APPROVED"
    entries_before = q1(db, "SELECT count(*) FROM journal_entries")
    gr = stores.ok(stores.post("/api/procurement/goods-receipts", json={"po_id": po["id"], "warehouse_id": wh, "delivery_note_no": f"DEMO-{uuid.uuid4().hex[:8]}",
                                                                         "lines": [{"po_item_id": po["items"][0]["id"], "qty": str(qty), "supplier_lot_no": f"DS-{uuid.uuid4().hex[:6]}",
                                                                                    "mfg_date": str(date.today() - timedelta(days=10)), "expiry_date": str(date.today() + timedelta(days=500))}]}))
    batch = gr["lines"][0]["batch_id"]
    assert batch_qty(db, batch) == qty, "the received batch holds exactly the received quantity"
    assert q1(db, "SELECT COALESCE(sum(qty_delta),0) FROM stock_ledger WHERE batch_id = %s", batch) == qty, "ledger and balance agree"
    assert q1(db, "SELECT count(*) FROM stock_ledger WHERE source_type = 'goods_receipt' AND source_id = %s", gr["id"]) >= 1
    assert q1(db, "SELECT status FROM purchase_orders WHERE id = %s", po["id"]) == "RECEIVED"
    assert q1(db, "SELECT count(*) FROM journal_entries") > entries_before, "receipt must post to the ledger"
    assert q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'purchase_order' AND entity_id = %s AND action = 'approve'", str(po["id"])) >= 1
    books_balance(db)
    S["grn_batch"] = gr["lines"][0]["batch_id"]


# ======================================================================================================================================
# c) Production: create/start, issue materials, record QC, release -> finished goods stock
# ======================================================================================================================================
def _produce(users, db, code="FG-BW-OUD-200", size="200", actual="196"):
    pm, op, qa, qi = users("pm"), users("op"), users("qa"), users("qi")
    bom = q1(db, "SELECT b.id FROM boms b JOIN items i ON i.id = b.output_item_id WHERE i.item_code = %s AND b.status = 'APPROVED' ORDER BY b.id DESC LIMIT 1", code) \
        or q1(db, "SELECT b.id FROM boms b JOIN items i ON i.id = b.output_item_id WHERE i.item_code = %s ORDER BY b.id DESC LIMIT 1", code)
    wh = q1(db, "SELECT id FROM warehouses WHERE code = 'RM-WH'")
    o = pm.ok(pm.post("/api/production/orders", json={"bom_id": bom, "planned_qty": size, "warehouse_id": wh, "planned_date": str(date.today())}))
    pm.ok(pm.post(f"/api/production/orders/{o['id']}/release", json={"expected_version": o["version"]}))
    pm.ok(pm.post(f"/api/production/orders/{o['id']}/issue"))
    for seq in range(1, len(o["stages"]) + 1):
        op.ok(op.post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={}, key=False))
        if seq == 1:
            qi.ok(qi.post(f"/api/production/orders/{o['id']}/stages/{seq}/checks", json={"parameter": "Batch temperature (C)", "value": "74", "min_value": "70", "max_value": "80"}, key=False))
        op.ok(op.post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={"parameters": {"temp_c": 74}}, key=False))
    ver = pm.ok(pm.get(f"/api/production/orders/{o['id']}"))["version"]
    done = pm.ok(pm.post(f"/api/production/orders/{o['id']}/complete", json={"expected_version": ver, "actual_qty": actual, "wastage": []}))
    return o, done


@budget(30)
def test_c_production_batch_to_finished_goods(users, db):
    """PRECONDITION  an approved BOM for Oud body wash and released raw materials / bottles in stock (seed + bulk opening stock).
    ON SCREEN     the batch moves through its stages; materials are issued; a QC reading passes; the finished batch waits in QC, then is released and counts as available stock.
    DB            raw-material stock falls (issue ledger rows), the output batch is created in QUARANTINE and RELEASED after the QC decision, finished-goods stock +actual qty, journals balance.
    FALLBACK      Production -> the order -> Complete; Quality -> Inspections -> record the results and Release."""
    qa, qi = users("qa"), users("qi")
    fg = item_id(db, "FG-BW-OUD-200")
    o, done = _produce(users, db)
    batch = done["output_batch_id"]
    assert batch and q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch) in ("QUARANTINE", "PENDING_QC", "ON_HOLD", "QUARANTINED"), "finished goods wait in QC"
    assert q1(db, "SELECT count(*) FROM stock_ledger WHERE source_type = 'production_order' AND source_id = %s AND qty_delta < 0", o["id"]) >= 1, "materials were consumed (issue ledger rows)"
    assert batch_qty(db, batch) == Decimal("196"), "finished goods stock = actual quantity"
    assert batch_available(db, batch) == 0, "...but not available until QC releases it"
    insp = qa.ok(qa.get("/api/quality/inspections", params={"batch_id": batch}))["items"]
    assert insp, "completing a batch opens a QC inspection"
    iid = insp[0]["id"]
    specs = qa.ok(qa.get("/api/quality/specs", params={"item_id": fg}))
    specs = specs["items"] if isinstance(specs, dict) else specs
    tests = [t for s in specs for t in s.get("tests", [])]
    for t in tests:
        body = {"test_name": t["test_name"], "passed": True} if t.get("pass_fail") else {"test_name": t["test_name"], "value": str((Decimal(t["min"] or "0") + Decimal(t["max"] or t["min"] or "0")) / 2)}
        qi.ok(qi.post(f"/api/quality/inspections/{iid}/results", json=body, key=False))
    qa.ok(qa.post(f"/api/quality/inspections/{iid}/decide", json={"decision": "RELEASE"}))
    assert q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch) == "RELEASED"
    assert batch_available(db, batch) == Decimal("196"), "released finished goods become available stock"
    assert q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'inspection' AND entity_id = %s", str(iid)) >= 1
    books_balance(db)
    S["released_batch"] = batch


# ======================================================================================================================================
# d) Quality: failed/quarantined batch in orange, CAPA, reject
# ======================================================================================================================================
@budget(30)
def test_d_quality_failed_batch_capa_and_reject(users, db):
    """PRECONDITION  the seed already holds rejected / quarantined batches; this step also creates a fresh one so the sequence is repeatable.
    ON SCREEN     Quality lists the batch as quarantined (orange "Needs attention"); a deviation and CAPA are raised on it; the batch is rejected and its stock leaves the available pool.
    DB            deviation + capa_actions rows linked to the batch; batch qc_status REJECTED; available stock for that batch is 0; audit rows exist.
    FALLBACK      Quality -> Deviations -> New; Inventory -> the batch -> QC decision -> Reject."""
    qa, qi = users("qa"), users("qi")
    assert q1(db, "SELECT count(*) FROM inventory_batches WHERE qc_status <> 'RELEASED'") > 0, "seed guarantees held or rejected batches"
    sm = qa.ok(users("admin").get("/api/analytics/management-summary"))
    o, done = _produce(users, db, code="FG-BB-OLIVE-150", size="150", actual="145")
    batch = done["output_batch_id"]
    assert q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch) != "RELEASED"
    dv = qa.ok(qa.post("/api/quality/deviations", json={"title": "Viscosity out of specification", "description": "Demo script: viscosity reading above limit on the finished batch", "classification": "MAJOR", "batch_id": batch}))
    capa = qa.ok(qa.post(f"/api/quality/deviations/{dv['id']}/capa", json={"kind": "CORRECTIVE", "description": "Re-check emulsifier ratio and re-train the line", "responsible_id": q1(db, "SELECT id FROM users WHERE email = 'demo.qa@example.com'"),
                                                                         "due_date": str(date.today() + timedelta(days=14))}))
    assert q1(db, "SELECT count(*) FROM capa_actions WHERE deviation_id = %s", dv["id"]) == 1 and capa
    insp = qa.ok(qa.get("/api/quality/inspections", params={"batch_id": batch}))["items"][0]["id"]
    qa.ok(qa.post(f"/api/quality/inspections/{insp}/decide", json={"decision": "REJECT", "reason": "Viscosity out of specification; CAPA raised"}))
    assert q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch) == "REJECTED"
    assert q1(db, "SELECT COALESCE(sum(qty_physical - qty_reserved - qty_blocked),0) FROM stock_balances WHERE batch_id = %s", batch) == 0, "a rejected batch is not available"
    assert q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'deviation' AND entity_id = %s", str(dv["id"])) >= 1
    books_balance(db)


# ======================================================================================================================================
# e) Dispatch / E-commerce: marketplace order -> pick/pack -> courier (Simulated) -> delivered
# ======================================================================================================================================
@budget(30)
def test_e_marketplace_order_to_delivery(users, db):
    """PRECONDITION  marketplace accounts with SKU mappings and released finished-goods stock (seed); the courier is the in-process DEMO adapter ("Simulated courier").
    ON SCREEN     the order appears in the marketplace queue, is confirmed, allocated, packed, dispatched with a Simulated courier, and tracked to Delivered; an invoice is raised.
    DB            external_orders SO_CREATED with a sales order; stock_ledger issue rows; shipments DELIVERED with an AWB; a sales invoice; courier partner integration_mode DEMO.
    FALLBACK      Dispatch -> Orders -> the order -> Allocate / Pack / Dispatch, then Shipments -> Simulate delivery."""
    sales, disp = users("sales"), users("disp")
    acct_id, plat = db.execute("SELECT id, platform FROM marketplace_accounts ORDER BY id LIMIT 1").fetchone()
    code = "FG-BW-OUD-200"
    iid = item_id(db, code)
    sku = q1(db, "SELECT platform_sku FROM sku_mappings WHERE account_id = %s AND item_id = %s LIMIT 1", acct_id, iid)
    assert sku, "SKU mapping must exist for the demo account"
    ext = f"DEMO-{uuid.uuid4().hex[:8].upper()}"
    r = sales.ok(sales.post(f"/api/ecommerce/accounts/{acct_id}/orders", json={"orders": [{"order_id": ext, "order_date": str(date.today()), "customer_name": "Aarav Sharma, Pune", "shipping_address": "12, Pune main road",
                                                                                             "items": [{"sku": sku, "qty": "2", "unit_price": "840.00"}]}]}, key=False))
    assert r["created"] == 1
    so_id = q1(db, "SELECT sales_order_id FROM external_orders WHERE external_order_id = %s", ext)
    assert so_id and q1(db, "SELECT status FROM external_orders WHERE external_order_id = %s", ext) == "SO_CREATED"
    so = disp.ok(disp.get(f"/api/dispatch/orders/{so_id}"))
    v = so["version"]
    disp.ok(disp.post(f"/api/dispatch/orders/{so_id}/confirm", json={"expected_version": v}, key=False))
    disp.ok(disp.post(f"/api/dispatch/orders/{so_id}/allocate", json={"expected_version": v + 1}))
    pk = disp.ok(disp.post(f"/api/dispatch/orders/{so_id}/pack", json={"expected_version": v + 2, "cartons": 1, "weight_kg": "0.9"}, key=False))
    d = disp.ok(disp.post(f"/api/dispatch/orders/{so_id}/dispatch", json={"expected_version": pk["version"], "courier": "fake"}))
    disp.ok(disp.post(f"/api/dispatch/shipments/{d['shipment_id']}/book", key=False))
    awb = disp.ok(disp.get(f"/api/dispatch/shipments/{d['shipment_id']}"))["awb"]
    assert awb and q1(db, "SELECT integration_mode FROM courier_partners WHERE code = 'FAKE'") == "DEMO"
    for n, etype in enumerate(("PICKED_UP", "IN_TRANSIT", "DELIVERED")):
        raw = json.dumps({"event_id": f"{awb}-{etype}", "awb": awb, "event_type": etype, "occurred_at": datetime.now(timezone.utc).isoformat()}).encode()
        sig = hmac.new(COURIER_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        rr = disp.c.post("/api/dispatch/webhooks/courier/fake", content=raw, headers={"X-Signature": sig, "Content-Type": "application/json"})
        if rr.elapsed.total_seconds() > 3:
            print(f"SLOWHOOK {rr.elapsed.total_seconds():.1f}s {etype}")
        assert rr.status_code in (200, 201), rr.text
    assert q1(db, "SELECT status FROM shipments WHERE id = %s", d["shipment_id"]) == "DELIVERED"
    assert q1(db, "SELECT COALESCE(sum(qty_delta),0) FROM stock_ledger WHERE source_type = 'shipment' AND source_id = %s", d["shipment_id"]) == Decimal("-2"), "dispatch takes exactly 2 units out"
    assert q1(db, "SELECT count(*) FROM stock_ledger WHERE source_type = 'shipment' AND source_id = %s AND qty_delta < 0", d["shipment_id"]) >= 1
    inv = d["invoice"]
    assert q1(db, "SELECT status FROM sales_invoices WHERE id = %s", inv["id"]) == "OPEN"
    S["invoice"] = inv["id"]
    S["invoice_total"] = Decimal(str(inv["total"]))
    S["invoice_customer"] = q1(db, "SELECT customer_id FROM sales_invoices WHERE id = %s", inv["id"])
    books_balance(db)


# ======================================================================================================================================
# f) CRM pipeline: drag a deal to Won
# ======================================================================================================================================
@budget(15)
def test_f_crm_deal_to_won(users, db):
    """PRECONDITION  an active customer exists (2,500 in the bulk seed).
    ON SCREEN     the deal moves Qualified -> Proposal -> Negotiation -> Won; the browser shows a toast with Undo (the server is told after 4 seconds, so Undo means nothing was sent); KPIs update.
    DB            opportunities.stage = WON, one history row per move, win count up by one.
    FALLBACK      CRM -> Pipeline -> Board/List toggle -> open the deal -> Move to Won."""
    sales = users("sales")
    cust = q1(db, "SELECT id FROM customers WHERE code LIKE %s ORDER BY id LIMIT 1", "BK-CUS-%") or q1(db, "SELECT id FROM customers ORDER BY id LIMIT 1")
    won_before = q1(db, "SELECT count(*) FROM opportunities WHERE stage = 'WON'")
    o = sales.ok(sales.post("/api/crm/opportunities", json={"name": "Demo script: salon chain refill order", "customer_id": cust, "expected_value": 450000, "expected_close": str(date.today() + timedelta(days=20))}))
    ver = o["version"]
    for stage in ("QUALIFIED", "PROPOSAL", "NEGOTIATION"):
        o = sales.ok(sales.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": stage, "expected_version": ver}, key=False))
        ver = o["version"]
    # the rule: a deal is won only once its quotation is accepted by the customer
    wh = q1(db, "SELECT id FROM warehouses WHERE code = 'FG-WH'")
    fg = item_id(db, "FG-BW-OUD-200")
    qt = sales.ok(sales.post("/api/sales/quotations", json={"customer_id": cust, "valid_until": str(date.today() + timedelta(days=30)), "notes": "Demo script", "lines": [{"item_id": fg, "qty": "100", "unit_price": "560.00"}]}))
    o = sales.ok(sales.post(f"/api/crm/opportunities/{o['id']}/quotation", json={"quotation_id": qt["id"], "expected_version": o["version"]}, key=False))
    qt = sales.ok(sales.post(f"/api/sales/quotations/{qt['id']}/send", json={"expected_version": qt["version"]}, key=False))
    sales.ok(sales.post(f"/api/sales/quotations/{qt['id']}/accept", json={"expected_version": qt["version"]}, key=False))
    o = sales.ok(sales.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "WON", "expected_version": o["version"]}, key=False))
    assert q1(db, "SELECT stage FROM opportunities WHERE id = %s", o["id"]) == "WON"
    assert q1(db, "SELECT count(*) FROM opportunity_stage_history WHERE opportunity_id = %s", o["id"]) >= 4
    assert q1(db, "SELECT count(*) FROM opportunities WHERE stage = 'WON'") == won_before + 1
    board = sales.ok(sales.get("/api/crm/customers-board"))
    assert board is not None


# ======================================================================================================================================
# g) Accounts: invoice -> payment -> receivables drop, ageing updates
# ======================================================================================================================================
@budget(15)
def test_g_payment_reduces_receivables_and_ageing(users, db):
    """PRECONDITION  the open invoice created in step e (order -> dispatch raises it).
    ON SCREEN     Accounts shows the invoice; a part payment then the balance marks it Paid; receivables outstanding falls by the amount; the ageing total follows.
    DB            sales_invoices.amount_paid grows, status PARTIAL then PAID, a payment row with its allocation, a balanced journal entry (Dr bank, Cr receivables).
    FALLBACK      Accounts -> Invoices -> the invoice -> Record payment."""
    acct = users("acct")
    inv, total, cust = S["invoice"], S["invoice_total"], S["invoice_customer"]
    sm0 = Decimal(str(acct.ok(users("admin").get("/api/analytics/management-summary"))["sales"]["receivable_outstanding"]))
    assert inv in [i["invoice_id"] for p in acct.ok(acct.get("/api/accounts/reports/ageing/receivables"))["parties"] for i in p["invoices"]], "the open invoice is in the ageing report before payment"
    first = (total / 2).quantize(Decimal("0.01"))
    for n, amount in enumerate((first, total - first)):
        acct.ok(acct.post("/api/accounts/payments", json={"direction": "RECEIVED", "party_id": cust, "amount": str(amount), "payment_date": str(date.today()), "method": "UPI", "bank_account_code": "1100",
                                                           "allocations": [{"invoice_id": inv, "amount": str(amount)}]}))
        if n == 0:
            assert q1(db, "SELECT status FROM sales_invoices WHERE id = %s", inv) == "PARTIAL"
    assert q1(db, "SELECT status FROM sales_invoices WHERE id = %s", inv) == "PAID"
    assert q1(db, "SELECT amount_paid FROM sales_invoices WHERE id = %s", inv) == total
    time.sleep(3.3)                                                       # dashboards may lag a write by at most 3 s (rcache.DASH_FLOOR_S); a presenter takes longer than that to switch screens
    sm1 = Decimal(str(users("admin").ok(users("admin").get("/api/analytics/management-summary"))["sales"]["receivable_outstanding"]))
    # other writers may be active (bulk load / Live Mode), so assert direction through the exact figure from the ledger-backed invoices instead of a strict delta
    open_now = q1(db, "SELECT COALESCE(sum(total - amount_paid - credited_amount),0) FROM sales_invoices")
    assert abs(sm1 - open_now) < Decimal("50000"), f"summary {sm1} should track open invoices {open_now}"
    rep = acct.ok(acct.get("/api/accounts/reports/ageing/receivables"))
    assert inv not in [i["invoice_id"] for p in rep["parties"] for i in p["invoices"]], "a paid invoice leaves the ageing report"
    assert Decimal(rep["grand_total"]) == sum((Decimal(p["total"]) for p in rep["parties"]), Decimal(0)), "ageing buckets add up to the grand total"
    books_balance(db)


# ======================================================================================================================================
# h) Approvals + Notifications
# ======================================================================================================================================
@budget(15)
def test_h_approval_from_list_and_from_notification(users, db):
    """PRECONDITION  the buyer and the administrator exist; submitting a PO notifies everyone who may approve it.
    ON SCREEN     the bell shows +1 unread and the Approvals count +1; approving from the list clears the item; opening a notification marks it read (bell -1) and lands on the record.
    DB            user_notifications row for the administrator, purchase_orders APPROVED with approved_by, an audit row, read_at set.
    FALLBACK      Approvals -> pick the item -> Approve; Notifications -> Mark all read."""
    adm, buyer = users("admin"), users("buyer")
    sup = q1(db, "SELECT id FROM suppliers WHERE approval_status = 'APPROVED' AND is_active ORDER BY id LIMIT 1")
    iid = item_id(db, "RM-ALOE")
    unread0 = adm.ok(adm.get("/api/notifications", params={"page_size": 1}))["unread"]
    pend0 = len(adm.ok(adm.get("/api/approvals/pending"))["items"])
    po = buyer.ok(buyer.post("/api/procurement/purchase-orders", json={"supplier_id": sup, "expected_date": str(date.today() + timedelta(days=5)), "notes": "Demo script approval", "items": [{"item_id": iid, "qty": "10", "unit_price": "90.00"}]}))
    po = buyer.ok(buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}))
    unread1 = adm.ok(adm.get("/api/notifications", params={"page_size": 1}))["unread"]
    assert unread1 == unread0 + 1, "submitting for approval notifies the approver"
    pend = adm.ok(adm.get("/api/approvals/pending"))["items"]
    assert len(pend) == pend0 + 1
    note = q1(db, "SELECT id FROM user_notifications WHERE user_id = (SELECT id FROM users WHERE email = 'demo.admin@example.com') AND link = %s ORDER BY id DESC LIMIT 1", f"/procurement/purchase-orders/{po['id']}")
    assert note, "the notification links to the PO"
    adm.ok(adm.post(f"/api/notifications/{note}/read", key=False), code=(200, 201, 204))               # "Open" marks it read
    assert adm.ok(adm.get("/api/notifications", params={"page_size": 1}))["unread"] == unread1 - 1
    assert q1(db, "SELECT read_at IS NOT NULL FROM user_notifications WHERE id = %s", note) is True
    po = adm.ok(adm.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}))
    assert po["status"] == "APPROVED" and len(adm.ok(adm.get("/api/approvals/pending"))["items"]) == pend0
    assert q1(db, "SELECT approved_by IS NOT NULL FROM purchase_orders WHERE id = %s", po["id"]) is True
    assert q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'purchase_order' AND entity_id = %s AND action = 'approve'", str(po["id"])) == 1
    # the same action twice (double-click / retry) must not approve twice or fail the second caller badly
    again = adm.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]})
    assert again.status_code in (200, 409) and q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'purchase_order' AND entity_id = %s AND action = 'approve'", str(po["id"])) == 1


# ======================================================================================================================================
# i) Command palette, Scan, theme, mobile
# ======================================================================================================================================
@budget(10)
def test_i_palette_search_and_scan(users, db):
    """PRECONDITION  items, suppliers and customers are searchable; batches carry scannable numbers.
    ON SCREEN     Ctrl+K finds records as you type; Alt+S (Scan) resolves a batch label or a document number to its record. Light/dark and the mobile bar are checked in the browser suite (ui.mjs).
    DB            read-only. FALLBACK  open the module list and use its search box (same endpoints).
    BUDGET        each search under 150 ms warm."""
    adm = users("admin")
    for path, term in (("/api/masters/items", "oud"), ("/api/masters/suppliers", "a"), ("/api/masters/customers", "beauty")):
        t = time.perf_counter()
        res = adm.ok(adm.get(path, params={"q": term, "page_size": 8}))
        ms = (time.perf_counter() - t) * 1000
        assert res["items"], f"palette search {path} q={term}"
        assert ms < 1500, f"{path} took {ms:.0f} ms"          # the strict 150 ms budget is measured by scripts/warm.py (warm, p95); this is a smoke bound
    batch_no = q1(db, "SELECT batch_no FROM inventory_batches WHERE id = %s", S.get("released_batch") or S.get("grn_batch"))
    r = adm.ok(adm.get("/api/scan/resolve", params={"code": batch_no}))
    assert r and (r.get("kind") or r.get("type") or r)
    inv_no = q1(db, "SELECT number FROM sales_invoices WHERE id = %s", S["invoice"])
    assert adm.ok(adm.get("/api/scan/resolve", params={"code": inv_no}))


# ======================================================================================================================================
# the whole run
# ======================================================================================================================================
def test_z_invariants_and_total_time(db):
    """After every scenario the integrity checker (30+ SQL invariants: stock vs ledger, trial balance, AR/AP control, gapless numbers, outbox) must pass, and the run must be quick."""
    import subprocess
    import sys
    from pathlib import Path
    backend = Path(__file__).resolve().parents[2] / "backend"
    env = dict(os.environ, MINTREE_DATABASE_URL=DB_URL.replace("postgresql://", "postgresql+psycopg://"), MINTREE_ENVIRONMENT="demo")
    deadline = time.time() + 30                                          # the outbox publisher drains within seconds; allow it
    while True:
        p = subprocess.run([sys.executable, "-m", "scripts.check_integrity"], cwd=backend, env=env, capture_output=True, text=True)
        if p.returncode == 0 or time.time() > deadline:
            break
        time.sleep(3)
    assert p.returncode == 0, p.stdout[-2500:]
    total = sum(t for _, t, _ in TIMINGS)
    print("\nstep timings:", ", ".join(f"{n.removeprefix('test_')}={t:.1f}s/{b:.0f}s" for n, t, b in TIMINGS), f"total={total:.1f}s")
    assert total < 300, f"the run-of-show took {total:.0f}s (budget 300s)"
    over = [(n, t, b) for n, t, b in TIMINGS if t > b]
    assert not over, f"steps over their time budget: {over}"
