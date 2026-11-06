"""CRM: lead -> customer (match or create) -> opportunity -> quotation -> accepted -> sales order. Guards, history, permissions, concurrency."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session as S

from app.core.clock import business_today
from app.core.db import engine
from app.masters.models import Customer, Item, UnitOfMeasure, Warehouse
from tests.test_dispatch import make_customer  # noqa: F401

N = Decimal


@pytest.fixture
def crm(login):
    return {"s": login("sales_operator"), "m": login("marketing_manager"), "admin": login("system_admin"), "ro": login("management")}


def tag():
    return uuid.uuid4().hex[:8]


def fg_item():
    with S(engine) as s:
        u = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id
        it = Item(item_code=f"FGQ-{tag().upper()}", name="Serum 30ml", kind="FG", uom_id=u, gst_rate=18)
        wh = Warehouse(code=f"WQ-{tag().upper()}", name="WQ", kind="FG")
        s.add_all([it, wh])
        s.commit()
        return it.id, wh.id


def qualified_lead(c, **kw):
    t = tag()
    l = c.post("/api/crm/leads", json={"name": f"Buyer {t}", "company": f"Spa {t}", "email": f"{t}@spa.example", "phone": f"+91{int(t[:6], 16) % 10**8:08d}", **kw}).json()
    for st in ("CONTACTED", "QUALIFIED"):
        assert c.post(f"/api/crm/leads/{l['id']}/status", json={"status": st}).status_code == 200
    return l


def quote(c, cust, item, valid_days=7, price="100", qty="10"):
    q = c.post("/api/sales/quotations", json={"customer_id": cust, "valid_until": str(business_today() + timedelta(days=valid_days)), "lines": [{"item_id": item, "qty": qty, "unit_price": price}]})
    assert q.status_code == 201, q.text
    return q.json()


def test_full_lead_to_sales_order_chain_is_linked_end_to_end(crm, db):
    c, item, wh = crm["s"], *fg_item()
    l = qualified_lead(c)
    conv = c.post(f"/api/crm/leads/{l['id']}/convert", json={"customer_type": "B2B"})
    assert conv.status_code == 200 and conv.json()["matched_existing"] is False
    cust = conv.json()["customer_id"]
    o = c.post("/api/crm/opportunities", json={"name": "Spa chain launch order", "lead_id": l["id"], "expected_value": "5000", "expected_close": str(business_today() + timedelta(days=30))})
    assert o.status_code == 201, o.text
    o = o.json()
    assert o["customer_id"] == cust and o["stage"] == "PROSPECTING" and o["probability"] == 10          # lead was already converted -> master customer carried over
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "WON", "expected_version": o["version"]}).status_code == 409   # no accepted quotation yet
    q = quote(c, cust, item)
    a = c.post(f"/api/crm/opportunities/{o['id']}/quotation", json={"quotation_id": q["id"], "expected_version": o["version"]})
    assert a.status_code == 200 and a.json()["stage"] == "PROPOSAL" and N(a.json()["expected_value"]) == N(q["total"])
    o = a.json()
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "WON", "expected_version": o["version"]}).status_code == 409   # quotation still a draft
    sent = c.post(f"/api/sales/quotations/{q['id']}/send", json={"expected_version": 1}).json()
    acc = c.post(f"/api/sales/quotations/{q['id']}/accept", json={"expected_version": sent["version"]}).json()
    won = c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "WON", "expected_version": o["version"], "note": "PO received"})
    assert won.status_code == 200 and won.json()["stage"] == "WON" and won.json()["probability"] == 100
    so = c.post(f"/api/sales/quotations/{q['id']}/convert", json={"expected_version": acc["version"], "warehouse_id": wh})
    assert so.status_code == 200, so.text
    # everything points at the SAME customer, verified in the database
    row = db.execute(text("""SELECT l.converted_customer_id, o.customer_id, sq.customer_id, so.customer_id, l.status, o.stage
                               FROM opportunities o JOIN leads l ON l.id = o.lead_id JOIN sales_quotations sq ON sq.id = o.quotation_id
                               JOIN sales_orders so ON so.id = sq.sales_order_id WHERE o.id = :o"""), {"o": o["id"]}).one()
    assert len({row[0], row[1], row[2], row[3]}) == 1 and row[0] == cust and (row[4], row[5]) == ("WON", "WON")
    hist = c.get(f"/api/crm/opportunities/{o['id']}").json()["history"]
    assert [(h["from"], h["to"]) for h in hist] == [(None, "PROSPECTING"), ("PROSPECTING", "PROPOSAL"), ("PROPOSAL", "WON")]
    assert db.scalar(text("SELECT count(*) FROM audit_logs WHERE entity='opportunity' AND entity_id=:i"), {"i": str(o["id"])}) == 3
    assert db.scalar(text("SELECT count(*) FROM outbox_events WHERE event_type IN ('crm.lead.converted','crm.opportunity.won') AND aggregate_id IN (:a,:b)"), {"a": str(l["id"]), "b": str(o["id"])}) == 2
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "LOST", "expected_version": won.json()["version"], "lost_reason": "x"}).status_code == 409   # terminal


def test_conversion_matches_existing_customer_instead_of_duplicating(crm, db):
    c = crm["s"]
    t = tag()
    with S(engine) as s:
        ex = Customer(code=f"EX-{t.upper()}", name="Existing Spa", customer_type="B2B", email=f"{t}@same.example")
        s.add(ex); s.commit(); ex_id = ex.id
    l = c.post("/api/crm/leads", json={"name": "New contact", "email": f"{t.upper()}@SAME.example"}).json()          # case-insensitive match
    for st in ("CONTACTED", "QUALIFIED"):
        c.post(f"/api/crm/leads/{l['id']}/status", json={"status": st})
    before = db.scalar(text("SELECT count(*) FROM customers"))
    r = c.post(f"/api/crm/leads/{l['id']}/convert", json={}).json()
    assert r["customer_id"] == ex_id and r["matched_existing"] is True
    assert db.scalar(text("SELECT count(*) FROM customers")) == before
    # explicit link and not-qualified / unknown cases
    l2 = qualified_lead(c)
    assert c.post(f"/api/crm/leads/{l2['id']}/convert", json={"customer_id": ex_id}).json()["customer_id"] == ex_id
    fresh = c.post("/api/crm/leads", json={"name": "Raw lead"}).json()
    r = c.post(f"/api/crm/leads/{fresh['id']}/convert", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lead_not_qualified"
    assert c.post("/api/crm/leads/999999/convert", json={}).status_code == 404
    assert c.post(f"/api/crm/leads/{l2['id']}/convert", json={}).status_code == 409                                  # already converted


def test_ambiguous_match_is_refused(crm):
    c, t = crm["s"], tag()
    with S(engine) as s:
        s.add_all([Customer(code=f"A1-{t.upper()}", name="A", customer_type="B2B", phone=f"+9{t}"), Customer(code=f"A2-{t.upper()}", name="B", customer_type="B2B", phone=f"+9{t}")])
        s.commit()
    l = c.post("/api/crm/leads", json={"name": "Dup phone", "phone": f"+9{t}"}).json()
    for st in ("CONTACTED", "QUALIFIED"):
        c.post(f"/api/crm/leads/{l['id']}/status", json={"status": st})
    r = c.post(f"/api/crm/leads/{l['id']}/convert", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ambiguous_customer_match"


def test_opportunity_rules(crm, db):
    c, item, _ = crm["s"], *fg_item()
    assert c.post("/api/crm/opportunities", json={"name": "No party", "expected_value": "1"}).status_code == 422
    raw = c.post("/api/crm/leads", json={"name": "Unqualified"}).json()
    assert c.post("/api/crm/opportunities", json={"name": "Too early", "lead_id": raw["id"], "expected_value": "1"}).status_code == 409
    cust = make_customer()
    assert c.post("/api/crm/opportunities", json={"name": "Past", "customer_id": cust, "expected_value": "1", "expected_close": str(business_today() - timedelta(days=1))}).status_code == 422
    assert c.post("/api/crm/opportunities", json={"name": "Neg", "customer_id": cust, "expected_value": "-5"}).status_code == 422
    assert c.post("/api/crm/opportunities", json={"name": "Ghost", "customer_id": 99999999, "expected_value": "1"}).status_code == 422
    o = c.post("/api/crm/opportunities", json={"name": "Direct customer deal", "customer_id": cust, "expected_value": "900"}).json()
    # no skipping straight to NEGOTIATION; lost needs a reason; stale version rejected
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "NEGOTIATION", "expected_version": o["version"]}).status_code == 409
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "LOST", "expected_version": o["version"]}).status_code == 422
    assert c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "QUALIFIED", "expected_version": 99}).status_code == 409
    # a quotation for another customer, or one already backing a different opportunity, cannot be attached
    other = make_customer()
    assert c.post(f"/api/crm/opportunities/{o['id']}/quotation", json={"quotation_id": quote(c, other, item)["id"], "expected_version": o["version"]}).status_code == 409
    q = quote(c, cust, item)
    a = c.post(f"/api/crm/opportunities/{o['id']}/quotation", json={"quotation_id": q["id"], "expected_version": o["version"]}).json()
    o2 = c.post("/api/crm/opportunities", json={"name": "Second", "customer_id": cust, "expected_value": "1"}).json()
    dup = c.post(f"/api/crm/opportunities/{o2['id']}/quotation", json={"quotation_id": q["id"], "expected_version": o2["version"]})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "quotation_already_linked"
    lost = c.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "LOST", "expected_version": a["version"], "lost_reason": "Went with a competitor on price"})
    assert lost.status_code == 200 and lost.json()["probability"] == 0
    # the database refuses an inconsistent row even if application code were wrong
    with pytest.raises(Exception, match="won_needs_customer_and_quotation|lost_needs_reason"):
        with db.begin_nested():
            db.execute(text("UPDATE opportunities SET stage='WON', probability=100, closed_at=now() WHERE id=:i"), {"i": o2["id"]})
    s = c.get("/api/crm/pipeline").json()
    assert s["by_stage"]["LOST"]["count"] >= 1 and any("competitor" in r["reason"] for r in s["lost_reasons"])


def test_lost_lead_needs_reason_and_direct_won_is_blocked(crm):
    c = crm["s"]
    l = c.post("/api/crm/leads", json={"name": "Maybe"}).json()
    assert c.post(f"/api/crm/leads/{l['id']}/status", json={"status": "LOST"}).status_code == 422
    r = c.post(f"/api/crm/leads/{l['id']}/status", json={"status": "LOST", "reason": "Not interested"})
    assert r.status_code == 200 and r.json()["lost_reason"] == "Not interested"
    l2 = qualified_lead(c)
    r = c.post(f"/api/crm/leads/{l2['id']}/status", json={"status": "WON"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "use_convert"


def test_activities_and_lead_sources(crm, db):
    c, m = crm["s"], crm["admin"]
    src = m.post("/api/crm/lead-sources", json={"code": f"trade-{tag()}", "name": "Trade fair"})
    assert src.status_code == 201
    l = c.post("/api/crm/leads", json={"name": "From fair", "lead_source_id": src.json()["id"]}).json()
    assert l["lead_source_id"] == src.json()["id"]
    assert c.post("/api/crm/leads", json={"name": "bad src", "lead_source_id": 99999999}).status_code == 422
    a = c.post("/api/crm/activities", json={"kind": "CALL", "subject": "Intro call", "duration_min": 15, "lead_id": l["id"]})
    assert a.status_code == 201 and a.json()["created_by"] == c.user.id
    assert c.post("/api/crm/activities", json={"kind": "CALL", "subject": "orphan"}).status_code == 422                  # needs a subject record
    assert c.post("/api/crm/activities", json={"kind": "TEXT", "subject": "x", "lead_id": l["id"]}).status_code == 422
    assert c.post("/api/crm/activities", json={"kind": "NOTE", "subject": "x", "lead_id": 99999999}).status_code == 422
    assert [x["id"] for x in c.get("/api/crm/activities", params={"lead_id": l["id"]}).json()["items"]] == [a.json()["id"]]


def test_crm_permissions(crm, anon, login):
    assert anon.get("/api/crm/opportunities").status_code == 401
    assert crm["ro"].get("/api/crm/opportunities").status_code == 200                       # management: read only
    assert crm["ro"].post("/api/crm/opportunities", json={"name": "x", "customer_id": 1, "expected_value": "1"}).status_code == 403
    assert crm["ro"].post("/api/crm/activities", json={"kind": "NOTE", "subject": "xx", "customer_id": 1}).status_code == 403
    assert login("production_operator").get("/api/crm/pipeline").status_code == 403
    assert login("qc_inspector").post("/api/crm/leads/1/convert", json={}).status_code == 403


def test_concurrent_stage_moves_one_winner(live_users):
    from concurrent.futures import ThreadPoolExecutor
    s = live_users("sales_operator")[0]
    with S(engine) as ss:
        cust = Customer(code=f"CC-{tag().upper()}", name="Race Co", customer_type="B2B")
        ss.add(cust); ss.commit(); cid = cust.id
    o = s.post("/api/crm/opportunities", json={"name": "Race", "customer_id": cid, "expected_value": "10"}).json()
    with ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(lambda _: s.post(f"/api/crm/opportunities/{o['id']}/stage", json={"stage": "QUALIFIED", "expected_version": o["version"]}).status_code, range(6)))
    assert codes.count(200) == 1 and all(c in (200, 409) for c in codes), codes          # optimistic version + row lock: exactly one wins
    with S(engine) as ss:
        assert ss.scalar(text("SELECT count(*) FROM opportunity_stage_history WHERE opportunity_id=:o AND to_stage='QUALIFIED'"), {"o": o["id"]}) == 1
