"""Proposals and the briefing: deterministic rules, human acceptance through the normal service, dedupe, stale handling, permissions."""
import pytest
from sqlalchemy import text

from app.core.clock import business_today
from tests.conftest import mk_master
from tests.test_ai_assistant import ai_on  # noqa: F401


@pytest.fixture
def low_item(db):
    m = mk_master("RO")
    from app.core.db import engine
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.execute(text("UPDATE items SET reorder_level = 100 WHERE id = :i"), {"i": m["item_id"]})
        s.commit()
        code = s.execute(text("SELECT item_code FROM items WHERE id=:i"), {"i": m["item_id"]}).scalar()
    yield {**m, "code": code}
    with Session(engine) as s:
        s.execute(text("UPDATE items SET reorder_level = NULL WHERE id = :i"), {"i": m["item_id"]})
        s.commit()


def gen(c):
    return c.post("/api/ai/proposals/generate")


def mine(c, code, status="PROPOSED"):
    return [p for p in c.get("/api/ai/proposals", params={"status": status}).json()["items"] if p["kind"] == "REORDER" and code in p["title"]]


def test_reorder_proposal_is_generated_once_explained_and_accepted_as_the_user(login, ai_on, low_item, db):
    buyer = login("purchase_manager")
    assert gen(buyer).status_code == 200
    ps = mine(buyer, low_item["code"])
    assert len(ps) == 1 and ps[0]["payload"]["qty"] == "200.000"            # order up to 2 x reorder level (100) from 0 available
    assert "below the reorder level" in ps[0]["rationale"]
    gen(buyer)
    assert len(mine(buyer, low_item["code"])) == 1                           # repeat generation is a no-op (partial unique index)
    assert db.execute(text("SELECT count(*) FROM purchase_requests")).scalar() == 0     # nothing exists until a person accepts
    r = buyer.post(f"/api/ai/proposals/{ps[0]['id']}/accept")
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    pr = db.execute(text("SELECT status, requested_by FROM purchase_requests WHERE id=:i"), {"i": res["id"]}).one()
    assert pr == ("DRAFT", buyer.user.id)                                    # a DRAFT request, owned by the accepting user, still needs the normal approval chain
    audit_users = {r[0] for r in db.execute(text("SELECT user_id FROM audit_logs WHERE (entity='purchase_request' AND entity_id=:p) OR (action='ai.proposal_accepted' AND entity_id=:q)"), {"p": str(res["id"]), "q": str(ps[0]["id"])})}
    assert audit_users == {buyer.user.id}
    assert buyer.post(f"/api/ai/proposals/{ps[0]['id']}/accept").status_code == 409          # cannot be accepted twice (no second PR)
    assert db.execute(text("SELECT count(*) FROM purchase_requests")).scalar() == 1
    gen(buyer)
    assert mine(buyer, low_item["code"]) == []                               # an open PR now covers the item


def test_reject_and_regenerate(login, ai_on, low_item):
    buyer = login("purchase_manager")
    gen(buyer)
    p = mine(buyer, low_item["code"])[0]
    assert buyer.post(f"/api/ai/proposals/{p['id']}/reject").json()["status"] == "REJECTED"
    assert buyer.post(f"/api/ai/proposals/{p['id']}/accept").status_code == 409
    gen(buyer)
    assert len(mine(buyer, low_item["code"])) == 1                           # the condition still holds, so it may be suggested again


def test_stale_proposal_is_withdrawn_not_executed(login, ai_on, low_item, db):
    buyer = login("purchase_manager")
    gen(buyer)
    p = mine(buyer, low_item["code"])[0]
    from app.core.db import engine
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.execute(text("UPDATE items SET reorder_level = 0 WHERE id=:i"), {"i": low_item["item_id"]})
        s.commit()
    r = buyer.post(f"/api/ai/proposals/{p['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "proposal_stale"
    assert db.execute(text("SELECT status FROM ai_proposals WHERE id=:i"), {"i": p["id"]}).scalar() == "EXPIRED"
    assert db.execute(text("SELECT count(*) FROM purchase_requests")).scalar() == 0


def test_permissions_and_switch(login, ai_on, low_item, anon):
    buyer, mgmt, acct = login("purchase_manager"), login("management"), login("accounts_manager")
    gen(buyer)
    p = mine(buyer, low_item["code"])[0]
    assert mgmt.get("/api/ai/proposals").status_code == 403 and gen(acct).status_code == 403
    assert anon.get("/api/ai/proposals").status_code == 401
    # a reviewer without the underlying permission cannot see or act on the proposal
    stores = login("stores_manager")
    assert all(x["id"] != p["id"] for x in stores.get("/api/ai/proposals").json()["items"])
    assert stores.post(f"/api/ai/proposals/{p['id']}/accept").status_code == 404
    ai_on.ai_enabled = False
    assert buyer.post(f"/api/ai/proposals/{p['id']}/accept").status_code == 409
    assert gen(buyer).status_code == 409


def test_overdue_invoice_follow_up_proposal(login, ai_on, db):
    mgr = login("dispatch_manager")
    cid = db.execute(text("INSERT INTO customers(code,name,customer_type,credit_days,is_active) VALUES ('C-AIP','AI Prop Customer','DISTRIBUTOR',10,true) RETURNING id")).scalar()
    wh = db.execute(text("INSERT INTO warehouses(code,name,kind) VALUES ('W-AIP','AI wh','FG') RETURNING id")).scalar()
    so = db.execute(text("INSERT INTO sales_orders(number,customer_id,channel,status,warehouse_id,created_by,subtotal,tax_total,total) VALUES ('SO/AIP/1',:c,'B2B','CONFIRMED',:w,:u,1000,180,1180) RETURNING id"), {"c": cid, "w": wh, "u": mgr.user.id}).scalar()
    db.execute(text("""INSERT INTO sales_invoices(number,customer_id,so_id,invoice_date,subtotal,tax_total,total,amount_paid,status)
                              VALUES ('INV/AIP/1',:c,:s,:d,1000,180,1180,0,'OPEN')"""), {"c": cid, "s": so, "d": business_today().replace(year=business_today().year - 1)})
    db.flush()
    assert gen(mgr).status_code == 200
    ps = [p for p in mgr.get("/api/ai/proposals").json()["items"] if p["kind"] == "FOLLOW_UP" and "INV/AIP/1" in p["title"]]
    assert len(ps) == 1 and "1180" in ps[0]["rationale"] and "no open follow-up" in ps[0]["rationale"]
    r = mgr.post(f"/api/ai/proposals/{ps[0]['id']}/accept")
    assert r.status_code == 200, r.text
    f = db.execute(text("SELECT customer_id, assigned_to, status FROM followups WHERE id=:i"), {"i": r.json()["result"]["id"]}).one()
    assert f == (cid, mgr.user.id, "OPEN")
    gen(mgr)
    assert not [p for p in mgr.get("/api/ai/proposals").json()["items"] if "INV/AIP/1" in p["title"]]      # the customer now has an open follow-up


def test_briefing_is_per_user_permission_scoped_and_cached(login, ai_on, low_item):
    mgmt, stores = login("management"), login("stores_manager")
    b = mgmt.get("/api/ai/briefing").json()
    keys = {s["key"] for s in b["sections"]}
    assert b["generated_without_ai_model"] is True and {"late_pos", "receivables", "low_stock", "quality", "production"} <= keys and "proposals" not in keys and b["cached"] is False
    assert mgmt.get("/api/ai/briefing").json()["cached"] is True
    assert mgmt.get("/api/ai/briefing", params={"refresh": True}).json()["cached"] is False
    sk = {s["key"] for s in stores.get("/api/ai/briefing").json()["sections"]}
    assert "receivables" not in sk and "low_stock" in sk and "proposals" in sk              # stores cannot see receivables
    low = [s for s in mgmt.get("/api/ai/briefing", params={"refresh": True}).json()["sections"] if s["key"] == "low_stock"][0]
    assert any(i["label"] == low_item["code"] for i in low["items"]) or low["count"] >= 5
    ai_on.ai_enabled = False
    assert mgmt.get("/api/ai/briefing").status_code == 409
