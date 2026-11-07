"""Concurrency on the money side, against the LIVE multi-process server: invoice postings, payment allocation, approval transitions."""
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import engine
from app.core.clock import business_today
from tests.conftest import mk_master
from tests.test_concurrency import N, integrity, key, seed_stock, team  # noqa: F401  (team is a fixture)

pytestmark = pytest.mark.filterwarnings("ignore")


def barrier_run(fn, n):
    b = threading.Barrier(n)
    def go(i):
        b.wait(15)
        return fn(i)
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(go, range(n)))


def test_concurrent_supplier_invoices_cannot_bill_more_than_was_received(live_users, team):
    m = mk_master("INVR")
    po, line = seed_stock(team, m, 100)
    accts = live_users("accounts_manager", n=8)
    item_id = po["items"][0]["id"]
    def post(i):
        return accts[i].post("/api/accounts/supplier-invoices", key=key("si"), json={"po_id": po["id"], "supplier_invoice_no": f"RACE-{uuid.uuid4().hex[:8]}", "invoice_date": str(business_today()),
                                                                                     "lines": [{"po_item_id": item_id, "qty": "100", "unit_price": "10", "tax_rate": "18"}]})
    rs = barrier_run(post, 8)
    codes = Counter(r.status_code for r in rs)
    assert codes == {201: 1, 409: 7}, codes
    assert all(r.json()["detail"]["code"] == "invoice_exceeds_receipt" for r in rs if r.status_code == 409)
    with engine.connect() as c:
        assert c.execute(text("SELECT qty_invoiced FROM purchase_order_items WHERE id = :i"), {"i": item_id}).scalar() == 100
        assert c.execute(text("SELECT count(*) FROM supplier_invoices WHERE po_id = :p"), {"p": po["id"]}).scalar() == 1
    integrity()


def test_concurrent_payments_never_allocate_more_than_the_invoice_is_worth(live_users, team):
    m = mk_master("PAYR")
    po, line = seed_stock(team, m, 100)
    accts = live_users("accounts_manager", n=8)
    inv = accts[0].post("/api/accounts/supplier-invoices", key=key("si"), json={"po_id": po["id"], "supplier_invoice_no": f"PAY-{uuid.uuid4().hex[:8]}", "invoice_date": str(business_today()),
                                                                                "lines": [{"po_item_id": po["items"][0]["id"], "qty": "100", "unit_price": "10", "tax_rate": "18"}]}).json()
    total = N(inv["total"])
    half = (total / 2).quantize(Decimal("0.01"))
    def pay(i):
        return accts[i].post("/api/accounts/payments", key=key("pay"), json={"direction": "PAID", "party_id": m["supplier_id"], "amount": str(half), "payment_date": str(business_today()), "method": "BANK_TRANSFER",
                                                                              "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": str(half)}]})
    rs = barrier_run(pay, 8)
    ok = [r for r in rs if r.status_code == 201]
    assert len(ok) == 2 and all(r.status_code in (201, 409, 422) for r in rs), Counter(r.status_code for r in rs)       # total/2 twice fills the invoice; the rest are refused
    with engine.connect() as c:
        paid, tot = c.execute(text("SELECT amount_paid, total FROM supplier_invoices WHERE id = :i"), {"i": inv["id"]}).one()
        assert paid <= tot and paid == half * 2
        assert c.execute(text("SELECT count(*) FROM payments WHERE party_id = :s AND direction = 'PAID'"), {"s": m["supplier_id"]}).scalar() == 2
    integrity()


def test_two_approvers_cannot_both_approve_the_same_purchase_order(live_users, team):
    m = mk_master("APPR")
    admins = live_users("system_admin", n=2)
    r = team["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "10", "unit_price": "5"}]})
    po = r.json()
    po = team["buyer"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}).json()
    rs = barrier_run(lambda i: admins[i].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}), 2)
    assert sorted(r.status_code for r in rs) == [200, 409], [r.status_code for r in rs]
    with engine.connect() as c:
        assert c.execute(text("SELECT status FROM purchase_orders WHERE id = :i"), {"i": po["id"]}).scalar() == "APPROVED"
        assert c.execute(text("SELECT count(*) FROM audit_logs WHERE entity = 'purchase_order' AND entity_id = :i AND action = 'approve'"), {"i": str(po["id"])}).scalar() == 1
    integrity()
