"""Product 360 / Batch 360 traceability. Every scenario is built through the real services (PO -> goods receipt -> QC -> BOM -> production order -> allocation -> dispatch -> courier webhook -> return),
then the read API is asked who consumed what and who received it. Nothing is inserted into a genealogy table: there is none; the answers come from the transactional rows."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.traceability import service as svc
from tests.conftest import mk_master
from tests.test_dispatch import advance, hook, make_customer, new_so, shop  # noqa: F401  (fixtures/helpers)
from tests.test_inventory_procurement import N
from tests.test_production import k, plant, stock_rm  # noqa: F401  (plant is a fixture that `shop` builds on)
from tests.test_rnd_quality import incoming_setup, qa  # noqa: F401  (fixtures/helpers)


def chain(shop, lots=(60, 40), prices=(10, 12), qty=100):
    """Two supplier lots of ONE raw material feed one production order (multi-source). Returns ids of everything."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.core.db import engine
    from app.masters.models import Item, UnitOfMeasure
    m = mk_master("TRC")
    rm_batches = [stock_rm(shop, m, q, p) for q, p in zip(lots, prices)]
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id
        fg = Item(item_code=f"FG-{uuid.uuid4().hex[:6].upper()}", name="Traceable Serum 30ml", kind="FG", uom_id=u, gst_rate=18, shelf_life_days=730)
        s.add(fg)
        s.commit()
        fg_id = fg.id
    bom = shop["pm"].post("/api/production/boms/manual", json={"output_item_id": fg_id, "output_qty": str(qty), "lines": [{"item_id": m["item_id"], "qty": str(qty)}]}).json()
    shop["pm2"].post(f"/api/production/boms/{bom['id']}/approve")
    o = shop["pm"].post("/api/production/orders", json={"bom_id": bom["id"], "planned_qty": str(qty), "warehouse_id": m["wh1"]}).json()
    shop["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1})
    shop["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k())
    for seq in (1, 2, 3):
        shop["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={})
        shop["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={})
    done = shop["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": str(qty)}).json()
    assert shop["qm"].post(f"/api/inventory/batches/{done['output_batch_id']}/qc-decision", headers=k(), json={"decision": "RELEASED"}).status_code == 200
    return {"m": m, "rm": rm_batches, "fg": fg_id, "wh": m["wh1"], "order": o["id"], "batch": done["output_batch_id"]}


def ship(shop, f, cust, qty, deliver=False):
    o = new_so(shop, f, cust, qty=str(qty))
    packed = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": packed["version"]})
    assert d.status_code == 200, d.text
    sid = d.json()["shipment_id"]
    shop["disp"].post(f"/api/dispatch/shipments/{sid}/book")
    sh = shop["disp"].get(f"/api/dispatch/shipments/{sid}").json()
    if deliver:
        assert hook(shop["disp"], sh["awb"], f"e-{uuid.uuid4().hex[:8]}", "DELIVERED").json()["shipment_status"] == "DELIVERED"
    return {"order": o, "shipment": sh, "dispatch": d.json()}


def g(client, bid, direction="both"):
    r = client.get(f"/api/traceability/batches/{bid}/graph", params={"direction": direction})
    assert r.status_code == 200, r.text
    return r.json()


def by_type(graph, t):
    return [n for n in graph["nodes"] if n["type"] == t]


def edge_qty(graph, s, t):
    return sum((N(e["qty"]) for e in graph["edges"] if e["source"] == s and e["target"] == t and e["qty"] is not None), Decimal(0))


# ---------------------------------------------------------------------------------------------------------------------------------- backward
def test_backward_traceability_follows_actual_consumption_across_two_supplier_lots(shop, db):
    f = chain(shop)
    gr = g(shop["qm"], f["batch"], "backward")
    batches = {n["id"]: n for n in by_type(gr, "batch")}
    for rb in f["rm"]:
        assert f"batch:{rb}" in batches, "both supplier lots that were really consumed appear"
    assert edge_qty(gr, f"batch:{f['rm'][0]}", f"order:{f['order']}") == N(60) and edge_qty(gr, f"batch:{f['rm'][1]}", f"order:{f['order']}") == N(40)
    assert edge_qty(gr, f"order:{f['order']}", f"batch:{f['batch']}") == N(100)
    assert len(by_type(gr, "goods_receipt")) == 2 and len(by_type(gr, "purchase_order")) == 2 and len(by_type(gr, "supplier")) >= 1
    assert gr["truncated"] is False
    # the same answer from the database, independent of the API
    cons = db.execute(text("SELECT batch_id, sum(qty) FROM production_consumptions WHERE order_id = :o GROUP BY 1 ORDER BY 1"), {"o": f["order"]}).all()
    assert {r[0]: N(r[1]) for r in cons} == {f["rm"][0]: N(60), f["rm"][1]: N(40)}


def test_bom_alone_does_not_create_genealogy(shop, db):
    """An approved BOM lists a material, but a batch that was never issued to the order must not appear in the order's ancestry."""
    f = chain(shop)
    unused = stock_rm(shop, f["m"], 25, 11)                                        # released stock of the same material, never consumed
    gr = g(shop["qm"], f["batch"], "backward")
    assert f"batch:{unused}" not in {n["id"] for n in gr["nodes"]}


# ---------------------------------------------------------------------------------------------------------------------------------- forward + impact
def test_forward_traceability_and_impact_reach_the_customers_and_returns(shop, db):
    f = chain(shop)
    c1, c2 = make_customer(), make_customer()
    s1 = ship(shop, f, c1, 30, deliver=True)
    s2 = ship(shop, f, c2, 20)                                                      # partial dispatch to a second customer, still in transit
    fw = g(shop["qm"], f["rm"][0], "forward")
    assert all(isinstance(n["ref"], str) and n["ref"] for n in fw["nodes"]), "every node reference is text (shipment_no is an integer column)"
    assert f"order:{f['order']}" in {n["id"] for n in fw["nodes"]} and f"batch:{f['batch']}" in {n["id"] for n in fw["nodes"]}
    assert len(by_type(fw, "shipment")) == 2 and len(by_type(fw, "customer")) == 2
    assert edge_qty(fw, f"batch:{f['batch']}", f"ship:{s1['shipment']['id']}") == N(30)
    # a raw lot feeding the order is also reachable from the OTHER lot (both are inputs to the same finished batch)
    assert len(by_type(g(shop["qm"], f["rm"][1], "forward"), "customer")) == 2
    imp = shop["qm"].post(f"/api/traceability/batches/{f['rm'][0]}/impact-analysis")
    assert imp.status_code == 200, imp.text
    i = imp.json()
    t = i["totals_finished_goods"]
    assert (N(t["produced"]), N(t["dispatched"]), N(t["delivered"]), N(t["available"])) == (100, 50, 30, 50)
    assert i["summary"]["customers"] == 2 and i["summary"]["shipments"] == 2 and i["summary"]["production_orders"] == 1
    assert N(i["summary"]["suspect_consumed"]) == 60 and N(i["suspect"]["physical"]) == 0
    assert {c["sales_order"] for c in i["customers"]} == {s1["order"]["number"], s2["order"]["number"]}
    assert i["affected_batches"][0]["batch_no"] and N(i["affected_batches"][0]["dispatched"]) == 50


def test_return_is_visible_downstream_and_reconciles(shop, db):
    f = chain(shop)
    cust = make_customer()
    s = ship(shop, f, cust, 30, deliver=True)
    line = shop["disp"].get(f"/api/dispatch/shipments/{s['shipment']['id']}").json()["lines"][0]
    r = shop["disp"].post(f"/api/dispatch/returns?order_id={s['order']['id']}", headers=k(), json={"reason": "damaged on arrival", "lines": [{"shipment_line_id": line["id"], "qty": "4"}]})
    assert r.status_code in (200, 201), r.text
    assert shop["disp"].post(f"/api/dispatch/returns/{r.json()['id']}/receive", headers=k()).status_code in (200, 201)
    assert shop["disp"].post(f"/api/dispatch/returns/{r.json()['id']}/decide", headers=k(), json={"decision": "REJECT"}).status_code in (200, 201)
    fw = g(shop["qm"], f["batch"], "forward")
    assert len(by_type(fw, "return")) == 1 and edge_qty(fw, f"ship:{s['shipment']['id']}", f"ret:{r.json()['id']}") == N(4)
    d = shop["qm"].get(f"/api/traceability/batches/{f['batch']}").json()
    q = d["reconciliation"]["quantities"]
    assert (N(q["produced"]), N(q["dispatched"]), N(q["returned_to_stock"]), N(q["physical"])) == (100, 30, 4, 74)
    assert d["reconciliation"]["balanced"], d["reconciliation"]["warnings"]
    assert N(q["blocked"]) >= 4, "a rejected return stays blocked"


# ---------------------------------------------------------------------------------------------------------------------------------- quantities
def test_reconciliation_balances_and_a_mismatch_is_reported_never_corrected(shop, db):
    f = chain(shop)
    for bid in [*f["rm"], f["batch"]]:
        d = shop["qm"].get(f"/api/traceability/batches/{bid}").json()
        assert d["reconciliation"]["balanced"], (bid, d["reconciliation"]["warnings"])
    d = shop["qm"].get(f"/api/traceability/batches/{f['rm'][0]}").json()
    assert (N(d["reconciliation"]["quantities"]["received"]), N(d["reconciliation"]["quantities"]["consumed_in_production"]), N(d["reconciliation"]["quantities"]["physical"])) == (60, 60, 0)
    # a consumption record with no matching stock movement: the page must say so and must not change anything
    ledger_before = db.execute(text("SELECT count(*), coalesce(sum(qty_delta), 0) FROM stock_ledger WHERE batch_id = :b"), {"b": f["rm"][1]}).one()
    db.execute(text("INSERT INTO production_consumptions (order_id, item_id, batch_id, qty, cost) VALUES (:o, :i, :b, 3, 0)"), {"o": f["order"], "i": f["m"]["item_id"], "b": f["rm"][1]})
    db.commit()
    try:
        d = shop["qm"].get(f"/api/traceability/batches/{f['rm'][1]}").json()
        assert d["reconciliation"]["balanced"] is False
        assert any("Consumption records equal stock issues" in w for w in d["reconciliation"]["warnings"])
        assert db.execute(text("SELECT count(*), coalesce(sum(qty_delta), 0) FROM stock_ledger WHERE batch_id = :b"), {"b": f["rm"][1]}).one() == ledger_before
    finally:
        db.execute(text("DELETE FROM production_consumptions WHERE order_id = :o AND batch_id = :b AND qty = 3"), {"o": f["order"], "i": f["m"]["item_id"], "b": f["rm"][1]})
        db.commit()


def test_a_batch_split_across_warehouses_keeps_one_identity_and_both_locations(shop, db):
    f = chain(shop)
    r = shop["stores"].post("/api/inventory/transfers", headers=k(), json={"item_id": f["fg"], "batch_id": f["batch"], "from_warehouse_id": f["wh"], "to_warehouse_id": f["m"]["wh2"], "qty": "35", "source_id": 7})
    assert r.status_code == 201, r.text
    d = shop["qm"].get(f"/api/traceability/batches/{f['batch']}").json()
    assert len(d["locations"]) == 2 and len({x["warehouse"] for x in d["locations"]}) == 2, "one batch, held in two warehouses"
    assert sorted(N(x["physical"]) for x in d["locations"]) == [35, 65]
    assert d["reconciliation"]["balanced"], d["reconciliation"]["warnings"]
    assert N(d["reconciliation"]["quantities"]["physical"]) == 100 and N(d["reconciliation"]["quantities"]["transfers_net"]) == 0
    mv = shop["qm"].get(f"/api/traceability/batches/{f['batch']}/movements").json()
    assert {x["movement_type"] for x in mv["items"]} >= {"PRODUCTION_OUTPUT", "TRANSFER_OUT", "TRANSFER_IN"}
    fw = g(shop["qm"], f["batch"], "forward")
    assert len(by_type(fw, "warehouse")) == 2


# ---------------------------------------------------------------------------------------------------------------------------------- quality history
def test_failed_result_and_retest_stay_in_the_batch_history(qa, admin, db):
    m, batch, iid = None, None, None
    m, batch, iid = incoming_setup(qa, admin)
    key = lambda: {"Idempotency-Key": f"k-{uuid.uuid4().hex[:14]}"}
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "90"})        # fails (limit 98-101)
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Assay", "value": "99.5"})      # retest passes
    qa["inspector"].post(f"/api/quality/inspections/{iid}/results", json={"test_name": "Appearance", "passed": True})
    assert qa["qm"].post(f"/api/quality/inspections/{iid}/decide", headers=key(), json={"decision": "RELEASE"}).status_code == 200
    d = qa["qm"].get(f"/api/traceability/batches/{batch}").json()
    assert d["batch"]["qc_status"] == "RELEASED"
    insp = d["inspections"][0]
    assert insp["decision"] == "RELEASE" and insp["retested"] is True and insp["failed_results"] == 1
    assay = [r for r in insp["results"] if r["test"] == "Assay"]
    assert [r["passed"] for r in assay] == [False, True], "the failed attempt is never hidden by the later pass"
    gr = g(qa["qm"], batch, "backward")
    assert any(n["type"] == "qc" and n["detail"]["retested"] for n in gr["nodes"])


# ---------------------------------------------------------------------------------------------------------------------------------- permissions, masking, audit
def test_permissions_masking_audit_and_read_only(shop, db, login):
    f = chain(shop)
    cust = make_customer()
    ship(shop, f, cust, 10, deliver=True)
    assert login("production_operator").get(f"/api/traceability/batches/{f['batch']}").status_code == 403
    assert login("production_operator").post(f"/api/traceability/batches/{f['rm'][0]}/impact-analysis").status_code == 403
    # impact analysis needs the QC-release permission as well as read access: a role that can read batches but cannot release them is refused
    reader = login("stores_manager")
    if "inventory.batch:qc_release" not in reader.get("/api/auth/me").json()["permissions"] and "inventory.batch:read" in reader.get("/api/auth/me").json()["permissions"]:
        assert reader.get(f"/api/traceability/batches/{f['batch']}").status_code == 200
        assert reader.post(f"/api/traceability/batches/{f['rm'][0]}/impact-analysis").status_code == 403
    stock = db.execute(text("SELECT count(*), coalesce(sum(qty_physical), 0) FROM stock_balances")).one()
    ledger = db.execute(text("SELECT count(*) FROM stock_ledger")).scalar()
    audits = db.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'impact_analysis'")).scalar()
    assert shop["qm"].post(f"/api/traceability/batches/{f['rm'][0]}/impact-analysis").status_code == 200
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'impact_analysis'")).scalar() == audits + 1, "the analysis itself is audited"
    assert db.execute(text("SELECT count(*), coalesce(sum(qty_physical), 0) FROM stock_balances")).one() == stock and db.execute(text("SELECT count(*) FROM stock_ledger")).scalar() == ledger, \
        "running an analysis changes no stock"
    # names of suppliers and customers are withheld from people who cannot read those masters
    masked = svc.forward(db, f["rm"][0], svc.Mask(False, False))
    assert {n["ref"] for n in masked["nodes"] if n["type"] == "customer"} == {svc.RESTRICTED}
    back = svc.backward(db, f["batch"], svc.Mask(False, False))
    assert {n["ref"] for n in back["nodes"] if n["type"] == "supplier"} == {svc.RESTRICTED}
    shown = svc.forward(db, f["rm"][0], svc.Mask(True, True))
    assert svc.RESTRICTED not in {n["ref"] for n in shown["nodes"] if n["type"] == "customer"}


def test_graph_is_bounded_and_lists_are_paginated(shop, db):
    f = chain(shop)
    j = shop["qm"].get(f"/api/traceability/batches/{f['batch']}/graph", params={"max_depth": 1, "direction": "backward"}).json()
    assert {n["id"] for n in j["nodes"]} >= {f"batch:{f['batch']}", f"order:{f['order']}"} and j["truncated"] is True, "depth 1 stops at the consumed lots and says so"
    assert shop["qm"].get(f"/api/traceability/batches/{f['batch']}/graph", params={"max_depth": 99}).status_code == 422
    assert shop["qm"].get("/api/traceability/batches", params={"page_size": 101}).status_code == 422
    p = shop["qm"].get("/api/traceability/batches", params={"item_id": f["m"]["item_id"], "page_size": 1}).json()
    assert p["total"] == 2 and len(p["items"]) == 1


def test_product_360_summary_is_built_from_records(shop, db):
    f = chain(shop)
    cust = make_customer()
    ship(shop, f, cust, 25, deliver=True)
    p = shop["qm"].get(f"/api/traceability/products/{f['fg']}").json()
    m = p["metrics"]
    assert (N(m["manufactured_qty"]), N(m["dispatched_qty"]), m["production_orders"], m["completed_orders"], m["batches"], m["customers"]) == (100, 25, 1, 1, 1, 1)
    assert N(p["stock"]["physical"]) == 75 and p["recipe"] and p["recipe"]["status"] == "ACTIVE"
    mats = shop["qm"].get(f"/api/traceability/products/{f['fg']}/materials").json()["items"]
    assert len(mats) == 1 and N(mats[0]["planned"]) == 100 and N(mats[0]["consumed"]) == 100 and {N(x["qty"]) for x in mats[0]["lots"]} == {N(60), N(40)}
    assert shop["qm"].get(f"/api/traceability/products/{f['fg']}/production").json()["total"] == 1
    sales = shop["qm"].get(f"/api/traceability/products/{f['fg']}/sales").json()
    assert sales["total"] == 1 and N(sales["items"][0]["qty"]) == 25 and N(sales["items"][0]["delivered"]) == 25
    assert shop["qm"].get(f"/api/traceability/products/{f['fg']}/batches").json()["total"] == 1
    assert shop["qm"].get(f"/api/traceability/products/999999").status_code == 404
