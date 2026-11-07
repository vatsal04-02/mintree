"""Meta workspace: sandbox records are labelled and never 'connected'; live connections need a verified Graph API response; campaign and content lifecycles."""
import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.branding.meta_provider import DemoMetaProvider, GraphMetaProvider, MetaError, MetaNotConfigured
from app.core.config import get_settings
from app.core.clock import business_today


def conn(c, kind="AD_ACCOUNT", mode="DEMO", ref="DEMO-ACT-T1", **kw):
    return c.post("/api/branding/meta/connections", json={"kind": kind, "name": f"Test {kind}", "external_ref": ref, "mode": mode, **kw})


def test_sandbox_connections_are_labelled_and_cannot_be_verified(login):
    m = login("marketing_manager")
    r = conn(m, ref="DEMO-ACT-T1")
    assert r.status_code == 201 and r.json()["status"] == "SANDBOX" and "Simulated" in r.json()["label"]
    assert conn(m, ref="DEMO-ACT-T1").status_code == 409                                        # duplicate asset
    v = m.post(f"/api/branding/meta/connections/{r.json()['id']}/verify")
    assert v.status_code == 409 and v.json()["detail"]["code"] == "sandbox_record"
    ws = m.get("/api/branding/meta/workspace").json()
    assert ws["live_connected"] is False and ws["has_sandbox_data"] is True and "simulated" in ws["notice"].lower()


def test_live_connection_cannot_be_marked_connected_without_verification(login, db):
    m = login("marketing_manager")
    live = conn(m, mode="LIVE", ref="1234567890").json()
    assert live["status"] == "NOT_CONNECTED" and live["label"] == "Registered, not connected"
    assert m.post(f"/api/branding/meta/connections/{live['id']}/verify").json()["detail"]["code"] == "meta_not_configured"     # no token in the environment
    # even raw SQL cannot fabricate a connected state
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE meta_connections SET status = 'CONNECTED' WHERE id = :i"), {"i": live["id"]})
        db.flush()
    db.rollback()


def test_graph_verification_with_a_mocked_api_and_without_leaking_the_token(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "meta_access_token", "EAAB-secret-token")
    seen = {}

    def ok(req: httpx.Request):
        seen["auth"], seen["url"] = req.headers.get("authorization"), str(req.url)
        return httpx.Response(200, json={"id": "act_555", "name": "Real ads"})

    assert GraphMetaProvider(httpx.MockTransport(ok)).verify("AD_ACCOUNT", "555") == {"id": "act_555", "name": "Real ads"}
    assert seen["auth"] == "Bearer EAAB-secret-token" and "EAAB" not in seen["url"]                      # token in a header, never in the URL
    with pytest.raises(MetaError, match="different asset"):
        GraphMetaProvider(httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "999"}))).verify("PAGE", "123")
    with pytest.raises(MetaError) as e:
        GraphMetaProvider(httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "token EAAB-secret-token"}))).verify("PAGE", "123")
    assert "EAAB" not in str(e.value)
    monkeypatch.setattr(s, "meta_access_token", "")
    with pytest.raises(MetaNotConfigured):
        GraphMetaProvider()
    with pytest.raises(NotImplementedError):
        monkeypatch.setattr(s, "meta_access_token", "x") or GraphMetaProvider().insights("c", date.today(), 1, None)       # live insights are honestly unimplemented


def test_live_verification_through_the_endpoint_sets_connected_with_who_and_when(login, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "meta_access_token", "tok")
    import app.branding.router as r
    monkeypatch.setattr(r, "GraphMetaProvider", lambda: GraphMetaProvider(httpx.MockTransport(lambda q: httpx.Response(200, json={"id": "777", "name": "Mintree page"}))))
    m = login("marketing_manager")
    live = conn(m, kind="PAGE", mode="LIVE", ref="777").json()
    done = m.post(f"/api/branding/meta/connections/{live['id']}/verify")
    assert done.status_code == 200 and done.json()["status"] == "CONNECTED" and done.json()["verified_at"]
    assert m.get("/api/branding/meta/workspace").json()["live_connected"] is True


def test_demo_insights_are_deterministic_and_internally_consistent():
    p, d = DemoMetaProvider(), date(2026, 10, 1)
    a, b = p.insights("campaign-1", d, 1000, "LEADS"), p.insights("campaign-1", d, 1000, "LEADS")
    assert a == b and a.simulated and a.clicks <= a.impressions and a.reach <= a.impressions and a.spend <= 1000


def test_campaign_lifecycle_content_scheduling_and_idempotent_sandbox_sync(login, admin, db):
    m = login("marketing_manager")
    ad = conn(m, ref="DEMO-ACT-LC").json()["id"]
    start = business_today() - timedelta(days=9)
    c = m.post("/api/branding/campaigns", json={"name": "LC test", "objective": "LEADS", "budget": "9000", "start_date": str(start), "end_date": str(start + timedelta(days=20)), "meta_ad_account_id": ad})
    assert c.status_code == 201 and c.json()["objective"] == "LEADS"
    cid = c.json()["id"]
    bad = m.post(f"/api/branding/campaigns/{cid}/status", json={"status": "PAUSED"})
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "invalid_transition"                # PLANNED cannot jump to PAUSED
    for st in ("RUNNING", "PAUSED", "RUNNING"):
        assert m.post(f"/api/branding/campaigns/{cid}/status", json={"status": st}).json()["status"] == st
    assert m.post("/api/branding/campaigns", json={"name": "x", "objective": "FAME"}).status_code == 422
    first = m.post("/api/branding/meta/sync-sandbox").json()
    assert first["simulated"] is True and first["days_created"] >= 10
    assert m.post("/api/branding/meta/sync-sandbox").json()["days_created"] == 0                         # idempotent: existing days are untouched
    n = db.execute(text("SELECT count(*), bool_and(source = 'DEMO') FROM campaign_metrics WHERE campaign_id = :c"), {"c": cid}).one()
    assert n[0] >= 10 and n[1] is True
    an = m.get("/api/branding/analytics", params={"start": str(start), "end": str(business_today())}).json()
    assert an["simulated_days"] >= 10 and "SIMULATED" in an["source"]
    # content: draft -> review -> approve (different user) -> schedule -> publish
    ct = m.post("/api/branding/content", json={"campaign_id": cid, "channel": "INSTAGRAM", "title": "Lifecycle post"}).json()
    assert m.post(f"/api/branding/content/{ct['id']}/schedule", json={"scheduled_for": "2030-01-01T10:00:00+05:30"}).status_code == 409        # not approved yet
    m.post(f"/api/branding/content/{ct['id']}/submit")
    assert m.post(f"/api/branding/content/{ct['id']}/approve").status_code in (403, 409)               # the author cannot approve
    assert admin.post(f"/api/branding/content/{ct['id']}/approve").status_code == 200
    sch = m.post(f"/api/branding/content/{ct['id']}/schedule", json={"scheduled_for": "2030-01-01T10:00:00+05:30"})
    assert sch.status_code == 200 and sch.json()["status"] == "SCHEDULED"
    pub = m.post(f"/api/branding/content/{ct['id']}/publish", json={"external_url": "https://example.invalid/x"})
    assert pub.status_code == 200 and pub.json()["status"] == "PUBLISHED"
    assert m.post(f"/api/branding/content/{ct['id']}/publish", json={}).status_code == 409


def test_permissions(login, anon):
    assert anon.get("/api/branding/meta/workspace").status_code == 401
    assert login("production_operator").get("/api/branding/meta/workspace").status_code == 403
    assert conn(login("management")).status_code == 403                                                  # read-only role cannot register assets
