"""Barcode support: resolver, label PDFs, retail barcode on items, pick verification by scan, manifest handover by scan."""
import uuid

import pytest
from sqlalchemy import text

from tests.test_dispatch import advance, make_customer, make_fg, shop  # noqa: F401
from tests.test_inventory_procurement import N
from tests.test_production import k, plant  # noqa: F401


def order(shop, f, cust, qty="20"):
    return shop["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": "B2B", "lines": [{"item_id": f["fg"], "qty": qty, "unit_price": "25"}]}).json()


def test_resolver_reads_mintree_labels_and_business_codes_and_respects_permissions(shop, db, login):
    f = make_fg(shop, 50, 10)
    batch_no = db.execute(text("SELECT batch_no FROM inventory_batches WHERE id = :b"), {"b": f["batch"]}).scalar()
    item_code = db.execute(text("SELECT item_code FROM items WHERE id = :i"), {"i": f["fg"]}).scalar()
    r = shop["stores"].get("/api/scan/resolve", params={"code": f"MT:BATCH:{f['batch']}"}).json()
    assert r["format"] == "mintree" and r["matches"][0]["type"] == "BATCH" and r["matches"][0]["id"] == f["batch"] and "?open=" in r["matches"][0]["link"]
    by_no = shop["stores"].get("/api/scan/resolve", params={"code": batch_no}).json()
    assert any(m["type"] == "BATCH" and m["id"] == f["batch"] for m in by_no["matches"]) and by_no["format"] == "business-code"
    assert any(m["type"] == "ITEM" for m in shop["stores"].get("/api/scan/resolve", params={"code": item_code}).json()["matches"])
    o = order(shop, f, make_customer())
    assert shop["disp"].get("/api/scan/resolve", params={"code": o["number"]}).json()["matches"][0]["type"] == "SO"
    # a user without dispatch access learns nothing about the order
    assert login("production_operator").get("/api/scan/resolve", params={"code": o["number"]}).json()["matches"] == []
    assert shop["stores"].get("/api/scan/resolve", params={"code": "MT:BATCH:999999999"}).json()["matches"] == []
    assert shop["stores"].get("/api/scan/resolve", params={"code": "NO-SUCH-CODE"}).json()["matches"] == []


def test_labels_are_pdfs_for_each_kind_and_gated(shop, db, login):
    f = make_fg(shop, 30, 10)
    eqp = login("maintenance_manager").post("/api/maintenance/equipment", json={"asset_id": f"EQ-{uuid.uuid4().hex[:5]}", "name": "Mixer"}).json()
    o = order(shop, f, make_customer())
    for kind, id_ in (("batch", f["batch"]), ("item", f["fg"]), ("equipment", eqp["id"]), ("order", o["id"])):
        r = (login("maintenance_manager") if kind == "equipment" else shop["disp"] if kind == "order" else shop["stores"]).get(f"/api/labels/{kind}/{id_}", params={"copies": 3})
        assert r.status_code == 200 and r.content[:5] == b"%PDF-", kind
    pdf = shop["stores"].get(f"/api/labels/batch/{f['batch']}", params={"copies": 3}).content
    assert pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 3 or pdf.count(b"/Page") >= 3
    assert shop["stores"].get("/api/labels/batch/999999999").status_code == 404
    assert login("production_operator").get(f"/api/labels/batch/{f['batch']}").status_code == 403
    assert shop["stores"].get(f"/api/labels/batch/{f['batch']}", params={"copies": 500}).status_code == 422


def test_item_retail_barcode_is_unique_validated_and_scannable(shop, admin, db):
    f = make_fg(shop, 10, 10)
    code = "89012345" + uuid.uuid4().hex[:5]
    r = admin.patch(f"/api/masters/items/{f['fg']}", json={"barcode": code})
    assert r.status_code == 200 and r.json()["barcode"] == code
    other = make_fg(shop, 10, 10)
    assert admin.patch(f"/api/masters/items/{other['fg']}", json={"barcode": code}).status_code == 409, "one retail barcode identifies one item"
    assert admin.patch(f"/api/masters/items/{other['fg']}", json={"barcode": "!!"}).status_code == 422
    m = shop["stores"].get("/api/scan/resolve", params={"code": code}).json()["matches"]
    assert [x["id"] for x in m if x["type"] == "ITEM"] == [f["fg"]]


def test_pick_scan_verifies_batch_and_quantity_and_can_gate_packing(shop, admin, db):
    f = make_fg(shop, 100, 10)
    o = order(shop, f, make_customer(), qty="10")
    alloc = advance(shop, o, "ALLOCATED")
    base = f"/api/dispatch/orders/{o['id']}"
    st = shop["disp"].get(f"{base}/scan-status").json()
    assert st["complete"] is False and N(st["lines"][0]["required"]) == 10
    assert shop["disp"].post(f"{base}/scan", json={"code": "MT:BATCH:999999999"}).json()["detail"]["code"] == "not_a_batch"
    other = make_fg(shop, 10, 10)
    wrong = shop["disp"].post(f"{base}/scan", json={"code": f"MT:BATCH:{other['batch']}"})
    assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "wrong_batch", "a batch that is not allocated to this order is a mis-pick"
    # packing is gated only when the company setting is on
    assert admin.put("/api/admin/settings/dispatch.require_pick_scan", json={"value": True}).status_code == 200
    blocked = shop["disp"].post(f"{base}/pack", json={"expected_version": alloc["version"], "cartons": 1, "weight_kg": "2"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "pick_scan_incomplete"
    ok = shop["disp"].post(f"{base}/scan", json={"code": f"MT:BATCH:{f['batch']}", "qty": "6"}).json()
    assert N(ok["lines"][0]["picked"]) == 6 and ok["complete"] is False
    over = shop["disp"].post(f"{base}/scan", json={"code": f"MT:BATCH:{f['batch']}", "qty": "5"})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "over_pick" and N(over.json()["detail"]["remaining"]) == 4
    done = shop["disp"].post(f"{base}/scan", json={"code": f"MT:BATCH:{f['batch']}", "qty": "4"}).json()
    assert done["complete"] is True
    assert shop["disp"].post(f"{base}/pack", json={"expected_version": alloc["version"], "cartons": 1, "weight_kg": "2"}).status_code == 200
    assert shop["stores"].post(f"{base}/scan", json={"code": f"MT:BATCH:{f['batch']}"}).status_code == 403
    admin.put("/api/admin/settings/dispatch.require_pick_scan", json={"value": False})
    assert admin.put("/api/admin/settings/dispatch.require_pick_scan", json={"value": "yes"}).status_code == 422


def test_manifest_handover_scan_is_idempotent_and_rejects_foreign_shipments(shop, db):
    f = make_fg(shop, 100, 10)
    ships = []
    for _ in range(2):
        o = order(shop, f, make_customer(), qty="5")
        p = advance(shop, o)
        d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": p["version"]}).json()
        assert shop["disp"].post(f"/api/dispatch/shipments/{d['shipment_id']}/book").json()["result"] == "booked"
        ships.append(d["shipment_id"])
    mf = shop["disp"].post("/api/dispatch/manifests", json={"courier": "fake"}).json()["manifest_no"]
    awbs = {s: shop["disp"].get(f"/api/dispatch/shipments/{s}").json()["awb"] for s in ships}
    scan = lambda code: shop["disp"].post(f"/api/dispatch/manifests/{mf}/scan", json={"code": code})
    a = scan(awbs[ships[0]]).json()
    assert a["handed_over"] >= 1 and a["already_scanned"] is False
    assert scan(f"MT:SHIP:{ships[0]}").json()["already_scanned"] is True, "scanning the same parcel twice is harmless"
    assert scan("UNKNOWN-AWB").status_code == 409
    r = scan(awbs[ships[1]]).json()
    assert r["handed_over"] == r["total"] and r["complete"] is True
    assert shop["disp"].get(f"/api/dispatch/manifests/{mf}").json()["handed_over"] == r["total"]


# ------------------------------------------------------------------ opening stock (go-live load)
def test_opening_stock_posts_batches_valuation_and_equity_and_guards_release(plant, admin, db):
    from tests.conftest import mk_master
    from tests.test_accounts import tb
    m = mk_master("OPN")
    body = lambda qc="QUARANTINE": {"warehouse_id": m["wh1"], "qc_status": qc, "note": "go-live load", "lines": [{"item_id": m["item_id"], "qty": "100", "unit_cost": "12.5", "supplier_lot_no": "LOT-OLD-1"}]}
    before = tb(admin)
    q = plant["stores"].post("/api/inventory/opening-stock", headers=k("os"), json=body())
    assert q.status_code == 201, q.text
    assert q.json()["number"].startswith("OS/") and N(q.json()["total_value"]) == N("1250.00") and q.json()["lines"][0]["batch_id"]
    av = plant["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]
    assert (N(av["physical"]), N(av["available"])) == (100, 0), "quarantined until QC releases it"
    after = tb(admin)
    dv = lambda c: after.get(c, 0) - before.get(c, 0)
    assert dv("1300") == N("1250.00") and dv("3000") == N("-1250.00")
    m2 = mk_master("OPN")
    denied = plant["stores"].post("/api/inventory/opening-stock", headers=k("os"), json={**body("RELEASED"), "lines": [{"item_id": m2["item_id"], "qty": "5", "unit_cost": "1"}], "warehouse_id": m2["wh1"]})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "release_not_permitted"
    ok = admin.post("/api/inventory/opening-stock", headers=k("os"), json={**body("RELEASED"), "lines": [{"item_id": m2["item_id"], "qty": "40", "unit_cost": "3"}], "warehouse_id": m2["wh1"]})
    assert ok.status_code == 201, ok.text
    assert N(plant["stores"].get("/api/inventory/availability", params={"item_id": m2["item_id"]}).json()["total"]["available"]) == 40
    dup = plant["stores"].post("/api/inventory/opening-stock", headers=k("os"), json={**body(), "lines": body()["lines"] * 2})
    assert dup.status_code == 422
    key = {"Idempotency-Key": "os-replay-key-0001"}
    m3 = mk_master("OPN")
    b3 = {**body(), "warehouse_id": m3["wh1"], "lines": [{"item_id": m3["item_id"], "qty": "7", "unit_cost": "2"}]}
    a = plant["stores"].post("/api/inventory/opening-stock", headers=key, json=b3)
    b = plant["stores"].post("/api/inventory/opening-stock", headers=key, json=b3)
    assert a.json() == b.json() and b.headers["idempotent-replayed"] == "true"
    from tests.test_dispatch import integrity
    integrity(db)
