"""Purchase returns and PO amendments: business rules, atomicity, idempotency, permissions, and direct DB verification."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import mk_master
from tests.test_accounts import fin, tb  # noqa: F401
from tests.test_accounts_gaps import supplier_invoice
from app.core.clock import business_today
from tests.test_inventory_procurement import N, assert_integrity, avail, po_to_approved, qc, receive


def k(p="k"):
    return {"Idempotency-Key": f"{p}-{uuid.uuid4().hex[:14]}"}


def stocked(fin, qty="100", price="10"):
    m = mk_master("RET")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty=qty, price=price)
    gr = receive(fin["stores"], po, m["wh1"], qty, f"DN-{uuid.uuid4().hex[:6]}", f"gr-{uuid.uuid4().hex[:10]}")
    assert gr.status_code == 201, gr.text
    return m, po, gr.json()


def gr_item_id(db, gr_id):
    return db.scalar(text("SELECT id FROM goods_receipt_items WHERE gr_id=:g ORDER BY id LIMIT 1"), {"g": gr_id})


def do_return(client, gr, line_id, qty, reason_type="QC_REJECTED", headers=None):
    return client.post(f"/api/procurement/goods-receipts/{gr['id']}/returns", headers=headers or k("ret"),
                       json={"reason_type": reason_type, "reason": "Failed incoming QC", "lines": [{"gr_item_id": line_id, "qty": qty}]})


def test_return_moves_stock_po_and_books_together(fin, db):
    m, po, gr = stocked(fin)
    line = gr_item_id(db, gr["id"])
    r = do_return(fin["stores"], gr, line, "30")
    assert r.status_code == 201, r.text
    body = r.json()
    assert N(body["total_value"]) == N("300.00") and body["po_status"] == "PARTIALLY_RECEIVED"
    t = avail(fin["stores"], m["item_id"])
    assert (N(t["physical"]), N(t["blocked"]), N(t["available"])) == (70, 70, 0)       # quarantined stock leaves from the blocked side
    assert db.scalar(text("SELECT qty_received FROM purchase_order_items WHERE po_id=:p"), {"p": po["id"]}) == N("70")
    led = db.execute(text("SELECT qty_delta, blocked_delta, movement_type, source_type FROM stock_ledger WHERE source_type='purchase_return' AND source_id=:i"), {"i": body["id"]}).all()
    assert [(l[0], l[1], l[2]) for l in led] == [(N("-30"), N("-30"), "ISSUE")]
    je = db.execute(text("SELECT total_debit, total_credit FROM journal_entries WHERE source_type='purchase_return' AND source_id=:i"), {"i": body["id"]}).one()
    assert je[0] == je[1] == N("300.00")
    deb = db.execute(text("SELECT a.code, l.debit, l.credit FROM journal_lines l JOIN accounts a ON a.id=l.account_id JOIN journal_entries e ON e.id=l.entry_id "
                          "WHERE e.source_type='purchase_return' AND e.source_id=:i ORDER BY a.code"), {"i": body["id"]}).all()
    assert [(c, d, cr) for c, d, cr in deb] == [("1300", N("0"), N("300.00")), ("2100", N("300.00"), N("0"))]   # Dr GRNI / Cr Inventory (reverses the receipt)
    assert db.scalar(text("SELECT count(*) FROM audit_logs WHERE entity='purchase_return' AND entity_id=:i"), {"i": str(body["id"])}) == 1
    assert db.scalar(text("SELECT count(*) FROM outbox_events WHERE event_type='procurement.purchase_return.posted' AND aggregate_id=:i"), {"i": str(body["id"])}) == 1
    assert_integrity(db)


def test_cannot_return_more_than_received_and_cap_is_cumulative(fin, db):
    m, po, gr = stocked(fin)
    line = gr_item_id(db, gr["id"])
    assert do_return(fin["stores"], gr, line, "60").status_code == 201
    over = do_return(fin["stores"], gr, line, "41")
    assert over.status_code == 409 and over.json()["detail"]["code"] == "exceeds_received"
    assert do_return(fin["stores"], gr, line, "40").status_code == 201
    assert do_return(fin["stores"], gr, line, "0.001").status_code == 409
    assert N(avail(fin["stores"], m["item_id"])["physical"]) == 0
    assert do_return(fin["stores"], gr, line, "-1").status_code == 422
    assert do_return(fin["stores"], gr, line, "0").status_code == 422


def test_idempotent_replay_does_not_double_post(fin, db):
    m, po, gr = stocked(fin)
    line, h = gr_item_id(db, gr["id"]), k("same")
    a, b = do_return(fin["stores"], gr, line, "10", headers=h), do_return(fin["stores"], gr, line, "10", headers=h)
    assert a.status_code == b.status_code == 201 and a.json() == b.json() and b.headers.get("Idempotent-Replayed") == "true"
    assert N(avail(fin["stores"], m["item_id"])["physical"]) == 90
    assert db.scalar(text("SELECT count(*) FROM purchase_returns WHERE goods_receipt_id=:g"), {"g": gr["id"]}) == 1
    reuse = do_return(fin["stores"], gr, line, "20", headers=h)                                 # same key, different payload is refused
    assert reuse.status_code >= 400 and N(avail(fin["stores"], m["item_id"])["physical"]) == 90


def test_invoiced_quantity_must_use_a_debit_note(fin, db):
    m, inv = supplier_invoice(fin, qty="100", price="10")
    gr_id = db.scalar(text("SELECT gr.id FROM goods_receipts gr WHERE gr.supplier_id=:s ORDER BY gr.id DESC LIMIT 1"), {"s": m["supplier_id"]})
    line = gr_item_id(db, gr_id)
    r = do_return(fin["stores"], {"id": gr_id}, line, "5")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_invoiced"
    assert db.scalar(text("SELECT count(*) FROM purchase_returns")) == 0                       # nothing half-posted


def test_reserved_stock_cannot_be_returned_and_failure_rolls_back_everything(fin, db):
    m, po, gr = stocked(fin)
    line = gr_item_id(db, gr["id"])
    batch = gr["lines"][0]["batch_id"]
    assert qc(fin["admin"], batch, "RELEASED", f"rel-{uuid.uuid4().hex[:10]}").status_code == 200
    assert fin["admin"].post("/api/inventory/reservations", headers=k("res"), json={"item_id": m["item_id"], "warehouse_id": m["wh1"], "qty": "90",
                                                                                   "reference_type": "production_order", "reference_id": "X-1"}).status_code == 201
    j_before = db.scalar(text("SELECT count(*) FROM journal_entries"))
    r = do_return(fin["stores"], gr, line, "20")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stock_not_on_hand"
    assert db.scalar(text("SELECT count(*) FROM journal_entries")) == j_before                  # journal not left behind
    assert db.scalar(text("SELECT qty_received FROM purchase_order_items WHERE po_id=:p"), {"p": po["id"]}) == N("100")
    assert db.scalar(text("SELECT count(*) FROM stock_ledger WHERE source_type='purchase_return'")) == 0
    ok = do_return(fin["stores"], gr, line, "10")                                                # exactly the free quantity
    assert ok.status_code == 201
    assert_integrity(db)


def test_replacement_can_be_received_after_a_return(fin, db):
    m, po, gr = stocked(fin)
    assert do_return(fin["stores"], gr, gr_item_id(db, gr["id"]), "100").status_code == 201
    assert db.scalar(text("SELECT status FROM purchase_orders WHERE id=:p"), {"p": po["id"]}) == "APPROVED"
    again = receive(fin["stores"], po, m["wh1"], "100", f"DN-{uuid.uuid4().hex[:6]}", f"gr-{uuid.uuid4().hex[:10]}")
    assert again.status_code == 201 and again.json()["po_status"] == "RECEIVED"
    assert_integrity(db)


def test_return_permissions(fin, db, login, anon):
    m, po, gr = stocked(fin)
    line = gr_item_id(db, gr["id"])
    assert do_return(login("qc_inspector"), gr, line, "1").status_code == 403
    assert do_return(login("management"), gr, line, "1").status_code == 403
    assert login("management").get("/api/procurement/purchase-returns").status_code == 200
    assert do_return(anon, gr, line, "1").status_code == 401
    assert do_return(fin["stores"], {"id": 999999}, line, "1").status_code == 404
    assert do_return(fin["stores"], gr, 999999, "1").status_code == 422                         # line of another/unknown receipt


# ---------------------------------------------------------------- amendments
def amend(client, po, lines, reason="Supplier agreed revised terms"):
    return client.post(f"/api/procurement/purchase-orders/{po['id']}/amendments", json={"reason": reason, "lines": lines})


def test_amendment_changes_nothing_until_approved_by_someone_else(fin, db):
    m = mk_master("AMD")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty="100", price="10")
    pid, line = po["id"], po["items"][0]["id"]
    r = amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "120", "new_unit_price": "11"}])
    assert r.status_code == 201, r.text
    am = r.json()
    assert am["status"] == "PENDING" and N(am["total_before"]) == N("1180.00") and N(am["total_after"]) == N("1557.60")   # 120*11*1.18
    assert db.scalar(text("SELECT qty_ordered FROM purchase_order_items WHERE id=:i"), {"i": line}) == N("100")             # untouched while pending
    assert amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "130"}]).status_code == 409                              # one pending at a time
    own = fin["buyer"].post(f"/api/procurement/po-amendments/{am['id']}/approve", json={})
    assert own.status_code == 409 and own.json()["detail"]["code"] == "self_approval_not_allowed"
    ok = fin["admin"].post(f"/api/procurement/po-amendments/{am['id']}/approve", json={})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    row = db.execute(text("SELECT qty_ordered, unit_price FROM purchase_order_items WHERE id=:i"), {"i": line}).one()
    assert (row[0], row[1]) == (N("120"), N("11.00"))
    assert db.scalar(text("SELECT total_amount FROM purchase_orders WHERE id=:p"), {"p": pid}) == N("1557.60")
    assert fin["admin"].post(f"/api/procurement/po-amendments/{am['id']}/approve", json={}).status_code == 409             # decided once
    assert db.scalar(text("SELECT count(*) FROM audit_logs WHERE entity='purchase_order' AND entity_id=:p AND action IN ('amendment_request','amendment_approve')"), {"p": str(pid)}) == 2
    hist = fin["buyer"].get(f"/api/procurement/purchase-orders/{pid}/amendments").json()["items"]
    assert [h["amendment_no"] for h in hist] == [1] and hist[0]["lines"][0]["old_qty"] == "100.000"                           # history keeps the old values


def test_reject_needs_reason_and_leaves_po_unchanged(fin, db):
    m = mk_master("AMR")
    po = po_to_approved(fin["buyer"], fin["admin"], m)
    am = amend(fin["buyer"], po, [{"po_item_id": po["items"][0]["id"], "new_qty": "50"}]).json()
    assert fin["admin"].post(f"/api/procurement/po-amendments/{am['id']}/reject", json={}).status_code == 422
    r = fin["admin"].post(f"/api/procurement/po-amendments/{am['id']}/reject", json={"reason": "Not agreed"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert db.scalar(text("SELECT qty_ordered FROM purchase_order_items WHERE id=:i"), {"i": po["items"][0]["id"]}) == N("100")
    assert amend(fin["buyer"], po, [{"po_item_id": po["items"][0]["id"], "new_qty": "60"}]).status_code == 201               # a new one may follow a rejection


def partial_po(fin, recv="60", ordered="100", price="10"):
    m = mk_master("AMX")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty=ordered, price=price)
    assert receive(fin["stores"], po, m["wh1"], recv, f"DN-{uuid.uuid4().hex[:6]}", f"gr-{uuid.uuid4().hex[:10]}").status_code == 201
    return m, po


def test_amendment_rules_received_and_status(fin, db):
    m, po = partial_po(fin)
    line = po["items"][0]["id"]
    below = amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "59.999"}])
    assert below.status_code == 409 and below.json()["detail"]["code"] == "below_received"
    assert amend(fin["buyer"], po, [{"po_item_id": line}]).status_code == 422                                                   # no change
    assert amend(fin["buyer"], po, [{"po_item_id": 999999, "new_qty": "5"}]).status_code == 422
    dn = amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "60"}]).json()                                                # shrink to exactly what arrived
    assert fin["admin"].post(f"/api/procurement/po-amendments/{dn['id']}/approve", json={}).status_code == 200
    assert db.scalar(text("SELECT status FROM purchase_orders WHERE id=:p"), {"p": po["id"]}) == "RECEIVED"                     # nothing left to receive
    assert amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "80"}]).status_code == 409                                  # RECEIVED is no longer amendable
    assert_integrity(db)


def test_price_locked_once_invoiced_and_draft_pos_use_normal_edit(fin, db):
    m, po = partial_po(fin, recv="60")
    line = po["items"][0]["id"]
    r = fin["acct"].post("/api/accounts/supplier-invoices", headers=k("si"), json={"po_id": po["id"], "supplier_invoice_no": f"SI-{uuid.uuid4().hex[:6]}", "invoice_date": str(business_today()),
                                                                                  "lines": [{"po_item_id": line, "qty": "60", "unit_price": "10", "tax_rate": "18"}]})
    assert r.status_code == 201, r.text
    locked = amend(fin["buyer"], po, [{"po_item_id": line, "new_unit_price": "9"}])
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "price_locked_by_invoice"
    qty_only = amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "120"}])                                                # quantity may still change
    assert qty_only.status_code == 201
    draft = fin["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "5", "unit_price": "1"}]}).json()
    d = amend(fin["buyer"], draft, [{"po_item_id": draft["items"][0]["id"], "new_qty": "6"}])
    assert d.status_code == 409 and d.json()["detail"]["code"] == "po_not_amendable"


def test_amendment_permissions(fin, db, login, anon):
    m = mk_master("AMP")
    po = po_to_approved(fin["buyer"], fin["admin"], m)
    line = po["items"][0]["id"]
    assert amend(login("qc_inspector"), po, [{"po_item_id": line, "new_qty": "5"}]).status_code == 403
    assert amend(anon, po, [{"po_item_id": line, "new_qty": "5"}]).status_code == 401
    am = amend(fin["buyer"], po, [{"po_item_id": line, "new_qty": "5"}]).json()
    assert login("stores_manager").post(f"/api/procurement/po-amendments/{am['id']}/approve", json={}).status_code == 403
    assert fin["admin"].post("/api/procurement/po-amendments/999999/approve", json={}).status_code == 404


# ---------------------------------------------------------------- concurrency (live 2-process server, real commits)
def test_concurrent_returns_never_exceed_received_quantity(live_users, committed):
    from concurrent.futures import ThreadPoolExecutor
    buyer, stores, admin = live_users("purchase_manager")[0], live_users("stores_manager")[0], live_users("system_admin")[0]
    m = mk_master("RCC")
    po = buyer.post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100", "unit_price": "10"}]}).json()
    po = buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}).json()
    po = admin.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}).json()
    grr = stores.post("/api/procurement/goods-receipts", key=f"gr-{uuid.uuid4().hex[:10]}", json={
        "po_id": po["id"], "warehouse_id": m["wh1"], "delivery_note_no": f"DN-{uuid.uuid4().hex[:6]}",
        "lines": [{"po_item_id": po["items"][0]["id"], "qty": "100", "supplier_lot_no": "L1", "expiry_date": str(business_today().replace(year=business_today().year + 1))}]})
    assert grr.status_code == 201, grr.text
    gr_id = grr.json()["id"]
    with committed() as s:
        line = s.scalar(text("SELECT id FROM goods_receipt_items WHERE gr_id=:g"), {"g": gr_id})

    def attempt(i):
        return stores.post(f"/api/procurement/goods-receipts/{gr_id}/returns", key=f"c-{i}-{uuid.uuid4().hex[:8]}",
                           json={"reason_type": "DAMAGED", "reason": "concurrent test", "lines": [{"gr_item_id": line, "qty": "30"}]}).status_code

    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(attempt, range(8)))
    assert codes.count(201) == 3, codes                                  # 3 x 30 = 90 <= 100; a 4th would need 120
    assert all(c in (201, 409, 503) for c in codes), codes
    with committed() as s:
        returned = s.scalar(text("SELECT COALESCE(sum(qty),0) FROM purchase_return_lines WHERE gr_item_id=:l"), {"l": line})
        phys = s.scalar(text("SELECT sum(qty_physical) FROM stock_balances WHERE item_id=:i"), {"i": m["item_id"]})
        recv = s.scalar(text("SELECT qty_received FROM purchase_order_items WHERE po_id=:p"), {"p": po["id"]})
        rep = __import__("scripts.check_integrity", fromlist=["run_checks"]).run_checks(s.connection())
    assert returned == N("90") and phys == N("10") and recv == N("10")
    assert not [c for c in rep["checks"] if not c["ok"]]
