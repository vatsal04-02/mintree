"""PRD Module 07 gaps: debit notes, bank statements + reconciliation, ageing, statements, GST, P&L / balance sheet, costing, settlement posting."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import mk_master
from tests.test_accounts import fin, integrity, tb  # noqa: F401
from tests.test_dispatch import advance, make_customer, make_fg, shop  # noqa: F401
from tests.test_inventory_procurement import N, po_to_approved, receive
from tests.test_production import k, plant  # noqa: F401


def supplier_invoice(fin, qty="100", price="10", rate="18"):
    m = mk_master("ACX")
    po = po_to_approved(fin["buyer"], fin["admin"], m, qty=qty, price=price)
    assert receive(fin["stores"], po, m["wh1"], qty, f"DN-{uuid.uuid4().hex[:6]}", f"gr-{uuid.uuid4().hex[:10]}").status_code == 201
    r = fin["acct"].post("/api/accounts/supplier-invoices", headers=k("si"), json={"po_id": po["id"], "supplier_invoice_no": f"SI-{uuid.uuid4().hex[:6]}", "invoice_date": str(date.today()),
                                                                                  "lines": [{"po_item_id": po["items"][0]["id"], "qty": qty, "unit_price": price, "tax_rate": rate}]})
    assert r.status_code == 201, r.text
    return m, r.json()


def new_bank(fin):
    code = "11" + str(uuid.uuid4().int)[:5]
    assert fin["admin"].post("/api/accounts/chart", json={"code": code, "name": f"Test bank {code}", "type": "ASSET"}).status_code == 201
    r = fin["acct"].post("/api/accounts/bank-accounts", json={"name": f"HDFC {code}", "bank_name": "HDFC", "account_last4": "4321", "ledger_account_code": code})
    assert r.status_code == 201, r.text
    return r.json()


def test_debit_note_reduces_payable_and_is_idempotent(fin, db):
    m, inv = supplier_invoice(fin)                                        # 1000 + 180 = 1180
    before = tb(fin["acct"])
    body = {"amount_ex_tax": "100", "reason_type": "QUALITY_CLAIM", "reason": "Out-of-spec lot partially credited"}
    r = fin["acct"].post(f"/api/accounts/supplier-invoices/{inv['id']}/debit-notes", headers={"Idempotency-Key": "dn-key-0001"}, json=body)
    assert r.status_code == 201, r.text
    assert N(r.json()["total"]) == N("118.00") and r.json()["number"].startswith("DN/")
    replay = fin["acct"].post(f"/api/accounts/supplier-invoices/{inv['id']}/debit-notes", headers={"Idempotency-Key": "dn-key-0001"}, json=body)
    assert replay.json() == r.json() and replay.headers["idempotent-replayed"] == "true"
    after = tb(fin["acct"])
    dv = lambda c: after.get(c, 0) - before.get(c, 0)
    assert dv("2000") == N("118.00") and dv("5300") == N("-100.00") and dv("1500") == N("-18.00")
    big = fin["acct"].post(f"/api/accounts/supplier-invoices/{inv['id']}/debit-notes", headers=k("dn"), json={**body, "amount_ex_tax": "1000"})
    assert big.status_code == 409 and big.json()["detail"]["code"] == "exceeds_outstanding"
    # payment can only settle what is left (1180 - 118 = 1062)
    pay = lambda amt: fin["acct"].post("/api/accounts/payments", headers=k("p"), json={"direction": "PAID", "party_id": m["supplier_id"], "amount": amt, "payment_date": str(date.today()), "method": "BANK_TRANSFER",
                                                                                      "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": amt}]})
    assert pay("1180").status_code == 409
    assert pay("1062").status_code == 201
    integrity(db)


def test_bank_statement_import_matching_booking_and_reconciliation(fin, db):
    bank = new_bank(fin)
    m, inv = supplier_invoice(fin)
    p = fin["acct"].post("/api/accounts/payments", headers=k("p"), json={"direction": "PAID", "party_id": m["supplier_id"], "amount": "1180", "payment_date": str(date.today()), "method": "BANK_TRANSFER",
                                                                         "bank_account_code": bank["ledger_account_code"], "allocations": [{"invoice_id": inv["id"], "amount": "1180"}]})
    assert p.status_code == 201, p.text                                  # registered bank accounts are valid settlement accounts
    fin["acct"].post("/api/accounts/journals", headers=k("jv"), json={"entry_date": str(date.today()), "description": "Owner capital in", "lines": [
        {"account_code": bank["ledger_account_code"], "debit": "5000"}, {"account_code": "3000", "credit": "5000"}]})
    csv_ = f"date,description,reference,credit,debit\n{date.today()},NEFT supplier,{p.json()['number']},,1180\n{date.today()},Capital,,5000,\n{date.today()},SMS charges,,,23.60\n"
    up = lambda c: fin["acct"].post(f"/api/accounts/bank-accounts/{bank['id']}/statements", files={"file": ("s.csv", c, "text/csv")})
    r = up(csv_)
    assert r.json()["imported"] == 3 and up(csv_).json() == {**r.json(), "imported": 0, "duplicates": 3}, "re-importing the same file changes nothing"
    assert up("garbage,x\n1,2\n").status_code == 422
    sug = {s["amount"]: s for s in fin["acct"].get(f"/api/accounts/bank-accounts/{bank['id']}/suggestions").json()}
    assert N("-1180.00") in [N(a) for a in sug] and N("5000.00") in [N(a) for a in sug]
    assert N("-23.60") not in [N(a) for a in sug], "nothing in the books matches the SMS charge"
    txns = {N(t["amount"]): t for t in fin["acct"].get(f"/api/accounts/bank-accounts/{bank['id']}/transactions").json()["items"]}
    for amt in (N("-1180.00"), N("5000.00")):
        cand = next(s for s in sug.values() if N(s["amount"]) == amt)["candidates"][0]
        assert fin["acct"].post(f"/api/accounts/bank-transactions/{txns[amt]['id']}/match", json={"journal_line_id": cand["journal_line_id"]}).json()["status"] == "MATCHED"
    wrong = fin["acct"].post(f"/api/accounts/bank-transactions/{txns[N('-23.60')]['id']}/match", json={"journal_line_id": next(iter(sug.values()))["candidates"][0]["journal_line_id"]})
    assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "amount_mismatch"
    rec = fin["acct"].get(f"/api/accounts/bank-accounts/{bank['id']}/reconciliation").json()
    assert rec["reconciled"] is False and rec["statement_lines_not_in_books"]["count"] == 1 and N(rec["statement_lines_not_in_books"]["net"]) == N("-23.60") and N(rec["unexplained_difference"]) == 0
    booked = fin["acct"].post(f"/api/accounts/bank-transactions/{txns[N('-23.60')]['id']}/book", json={"counter_account_code": "5400", "narration": "SMS alert charges"})
    assert booked.status_code == 200 and booked.json()["status"] == "MATCHED"
    rec = fin["acct"].get(f"/api/accounts/bank-accounts/{bank['id']}/reconciliation").json()
    assert rec["reconciled"] is True and N(rec["ledger_balance"]) == N(rec["statement_balance"]) == N("3796.40")
    assert fin["acct"].post(f"/api/accounts/bank-transactions/{txns[N('5000.00')]['id']}/unmatch").json()["status"] == "UNMATCHED"
    assert fin["acct"].get(f"/api/accounts/bank-accounts/{bank['id']}/reconciliation").json()["reconciled"] is False
    assert fin["acct"].post("/api/accounts/bank-accounts", json={"name": "dup", "ledger_account_code": bank["ledger_account_code"]}).status_code == 409
    assert fin["buyer"].get("/api/accounts/bank-accounts").status_code == 403
    integrity(db)


def test_ageing_statement_pnl_and_balance_sheet(fin, db):
    m, inv = supplier_invoice(fin)
    db.execute(text("UPDATE suppliers SET payment_terms_days = 30 WHERE id = :i"), {"i": m["supplier_id"]})
    db.execute(text("UPDATE supplier_invoices SET invoice_date = current_date - 50 WHERE id = :i"), {"i": inv["id"]})
    ag = fin["acct"].get("/api/accounts/reports/ageing/payables").json()
    row = next(p for p in ag["parties"] if p["party_id"] == m["supplier_id"])
    assert row["invoices"][0]["days_overdue"] == 20 and row["buckets"]["1-30"] == "1180.00" and row["total"] == "1180.00"
    assert fin["acct"].get("/api/accounts/reports/ageing/receivables").status_code == 200
    st = fin["acct"].get(f"/api/accounts/reports/statement/supplier/{m['supplier_id']}", params={"start": str(date.today() - timedelta(days=60)), "end": str(date.today())}).json()
    assert st["lines"][0]["type"] == "INVOICE" and N(st["closing_balance"]) == N("1180.00")
    assert fin["acct"].get("/api/accounts/reports/statement/customer/99999999", params={"start": "2026-01-01", "end": "2026-12-31"}).status_code == 404
    pl = fin["acct"].get("/api/accounts/reports/profit-and-loss", params={"start": str(date.today() - timedelta(days=400)), "end": str(date.today())}).json()
    assert N(pl["net_profit"]) == N(pl["total_revenue"]) - N(pl["total_expenses"])
    bs = fin["acct"].get("/api/accounts/reports/balance-sheet").json()
    assert bs["balanced"] is True and N(bs["total_assets"]) == N(bs["total_liabilities"]) + N(bs["total_equity"])
    assert fin["buyer"].get("/api/accounts/reports/balance-sheet").status_code == 403


def run_dispatch(shop, f, cust, qty="20", price="25"):
    o = shop["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": "B2B", "lines": [{"item_id": f["fg"], "qty": qty, "unit_price": price}]}).json()
    p = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]})
    assert d.status_code == 200, d.text
    return d.json()


def test_gst_summary_costing_and_margin(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    today = str(date.today())
    before = shop["acct"].get("/api/accounts/reports/gst-summary", params={"start": today, "end": today}).json()
    run_dispatch(shop, f, cust)                                        # 20 x 25 = 500 taxable, 18% = 90 (customer state MH = company state 27 -> CGST+SGST)
    after = shop["acct"].get("/api/accounts/reports/gst-summary", params={"start": today, "end": today}).json()
    rate = lambda r, field: N(r["outward_supplies_by_rate"].get("18", {}).get(field, 0))
    d = lambda field: rate(after, field) - rate(before, field)
    assert d("taxable") == 500 and d("cgst") == 45 and d("sgst") == 45 and d("igst") == 0
    assert N(after["gstr3b"]["output_tax"]) - N(before["gstr3b"]["output_tax"]) == 90
    # inter-state customer -> IGST
    other = make_customer()
    db.execute(text("UPDATE customers SET state = 'Karnataka' WHERE id = :i"), {"i": other})
    f2 = make_fg(shop, 50, 10)
    run_dispatch(shop, f2, other, qty="10", price="30")                  # 300 taxable, 54 IGST
    final = shop["acct"].get("/api/accounts/reports/gst-summary", params={"start": today, "end": today}).json()
    assert rate(final, "igst") - rate(after, "igst") == 54
    assert any(h["rate"] == "18" for h in final["hsn_summary"])
    # costing: revenue 500, cogs 200 for the first product
    pc = shop["acct"].get("/api/accounts/reports/product-costing", params={"start": today, "end": today}).json()
    row = next(p for p in pc["products"] if p["item_id"] == f["fg"])
    assert N(row["revenue_ex_tax"]) == 500 and N(row["cogs"]) == 200 and N(row["gross_margin"]) == 300 and N(row["gross_margin_pct"]) == 60
    assert N(row["last_production_unit_cost"]) == 10
    assert shop["stores"].get("/api/accounts/reports/gst-summary", params={"start": today, "end": today}).status_code == 403


def test_marketplace_settlement_posts_cash_and_deductions_and_clears_invoice(shop, login, db):
    f = make_fg(shop, 100, 10)
    mgr = login("dispatch_manager")
    cust = make_customer()
    acct = mgr.post("/api/ecommerce/accounts", json={"platform": "AMAZON", "name": "Amazon", "seller_id": f"S{uuid.uuid4().hex[:6]}", "customer_id": cust, "warehouse_id": f["wh"]}).json()
    mgr.put(f"/api/ecommerce/accounts/{acct['id']}/mappings", json={"platform_sku": "SRM", "item_id": f["fg"]})
    oid = f"AMZ-{uuid.uuid4().hex[:6]}"
    assert mgr.post(f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [{"order_id": oid, "order_date": str(date.today()), "items": [{"sku": "SRM", "qty": "20", "unit_price": "25"}]}]}).json()["created"] == 1
    so = db.execute(text("SELECT id FROM sales_orders WHERE external_ref = :r"), {"r": f"AMAZON:{oid}"}).scalar()
    o = shop["disp"].get(f"/api/dispatch/orders/{so}").json()
    p = advance(shop, o)
    inv = shop["disp"].post(f"/api/dispatch/orders/{so}/dispatch", headers=k(), json={"expected_version": p["version"]}).json()["invoice"]
    assert N(inv["total"]) == 590
    st = mgr.post(f"/api/ecommerce/accounts/{acct['id']}/settlements", json={"reference": f"W-{uuid.uuid4().hex[:5]}", "period_start": str(date.today()), "period_end": str(date.today()),
                                                                           "gross_sales": "590", "commission": "88.50"}).json()
    assert N(st["net_settlement"]) == N("501.50")
    post = lambda: shop["acct"].post(f"/api/accounts/settlements/{st['id']}/post", headers=k("st"), json={"bank_account_code": "1100"})
    assert post().status_code == 409, "cannot post before the receipt is recorded"
    mgr.post(f"/api/ecommerce/settlements/{st['id']}/received", json={"received_amount": "501.50"})
    before = tb(shop["acct"])
    r = post()
    assert r.status_code == 200, r.text
    assert N(r.json()["cash_received"]) == N("501.50") and N(r.json()["deductions_expensed"]) == N("88.50")
    after = tb(shop["acct"])
    dv = lambda c: after.get(c, 0) - before.get(c, 0)
    assert dv("5500") == N("88.50") and dv("1100") == N("501.50") and dv("1200") == N("-590.00")
    assert shop["disp"].get(f"/api/dispatch/invoices/{inv['id']}").json()["status"] == "PAID"
    assert shop["acct"].post(f"/api/accounts/settlements/{st['id']}/post", headers=k("st2"), json={"bank_account_code": "1100"}).status_code == 409, "posted once"
    integrity(db)
