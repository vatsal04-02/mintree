"""Printable documents carry the authentic logo once, reconcile with the stored totals, and respect permissions."""
from datetime import timedelta

from app.core.brand import logo_drawing
from app.core.clock import business_today
from tests.conftest import mk_master
from tests.test_dispatch import advance, make_customer, make_fg, new_so, shop  # noqa: F401
from tests.test_inventory_procurement import po_to_approved
from tests.test_partial_dispatch import book, ship
from tests.test_production import k, plant  # noqa: F401
from tests.test_sales_dispatch_gaps import crm  # noqa: F401


def is_pdf(r):
    return r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"


def test_logo_is_the_supplied_vector_artwork_with_locked_proportions():
    full, mark = logo_drawing(9), logo_drawing(9, mark=True)
    assert len(full.contents) == 7                                           # all seven paths of the supplied SVG, nothing redrawn
    assert abs(full.width / full.height - 172 / 32) < 1e-6 and abs(mark.width / mark.height - 54 / 32) < 1e-6
    from app.core.brand import ASSET
    import filecmp, pathlib
    assert filecmp.cmp(ASSET, pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "brand-logo.svg", shallow=False), "the backend copy must equal the frontend asset"


def test_purchase_order_pdf_only_when_approved(login, admin):
    buyer = login("purchase_manager")
    m = mk_master("PDFPO")
    r = buyer.post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "10", "unit_price": "5"}]}).json()
    assert buyer.get(f"/api/procurement/purchase-orders/{r['id']}/pdf").status_code == 422             # a draft is not sent to a supplier
    po = po_to_approved(buyer, admin, m, qty="10", price="5")
    pdf = buyer.get(f"/api/procurement/purchase-orders/{po['id']}/pdf")
    assert is_pdf(pdf) and po["number"].replace("/", "-") in pdf.headers["content-disposition"]
    assert login("production_operator").get(f"/api/procurement/purchase-orders/{po['id']}/pdf").status_code == 403


def test_quotation_and_challan_pdfs(crm, db):
    f, cust = make_fg(crm, 100, 10), make_customer()
    t = business_today()
    assert crm["mgr"].post("/api/sales/prices", json={"customer_id": cust, "item_id": f["fg"], "price": "22.50", "valid_from": str(t - timedelta(days=1))}).status_code == 201
    q = crm["s1"].post("/api/sales/quotations", json={"customer_id": cust, "valid_until": str(t + timedelta(days=7)), "lines": [{"item_id": f["fg"], "qty": "10"}]}).json()
    assert is_pdf(crm["s1"].get(f"/api/sales/quotations/{q['id']}/pdf"))
    assert crm["s1"].get("/api/sales/quotations/999999999/pdf").status_code == 404
    o = new_so(crm, f, cust, qty="5", price="25")
    packed = advance(crm, o)
    d = ship(crm, o, version=packed["version"]).json()
    sh = book(crm, d["shipment_id"])
    assert is_pdf(crm["disp"].get(f"/api/dispatch/shipments/{d['shipment_id']}/challan"))
    assert crm["disp"].get("/api/dispatch/shipments/999999999/challan").status_code == 404
    inv = crm["disp"].get(f"/api/dispatch/invoices/{d['invoice']['id']}/pdf")
    assert is_pdf(inv)
