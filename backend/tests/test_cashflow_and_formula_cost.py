"""Cash-flow statement (direct method) reconciles with the ledger; formula costing uses real stock/purchase cost and never guesses."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from app.core.clock import business_today
from tests.conftest import mk_master
from tests.test_inventory_procurement import N, po_to_approved, receive


def test_cash_flow_reconciles_and_classifies_by_counter_account(login, admin, db):
    acct, buyer, stores = login("accounts_manager"), login("purchase_manager"), login("stores_manager")
    start = business_today() - timedelta(days=1)
    before = acct.get("/api/accounts/reports/cash-flow", params={"start": str(start), "end": str(business_today())}).json()
    m = mk_master("CF")
    po = po_to_approved(buyer, admin, m, qty="10", price="100")
    assert receive(stores, po, m["wh1"], "10", "DN-CF", "key-cf-gr-0001").status_code == 201
    inv = acct.post("/api/accounts/supplier-invoices", headers={"Idempotency-Key": "key-cf-si-0001"}, json={"po_id": po["id"], "supplier_invoice_no": "CF-1", "invoice_date": str(business_today()),
                                                                                                          "lines": [{"po_item_id": po["items"][0]["id"], "qty": "10", "unit_price": "100", "tax_rate": "18"}]}).json()
    pay = acct.post("/api/accounts/payments", headers={"Idempotency-Key": "key-cf-pay-0001"}, json={"direction": "PAID", "party_id": m["supplier_id"], "amount": str(inv["total"]), "payment_date": str(business_today()),
                                                                                                     "method": "BANK_TRANSFER", "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": str(inv["total"])}]})
    assert pay.status_code == 201, pay.text
    after = acct.get("/api/accounts/reports/cash-flow", params={"start": str(start), "end": str(business_today())})
    assert after.status_code == 200
    r = after.json()
    assert r["reconciles"] is True and r["method"] == "direct" and "fixed-asset" in r["investing_note"]
    paid = next(x for x in r["operating"] if x["label"] == "Payments to suppliers")
    paid0 = next((x for x in before["operating"] if x["label"] == "Payments to suppliers"), {"outflow": "0"})
    assert N(paid["outflow"]) - N(paid0["outflow"]) == N(inv["total"])                       # the payment shows as an outflow of exactly the invoice total
    # the statement agrees with the trial balance on the bank + cash accounts
    tb = {a["code"]: N(a["balance"]) for a in acct.get("/api/accounts/reports/trial-balance").json()["accounts"]}
    assert N(r["closing_cash"]) == tb.get("1000", 0) + tb.get("1100", 0)
    assert acct.get("/api/accounts/reports/cash-flow", params={"start": str(business_today()), "end": str(start)}).status_code in (404, 422)
    assert login("production_operator").get("/api/accounts/reports/cash-flow", params={"start": str(start), "end": str(business_today())}).status_code == 403


def test_formula_cost_uses_stock_cost_then_last_po_price_and_flags_unpriced(login, admin, db):
    rnd, buyer, stores = login("rnd_manager"), login("purchase_manager"), login("stores_manager")
    priced, from_po, unpriced = mk_master("FCA"), mk_master("FCB"), mk_master("FCC")
    po = po_to_approved(buyer, admin, priced, qty="50", price="40")
    assert receive(stores, po, priced["wh1"], "50", "DN-FC1", "key-fc-gr-0001").status_code == 201          # stock value 50 x 40 -> average cost 40
    po_to_approved(buyer, admin, from_po, qty="5", price="90")                                               # ordered, nothing received: last purchase price 90
    uom = admin.get("/api/masters/units-of-measure", params={"page_size": 50}).json()["items"][0]["id"]
    prod = admin.post("/api/masters/items", json={"item_code": f"FCP-{priced['item_id']}", "name": "Costed product", "kind": "FG", "uom_id": uom, "gst_rate": "18"})
    assert prod.status_code == 201, prod.text
    f = rnd.post("/api/rnd/formulations", json={"code": f"FC-{priced['item_id']}", "name": "Cost formula", "product_item_id": prod.json()["id"], "batch_size": "10", "ingredients": [
        {"item_id": priced["item_id"], "qty": "6"}, {"item_id": from_po["item_id"], "qty": "3"}, {"item_id": unpriced["item_id"], "qty": "1"}]})
    assert f.status_code == 201, f.text
    vid = f.json()["versions"][-1]["id"] if "versions" in f.json() else f.json()["id"]
    r = rnd.get(f"/api/rnd/formulation-versions/{vid}/cost")
    assert r.status_code == 200, r.text
    c = r.json()
    by = {x["item_code"].split("-")[0]: x for x in c["lines"]}
    assert by["FCA"]["cost_source"] == "average stock cost" and by["FCA"]["amount"] == "240.00"       # 6 x 40
    assert by["FCB"]["cost_source"] == "last purchase price" and by["FCB"]["amount"] == "270.00"      # 3 x 90
    assert by["FCC"]["cost_source"] == "unpriced" and by["FCC"]["amount"] is None
    assert c["complete"] is False and c["cost_per_unit"] is None and c["material_cost"] == "510.00"    # never guessed, never silently zero
