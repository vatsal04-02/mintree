"""Notification preferences: mute a kind in-app; urgent alerts cannot be muted; only supported channels are storable."""
from sqlalchemy import text

from app.notifications.service import notify
from tests.conftest import mk_master


def count(client, kind):
    return sum(1 for n in client.get("/api/notifications", params={"page_size": 100}).json()["items"] if n["kind"] == kind)


def submit_po(buyer):
    m = mk_master("NPF")
    po = buyer.post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "5", "unit_price": "10"}]}).json()
    assert buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]}).status_code == 200


def test_muting_a_kind_stops_only_that_user_and_can_be_reversed(login):
    muted, normal, buyer = login("purchase_manager"), login("purchase_manager"), login("purchase_manager")
    assert muted.put("/api/notifications/preferences", json={"kind": "po.submitted", "enabled": False}).status_code == 200
    submit_po(buyer)
    assert count(muted, "po.submitted") == 0 and count(normal, "po.submitted") == 1
    assert muted.get("/api/notifications/preferences").json()["muted_or_set"] == [{"kind": "po.submitted", "channel": "IN_APP", "enabled": False}]
    assert muted.put("/api/notifications/preferences", json={"kind": "po.submitted", "enabled": True}).status_code == 200          # upsert, not a duplicate row
    submit_po(buyer)
    assert count(muted, "po.submitted") == 1 and count(normal, "po.submitted") == 2


def test_urgent_alerts_ignore_mutes(login, db):
    u = login("purchase_manager")
    u.put("/api/notifications/preferences", json={"kind": "stock.critical", "enabled": False})
    notify(db, kind="stock.critical", title="muted normal", user_ids=[u.user.id], priority="NORMAL")
    notify(db, kind="stock.critical", title="urgent", user_ids=[u.user.id], priority="HIGH")
    db.flush()
    titles = [r[0] for r in db.execute(text("SELECT title FROM user_notifications WHERE user_id=:u AND kind='stock.critical'"), {"u": u.user.id})]
    assert titles == ["urgent"]


def test_validation_and_isolation(login, anon):
    a, b = login("purchase_manager"), login("production_manager")
    assert a.put("/api/notifications/preferences", json={"kind": "po.submitted", "channel": "EMAIL", "enabled": False}).status_code == 422     # no e-mail integration exists
    for bad in ("Bad Kind", "nodot", "", "po.", "x" * 70 + ".y"):
        assert a.put("/api/notifications/preferences", json={"kind": bad, "enabled": False}).status_code == 422, bad
    assert a.put("/api/notifications/preferences", json={"kind": "po.submitted", "enabled": "maybe"}).status_code == 422
    assert a.put("/api/notifications/preferences", json={"kind": "po.rejected", "enabled": False}).status_code == 200
    assert b.get("/api/notifications/preferences").json()["muted_or_set"] == []                                                              # preferences are per user
    assert anon.get("/api/notifications/preferences").status_code == 401
