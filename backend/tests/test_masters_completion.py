"""Master data completion: unit conversions, tax classes, payment methods, courier partners."""
from decimal import Decimal

from tests.test_masters import uom

N = Decimal


def mk_uom(admin, code):
    r = admin.post("/api/masters/units-of-measure", json={"code": code, "name": code})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def conv(admin, a, b, factor, item_id=None):
    return admin.post("/api/masters/unit-conversions", json={"from_uom_id": a, "to_uom_id": b, "factor": factor, "item_id": item_id})


def test_conversion_direct_inverse_exact_decimal(admin):
    kg, g = uom(admin, "KG"), mk_uom(admin, "GX1")
    assert conv(admin, kg, g, "1000").status_code == 201
    r = admin.get("/api/masters/convert", params={"qty": "2.5", "from_uom_id": kg, "to_uom_id": g}).json()
    assert N(r["result"]) == N("2500")
    inv = admin.get("/api/masters/convert", params={"qty": "250", "from_uom_id": g, "to_uom_id": kg}).json()
    assert N(inv["result"]) == N("0.25")                                    # inverse is derived, not stored twice
    same = admin.get("/api/masters/convert", params={"qty": "7", "from_uom_id": kg, "to_uom_id": kg}).json()
    assert N(same["factor"]) == 1
    # 0.1 + 0.2 style traps: Decimal arithmetic stays exact
    third = admin.get("/api/masters/convert", params={"qty": "0.3", "from_uom_id": kg, "to_uom_id": g}).json()
    assert N(third["result"]) == N("300.0")


def test_item_specific_rule_overrides_generic_and_missing_rule_is_422(admin):
    lt, kg = mk_uom(admin, "LTX2"), uom(admin, "KG")
    item = admin.post("/api/masters/items", json={"item_code": "CV-OIL", "name": "Oil", "kind": "RM", "uom_id": kg}).json()
    assert admin.get("/api/masters/convert", params={"qty": "1", "from_uom_id": lt, "to_uom_id": kg}).status_code == 422   # nothing defined
    assert conv(admin, lt, kg, "1").status_code == 201                                                                      # generic: 1 L = 1 kg
    assert conv(admin, lt, kg, "0.92", item["id"]).status_code == 201                                                       # density of this oil
    generic = admin.get("/api/masters/convert", params={"qty": "10", "from_uom_id": lt, "to_uom_id": kg}).json()
    specific = admin.get("/api/masters/convert", params={"qty": "10", "from_uom_id": lt, "to_uom_id": kg, "item_id": item["id"]}).json()
    assert N(generic["result"]) == N("10") and N(specific["result"]) == N("9.20")


def test_conversion_constraints(admin):
    kg, g = uom(admin, "KG"), mk_uom(admin, "GX3")
    assert conv(admin, kg, g, "0").status_code == 422
    assert conv(admin, kg, g, "-5").status_code == 422
    assert conv(admin, kg, kg, "1").status_code == 422                       # a unit cannot convert to itself
    assert conv(admin, kg, g, "1000").status_code == 201
    assert conv(admin, kg, g, "999").status_code == 409                      # one generic rule per direction (NULLS NOT DISTINCT)
    assert conv(admin, kg, 999999, "2").status_code == 422                   # FK


def test_tax_class_payment_method_courier_crud_and_rules(admin):
    t = admin.post("/api/masters/tax-classes", json={"code": "gst18", "name": "GST 18%", "hsn_prefix": "3304", "gst_rate": "18"})
    assert t.status_code == 201 and t.json()["code"] == "GST18"
    assert admin.post("/api/masters/tax-classes", json={"code": "bad", "name": "x", "gst_rate": "101"}).status_code == 422
    assert admin.post("/api/masters/tax-classes", json={"code": "GST18", "name": "dup", "gst_rate": "5"}).status_code == 409
    p = admin.post("/api/masters/payment-methods", json={"code": "upi", "name": "UPI", "kind": "UPI", "ledger_account_code": "1100"})
    assert p.status_code == 201
    assert admin.post("/api/masters/payment-methods", json={"code": "zz", "name": "x", "kind": "BITCOIN"}).status_code == 422
    c = admin.post("/api/masters/courier-partners", json={"code": "demo-dtdc", "name": "Demo courier", "kind": "COURIER"})
    assert c.status_code == 201 and c.json()["integration_mode"] == "MANUAL"   # never defaults to LIVE
    assert admin.post("/api/masters/courier-partners", json={"code": "x1", "name": "x", "kind": "AIR", "integration_mode": "REAL"}).status_code == 422
    logs = admin.get("/api/admin/audit-logs", params={"entity": "tax_class", "entity_id": t.json()["id"]}).json()["items"]
    assert [l["action"] for l in logs] == ["create"]


def test_permissions_enforced(admin, login, anon):
    viewer = login("production_operator")
    assert viewer.get("/api/masters/tax-classes").status_code == 403
    assert viewer.post("/api/masters/tax-classes", json={"code": "q", "name": "q", "gst_rate": "5"}).status_code == 403
    assert anon.get("/api/masters/courier-partners").status_code == 401
    mgmt = login("management")
    assert mgmt.get("/api/masters/payment-methods").status_code == 200       # read-only visibility
    assert mgmt.post("/api/masters/payment-methods", json={"code": "q", "name": "q", "kind": "CASH"}).status_code == 403


def test_spare_part_cost_is_exact_decimal_not_float(login):
    """Money must never pass through a float: 0.1 + 0.2 style values round-trip exactly and sub-paisa precision is refused."""
    m = login("maintenance_manager")
    eq = m.post("/api/maintenance/equipment", json={"asset_id": f"EQ-{uuid_hex()}", "name": "Test mixer"}).json()
    wo = m.post("/api/maintenance/breakdowns", json={"equipment_id": eq["id"], "description": "Gearbox noise"}).json()
    assert m.post(f"/api/maintenance/work-orders/{wo['id']}/assign", json={"assignee_id": m.user.id}).status_code == 200
    assert m.post(f"/api/maintenance/work-orders/{wo['id']}/start").status_code == 200
    ok = m.post(f"/api/maintenance/work-orders/{wo['id']}/spare-parts", json={"part_name": "Seal", "qty": 3, "unit_cost": "1234.56"})
    assert ok.status_code == 201 and ok.json()["unit_cost"] == "1234.56"
    assert m.get(f"/api/maintenance/work-orders/{wo['id']}/spare-parts").json()[0]["unit_cost"] == "1234.56"
    assert m.post(f"/api/maintenance/work-orders/{wo['id']}/spare-parts", json={"part_name": "Seal", "qty": 1, "unit_cost": "1.005"}).status_code == 422
    assert m.post(f"/api/maintenance/work-orders/{wo['id']}/spare-parts", json={"part_name": "Seal", "qty": 1, "unit_cost": "-1"}).status_code == 422


def uuid_hex():
    import uuid
    return uuid.uuid4().hex[:8].upper()
