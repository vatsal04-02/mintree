"""Production plans and work centres: capacity, combined material check, conversion to orders, attainment, utilisation."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session as S

from app.core.clock import business_today
from app.core.db import engine
from app.masters.models import Item, UnitOfMeasure
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_dispatch import integrity
from tests.test_production import k, plant, stock_rm  # noqa: F401

N = Decimal


def active_bom(plant, m, rm_qty="100", out_qty="100"):
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id
        fg = Item(item_code=f"FGP-{uuid.uuid4().hex[:6].upper()}", name="Plan serum", kind="FG", uom_id=u, gst_rate=18, shelf_life_days=730)
        s.add(fg)
        s.commit()
        fg_id = fg.id
    bom = plant["pm"].post("/api/production/boms/manual", json={"output_item_id": fg_id, "output_qty": out_qty, "lines": [{"item_id": m["item_id"], "qty": rm_qty}]}).json()
    assert plant["pm2"].post(f"/api/production/boms/{bom['id']}/approve").status_code == 200
    return bom["id"], fg_id


def wc(client, cap="100", kind="MIXING"):
    r = client.post("/api/production/work-centres", json={"code": f"wc-{uuid.uuid4().hex[:6]}", "name": "Mixing line", "kind": kind, "capacity_per_day": cap})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def mk_plan(client, lines, name="November plan", start=0, end=30):
    t = business_today()
    return client.post("/api/production/plans", json={"name": name, "period_start": str(t + timedelta(days=start)), "period_end": str(t + timedelta(days=end)), "lines": lines})


def line(bom, wh, qty="50", day=1, wcid=None):
    return {"bom_id": bom, "planned_qty": qty, "planned_date": str(business_today() + timedelta(days=day)), "warehouse_id": wh, "work_centre_id": wcid}


def test_work_centre_crud_and_rules(plant):
    pm = plant["pm"]
    assert pm.post("/api/production/work-centres", json={"code": "bad", "name": "x", "kind": "BOGUS"}).status_code == 422
    assert pm.post("/api/production/work-centres", json={"code": "bad2", "name": "x", "kind": "LAB", "capacity_per_day": "0"}).status_code == 422
    code = f"dup-{uuid.uuid4().hex[:5]}"
    assert pm.post("/api/production/work-centres", json={"code": code, "name": "x", "kind": "LAB"}).status_code == 201
    assert pm.post("/api/production/work-centres", json={"code": code, "name": "y", "kind": "LAB"}).status_code == 409
    assert plant["op"].post("/api/production/work-centres", json={"code": "op-wc", "name": "x", "kind": "LAB"}).status_code == 403


def test_plan_to_orders_with_combined_material_check(plant, db):
    m = mk_master("PLN")
    stock_rm(plant, m, 100, 10)                                                    # 100 kg of the shared component, released
    bom, _ = active_bom(plant, m)                                                  # 100 kg -> 100 units: 1 kg per unit
    c = wc(plant["pm"], cap="200")
    r = mk_plan(plant["pm"], [line(bom, m["wh1"], "60", 1, c), line(bom, m["wh1"], "60", 2, c)])
    assert r.status_code == 201, r.text
    p = r.json()
    mc = plant["pm"].get(f"/api/production/plans/{p['id']}/material-check").json()
    # each line alone (60) fits in 100 kg; together (120) do not: the check must count the shared component once against stock
    assert mc["ok"] is False and len(mc["lines"]) == 1 and mc["lines"][0]["required"] == "120.000" and mc["lines"][0]["short"] == "20.000"
    one = mk_plan(plant["pm"], [line(bom, m["wh1"], "60", 1, c)]).json()
    assert plant["pm"].get(f"/api/production/plans/{one['id']}/material-check").json()["ok"] is True
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/lines/1/convert").status_code == 409                          # not confirmed yet
    cf = plant["pm"].post(f"/api/production/plans/{p['id']}/confirm", json={"expected_version": p["version"]})
    assert cf.status_code == 200 and cf.json()["status"] == "CONFIRMED"
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/confirm", json={"expected_version": p["version"] + 1}).status_code == 409
    o1 = plant["pm"].post(f"/api/production/plans/{p['id']}/lines/1/convert")
    assert o1.status_code == 201, o1.text
    order = o1.json()["order"]
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/lines/1/convert").json()["detail"]["code"] == "already_converted"
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/lines/9/convert").status_code == 404
    # the order really exists, pinned to the same BOM, same date; its stages inherited the work centre
    row = db.execute(text("SELECT o.bom_id, o.planned_qty, o.planned_date FROM production_orders o WHERE o.id=:i"), {"i": order["id"]}).one()
    assert (row[0], row[1]) == (bom, N("60")) and row[2] == business_today() + timedelta(days=1)
    assert db.scalar(text("SELECT count(DISTINCT work_centre_id) FROM production_stages WHERE order_id=:i"), {"i": order["id"]}) == 1
    assert db.scalar(text("SELECT order_id FROM production_plan_lines WHERE plan_id=:p AND line_no=1"), {"p": p["id"]}) == order["id"]
    # closing: needs every line converted, and the orders finished
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/close", json={}).json()["detail"]["code"] == "lines_not_converted"
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/lines/2/convert").status_code == 201
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/close", json={}).json()["detail"]["code"] == "orders_open"
    att = plant["pm"].get(f"/api/production/plans/{p['id']}/attainment").json()
    assert att["planned_qty"] == "120.000" and att["completed_qty"] == "0" and att["attainment_pct"] == "0.00" and all(l["number"] for l in att["lines"])
    integrity(db)


def test_capacity_is_enforced_across_plans_and_overload_needs_a_reason(plant, db):
    m = mk_master("CAP")
    bom, _ = active_bom(plant, m)
    c = wc(plant["pm"], cap="100")
    a = mk_plan(plant["pm"], [line(bom, m["wh1"], "70", 3, c)]).json()
    assert plant["pm"].post(f"/api/production/plans/{a['id']}/confirm", json={"expected_version": a["version"]}).status_code == 200
    b = mk_plan(plant["pm"], [line(bom, m["wh1"], "50", 3, c)]).json()                         # 70 + 50 = 120 > 100 on the same day
    cc = plant["pm"].get(f"/api/production/plans/{b['id']}/capacity-check").json()
    assert cc["ok"] is False and cc["overloads"][0]["over_by"] == "20.000"
    r = plant["pm"].post(f"/api/production/plans/{b['id']}/confirm", json={"expected_version": b["version"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "capacity_exceeded"
    assert plant["pm"].post(f"/api/production/plans/{b['id']}/confirm", json={"expected_version": b["version"], "accept_overload_reason": "ok"}).status_code == 422   # reason too short
    ok = plant["pm"].post(f"/api/production/plans/{b['id']}/confirm", json={"expected_version": b["version"], "accept_overload_reason": "Overtime shift approved by plant head"})
    assert ok.status_code == 200 and ok.json()["overload_reason"].startswith("Overtime")
    assert db.scalar(text("SELECT overload_accepted_by FROM production_plans WHERE id=:i"), {"i": b["id"]}) == plant["pm"].user.id
    d2 = mk_plan(plant["pm"], [line(bom, m["wh1"], "90", 4, c)]).json()                      # different day: fine
    assert plant["pm"].get(f"/api/production/plans/{d2['id']}/capacity-check").json()["ok"] is True


def test_plan_validation(plant):
    m = mk_master("VAL")
    bom, _ = active_bom(plant, m)
    c = wc(plant["pm"])
    pm = plant["pm"]
    assert mk_plan(pm, [line(bom, m["wh1"], "10", 40)], end=30).status_code == 422                       # date outside the period
    assert mk_plan(pm, [line(bom, m["wh1"], "10", 1)], start=10, end=5).status_code == 422               # period ends before it starts
    assert mk_plan(pm, [line(99999999, m["wh1"], "10", 1)]).status_code == 422                           # unknown BOM
    assert mk_plan(pm, [line(bom, m["wh1"], "0", 1)]).status_code == 422
    assert mk_plan(pm, [line(bom, m["wh1"], "-5", 1)]).status_code == 422
    assert mk_plan(pm, [line(bom, m["wh1"], "10", 1, 99999999)]).status_code == 422                      # unknown work centre
    assert mk_plan(pm, []).status_code == 422
    draft_bom = pm.post("/api/production/boms/manual", json={"output_item_id": active_bom(plant, m)[1], "output_qty": "10", "lines": [{"item_id": m["item_id"], "qty": "1"}]}).json()["id"]
    assert mk_plan(pm, [line(draft_bom, m["wh1"], "10", 1)]).status_code == 422                          # only ACTIVE BOMs can be planned
    cancel = mk_plan(pm, [line(bom, m["wh1"], "10", 1, c)]).json()
    assert pm.post(f"/api/production/plans/{cancel['id']}/close", json={"cancel": True}).status_code == 422       # needs a reason
    assert pm.post(f"/api/production/plans/{cancel['id']}/close", json={"cancel": True, "reason": "Demand fell"}).json()["status"] == "CANCELLED"
    assert pm.post(f"/api/production/plans/{cancel['id']}/confirm", json={"expected_version": 99}).status_code == 409


def test_plan_permissions(plant, login, anon):
    m = mk_master("PRM")
    bom, _ = active_bom(plant, m)
    p = mk_plan(plant["pm"], [line(bom, m["wh1"], "10", 1)]).json()
    assert mk_plan(plant["op"], [line(bom, m["wh1"], "10", 1)]).status_code == 403
    assert login("management").get(f"/api/production/plans/{p['id']}").status_code == 200
    assert login("management").post(f"/api/production/plans/{p['id']}/confirm", json={"expected_version": 1}).status_code == 403
    assert login("sales_operator").get("/api/production/plans").status_code == 403
    assert anon.get("/api/production/plans").status_code == 401


def test_full_cycle_attainment_and_utilisation(plant, db):
    """Plan -> confirm -> convert -> release/issue/run stages -> complete: attainment and work-centre hours come from the executed order."""
    m = mk_master("FUL")
    stock_rm(plant, m, 100, 10)
    bom, fg = active_bom(plant, m)
    c = wc(plant["pm"], cap="500", kind="FILLING")
    p = mk_plan(plant["pm"], [line(bom, m["wh1"], "100", 0, c)]).json()
    plant["pm"].post(f"/api/production/plans/{p['id']}/confirm", json={"expected_version": p["version"]})
    o = plant["pm"].post(f"/api/production/plans/{p['id']}/lines/1/convert").json()["order"]
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/release", headers=k(), json={"expected_version": 1}).status_code == 200
    assert plant["pm"].post(f"/api/production/orders/{o['id']}/issue", headers=k()).status_code == 200
    for seq in (1, 2, 3):
        assert plant["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/start", json={}).status_code == 200
        assert plant["op"].post(f"/api/production/orders/{o['id']}/stages/{seq}/complete", json={}).status_code == 200
    done = plant["pm"].post(f"/api/production/orders/{o['id']}/complete", headers=k(), json={"expected_version": 3, "actual_qty": "96"})
    assert done.status_code == 200, done.text
    att = plant["pm"].get(f"/api/production/plans/{p['id']}/attainment").json()
    assert att["completed_qty"] == "96.000" and att["attainment_pct"] == "96.00" and att["lines"][0]["status"] == "COMPLETED" and att["lines"][0]["late"] is False
    u = plant["pm"].get(f"/api/production/work-centres/{c}/utilisation", params={"start": str(business_today() - timedelta(days=1)), "end": str(business_today() + timedelta(days=1))}).json()
    assert u["days"] and u["days"][0]["stages"] == 3 and N(u["total_hours"]) >= 0
    assert plant["pm"].post(f"/api/production/plans/{p['id']}/close", json={}).json()["status"] == "CLOSED"
    assert plant["pm"].get(f"/api/production/work-centres/{c}/utilisation", params={"start": "2026-02-01", "end": "2026-01-01"}).status_code == 422
    integrity(db)


def test_stage_work_centre_assignment(plant, db):
    m = mk_master("SWC")
    stock_rm(plant, m, 100, 10)
    bom, _ = active_bom(plant, m)
    o = plant["pm"].post("/api/production/orders", json={"bom_id": bom, "planned_qty": "50", "warehouse_id": m["wh1"]}).json()
    c = wc(plant["pm"])
    ok = plant["pm"].put(f"/api/production/orders/{o['id']}/stages/1/work-centre", json={"work_centre_id": c})
    assert ok.status_code == 200 and ok.json()["seq"] == 1
    assert plant["pm"].put(f"/api/production/orders/{o['id']}/stages/1/work-centre", json={"work_centre_id": 99999999}).status_code == 422
    assert plant["pm"].put(f"/api/production/orders/{o['id']}/stages/99/work-centre", json={"work_centre_id": c}).status_code == 404
    assert plant["op"].put(f"/api/production/orders/{o['id']}/stages/1/work-centre", json={"work_centre_id": c}).status_code == 403
