"""List endpoints for documents (receipts, BOMs, invoices, payments, returns, shipments): paginated, newest first, permission-gated."""
import pytest

PATHS = [("/api/procurement/goods-receipts", "production_operator"), ("/api/production/boms", "sales_operator"), ("/api/accounts/supplier-invoices", "sales_operator"),
         ("/api/accounts/payments", "production_operator"), ("/api/dispatch/returns", "production_operator"), ("/api/dispatch/shipments", "production_operator"),
         ("/api/dispatch/invoices", "production_operator")]


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
