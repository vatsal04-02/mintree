"""List endpoints for documents (receipts, BOMs, invoices, payments, returns, shipments): paginated, newest first, permission-gated."""
import pytest

from tests.test_dispatch import shop  # noqa: F401
from tests.test_production import plant  # noqa: F401

PATHS = [("/api/procurement/goods-receipts", "production_operator"), ("/api/production/boms", "sales_operator"), ("/api/accounts/supplier-invoices", "sales_operator"),
         ("/api/accounts/payments", "production_operator"), ("/api/dispatch/returns", "production_operator"), ("/api/dispatch/shipments", "production_operator"),
         ("/api/dispatch/invoices", "production_operator"),
         ("/api/accounts/bank-transactions", "production_operator"), ("/api/ecommerce/settlements", "production_operator"), ("/api/rnd/versions", "sales_operator"),
         ("/api/rnd/trials", "sales_operator"), ("/api/quality/capa", "sales_operator"), ("/api/sales/contacts", "production_operator"), ("/api/inventory/stocktakes", "production_operator")]


@pytest.mark.parametrize("path,outsider", PATHS)
def test_listing_is_paginated_and_gated(path, outsider, admin, login):
    r = admin.get(path, params={"page": 1, "page_size": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["items"], list) and len(body["items"]) <= 5 and body["page"] == 1
    ids = [i["id"] for i in body["items"]]
    assert ids == sorted(ids, reverse=True), "newest first"
    assert login(outsider).get(path).status_code == 403
    assert admin.get(path, params={"page_size": 500}).status_code == 422


def test_shipment_lines_show_what_can_still_be_returned(shop):
    from tests.test_barcodes import order
    from tests.test_dispatch import advance, make_customer, make_fg
    from tests.test_production import k
    f = make_fg(shop, 50, 10)
    o = order(shop, f, make_customer(), qty="10")
    p = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]}).json()
    lines = shop["disp"].get(f"/api/dispatch/orders/{o['id']}/shipment-lines").json()
    assert len(lines) == 1 and lines[0]["returnable"] == "10.000" and lines[0]["batch_no"]
    shop["disp"].post(f"/api/dispatch/returns?order_id={o['id']}", headers=k(), json={"reason": "damaged", "lines": [{"shipment_line_id": lines[0]["shipment_line_id"], "qty": "3"}]})
    assert shop["disp"].get(f"/api/dispatch/orders/{o['id']}/shipment-lines").json()[0]["returnable"] == "7.000"


def test_list_screens_search_every_shown_column_and_stay_paginated(login):
    c = login("management")
    base = c.get("/api/dispatch/invoices", params={"page_size": 5}).json()
    assert isinstance(base["items"], list)
    if base["items"]:
        number = base["items"][0]["number"]
        hit = c.get("/api/dispatch/invoices", params={"q": number[-6:]}).json()["items"]
        assert hit and all(number[-6:].lower() in str(row).lower() for row in hit)
    assert c.get("/api/dispatch/invoices", params={"q": "zzz-no-such-thing"}).json()["items"] == []
    for evil in ("%", "_", "'; DROP TABLE users; --", "\\"):
        assert c.get("/api/dispatch/invoices", params={"q": evil}).status_code == 200            # wildcards are literal, SQL is parameterised
    assert c.get("/api/dispatch/invoices", params={"q": "x" * 200}).status_code == 422
    assert c.get("/api/procurement/goods-receipts", params={"q": "nothing-here"}).json()["items"] == []


def test_leads_and_orders_search_beyond_the_first_page(login, db):
    c = login("management")
    sales = login("sales_operator")
    for i in range(3):
        assert sales.post("/api/crm/leads", json={"name": f"Searchable Person {i}", "company": "Zzyzx Traders", "email": f"zz{i}@example.com"}).status_code == 201
    hit = c.get("/api/crm/leads", params={"q": "zzyzx"}).json()["items"]
    assert len(hit) == 3 and all("Zzyzx" in (x["company"] or "") for x in hit)
    assert c.get("/api/crm/leads", params={"q": "no-such-company-xyz"}).json()["items"] == []
    assert c.get("/api/crm/leads", params={"q": "%"}).json()["items"] == []                    # wildcards are literal
    first = c.get("/api/dispatch/orders", params={"page_size": 1}).json()["items"]
    if first:
        n = first[0]["number"]
        assert [o["number"] for o in c.get("/api/dispatch/orders", params={"q": n}).json()["items"]] == [n]
