"""Cross-module workflow: a marketplace order travels through every module (catalogue/stock from a real production chain ->
marketplace ingestion -> sales order -> allocation -> atomic dispatch + GST invoice + COGS -> courier -> return + credit note ->
settlement -> management summary). The integrity checker (all invariants, incl. balanced journals) is run after EVERY stage."""
import uuid
from datetime import datetime

from sqlalchemy import text

from tests.test_dispatch import advance, integrity, make_customer, make_fg, shop  # noqa: F401
from tests.test_inventory_procurement import N
from tests.test_production import k, plant  # noqa: F401


def test_marketplace_order_through_every_module(shop, login, db):
    f = make_fg(shop, 100, 10)                                          # procure -> QC -> BOM -> produce -> QC release (valued FG)
    integrity(db)
    mgr = login("dispatch_manager")
    acct = mgr.post("/api/ecommerce/accounts", json={"platform": "NYKAA", "name": "Nykaa", "seller_id": f"N{uuid.uuid4().hex[:6]}", "customer_id": make_customer(), "warehouse_id": f["wh"]}).json()
    mgr.put(f"/api/ecommerce/accounts/{acct['id']}/mappings", json={"platform_sku": "SERUM-30", "item_id": f["fg"]})
    res = mgr.post(f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [{"order_id": "NYK-1001", "order_date": str(datetime.now().date()),
            "customer_name": "R. Sharma", "shipping_address": "5 Park St, Kolkata", "items": [{"sku": "SERUM-30", "qty": "20", "unit_price": "25"}]}]}).json()
    assert res["created"] == 1
    integrity(db)
    so = db.execute(text("SELECT id, version FROM sales_orders WHERE external_ref = 'NYKAA:NYK-1001'")).mappings().one()
    o = shop["disp"].get(f"/api/dispatch/orders/{so['id']}").json()
    packed = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{so['id']}/dispatch", headers=k(), json={"expected_version": packed["version"]})
    assert d.status_code == 200, d.text
    body = d.json()
    assert N(body["invoice"]["total"]) == 590                           # 20 x 25 + 18 %
    integrity(db)
    sh = shop["disp"].get(f"/api/dispatch/shipments/{body['shipment_id']}").json()
    assert shop["disp"].post(f"/api/dispatch/shipments/{sh['id']}/book").json()["result"] == "booked"
    line = sh["lines"][0]
    r = shop["disp"].post(f"/api/dispatch/returns?order_id={so['id']}", headers=k(), json={"reason": "customer changed mind", "lines": [{"shipment_line_id": line["id"], "qty": "4"}]}).json()
    shop["disp"].post(f"/api/dispatch/returns/{r['id']}/receive", headers=k())
    assert shop["disp"].post(f"/api/dispatch/returns/{r['id']}/decide", headers=k(), json={"decision": "RESTOCK"}).status_code == 200
    integrity(db)
    # settlement for the marketplace: gross 500, commission 75 -> net 425
    s = mgr.post(f"/api/ecommerce/accounts/{acct['id']}/settlements", json={"reference": "NYK-W45", "period_start": str(datetime.now().date()), "period_end": str(datetime.now().date()),
                                                                          "gross_sales": "500", "commission": "75"}).json()
    assert N(s["net_settlement"]) == 425
    assert N(s["orders_value_in_period"]) == 500 and N(s["gross_vs_orders_difference"]) == 0, "settlement gross matches the ingested orders for the period"
    summary = login("management").get("/api/analytics/management-summary").json()
    assert N(summary["sales"]["credit_notes_ex_tax"]) >= 100              # 4 x 25 returned
    assert any(r_["status"] == "DISPATCHED" for r_ in summary["orders_by_status"])
    integrity(db)
