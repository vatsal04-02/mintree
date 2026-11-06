import csv
import io


def uom(admin, code="KG"):
    items = admin.get("/api/masters/units-of-measure", params={"q": code}).json()["items"]
    return next(u["id"] for u in items if u["code"] == code)


ITEM = {"item_code": "rm-001", "name": "Glycerin", "kind": "RM"}


def test_item_crud_normalises_and_audits(admin):
    r = admin.post("/api/masters/items", json={**ITEM, "uom_id": uom(admin), "hsn_code": "29054500", "gst_rate": "18",
                                                "shelf_life_days": 730, "reorder_level": "200.5"})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["item_code"] == "RM-001" and item["uom_code"] == "KG"
    r = admin.patch(f"/api/masters/items/{item['id']}", json={"reorder_level": "300", "name": "Glycerin USP"})
    assert r.json()["name"] == "Glycerin USP"
    logs = admin.get("/api/admin/audit-logs", params={"entity": "item", "entity_id": item["id"]}).json()["items"]
    assert [l["action"] for l in logs] == ["update", "create"]
    assert logs[0]["before"]["name"] == "Glycerin" and logs[0]["after"]["name"] == "Glycerin USP"
    assert set(logs[0]["after"]) == {"name", "reorder_level"}  # diff only


def test_duplicate_codes_conflict(admin):
    body = {**ITEM, "uom_id": uom(admin)}
    assert admin.post("/api/masters/items", json=body).status_code == 201
    assert admin.post("/api/masters/items", json={**body, "item_code": "RM-001"}).status_code == 409


def test_database_rules_surface_as_validation_errors(admin):
    base = {**ITEM, "uom_id": uom(admin)}
    assert admin.post("/api/masters/items", json={**base, "shelf_life_days": 0}).status_code == 422
    assert admin.post("/api/masters/items", json={**base, "reorder_level": "-1"}).status_code == 422
    assert admin.post("/api/masters/items", json={**base, "kind": "WIDGET"}).status_code == 422
    assert admin.post("/api/masters/items", json={**base, "uom_id": 999999}).status_code == 422  # FK violation, not 500


def test_tax_identifier_validation(admin):
    ok = admin.post("/api/masters/suppliers", json={"code": "S1", "name": "A", "gstin": "27aapfu0939f1zv", "pan": "aapfu0939f"})
    assert ok.status_code == 201 and ok.json()["gstin"] == "27AAPFU0939F1ZV"
    assert admin.post("/api/masters/suppliers", json={"code": "S2", "name": "B", "gstin": "NOTAGSTIN"}).status_code == 422
    assert admin.post("/api/masters/suppliers", json={"code": "S3", "name": "C", "pan": "12345"}).status_code == 422
    assert admin.post("/api/masters/suppliers", json={"code": "S4", "name": "D", "gstin": ""}).status_code == 201  # blank -> null


def test_delete_deactivates_and_never_removes(admin):
    r = admin.post("/api/masters/brands", json={"code": "MINTREE", "name": "Mintree"}).json()
    assert admin.delete(f"/api/masters/brands/{r['id']}").status_code == 204
    assert admin.get("/api/masters/brands").json()["total"] == 0
    assert admin.get("/api/masters/brands", params={"is_active": "false"}).json()["total"] == 1
    assert admin.get(f"/api/masters/brands/{r['id']}").json()["is_active"] is False
    assert admin.patch(f"/api/masters/brands/{r['id']}", json={"is_active": True}).json()["is_active"] is True
    actions = [l["action"] for l in admin.get("/api/admin/audit-logs", params={"entity": "brand"}).json()["items"]]
    assert actions == ["update", "deactivate", "create"]


def test_list_search_filter_sort_paginate(admin):
    u = uom(admin)
    for i, (kind, name) in enumerate([("RM", "Niacinamide"), ("RM", "Hyaluronic acid"), ("PM", "Pump bottle 50ml"),
                                      ("PM", "Carton"), ("FG", "Vitamin C serum")]):
        assert admin.post("/api/masters/items", json={"item_code": f"X{i}", "name": name, "kind": kind, "uom_id": u}).status_code == 201
    g = lambda **p: admin.get("/api/masters/items", params=p).json()
    assert g()["total"] == 5
    assert g(kind="PM")["total"] == 2
    assert [i["name"] for i in g(q="acid")["items"]] == ["Hyaluronic acid"]
    assert [i["item_code"] for i in g(sort="-item_code")["items"]][:2] == ["X4", "X3"]
    page2 = g(page=2, page_size=2, sort="item_code")
    assert page2["total"] == 5 and [i["item_code"] for i in page2["items"]] == ["X2", "X3"]
    assert g(sort="nonexistent; drop table")["total"] == 5  # unknown sort column falls back safely


def test_csv_export_requires_permission_and_neutralises_formulas(admin, login):
    admin.post("/api/masters/suppliers", json={"code": "S9", "name": "=HYPERLINK(\"http://evil\")"})
    assert login("stores_manager").get("/api/masters/suppliers/export.csv").status_code == 403
    r = admin.get("/api/masters/suppliers/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert rows[0]["name"].startswith("'=")
    assert admin.get("/api/admin/audit-logs", params={"action": "export"}).json()["total"] == 1


def test_storage_locations_belong_to_a_warehouse(admin):
    wh = admin.get("/api/masters/warehouses", params={"q": "RM-WH"}).json()["items"][0]
    r = admin.post("/api/masters/locations", json={"warehouse_id": wh["id"], "code": "A-01"})
    assert r.status_code == 201 and r.json()["warehouse_code"] == "RM-WH"
    assert admin.post("/api/masters/locations", json={"warehouse_id": wh["id"], "code": "a-01"}).status_code == 409  # unique per warehouse
