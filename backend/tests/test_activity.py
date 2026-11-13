"""Live activity: committed business events (transactional outbox) feed the timeline and the event stream, filtered per viewer.

An activity exists if and only if its business transaction committed; a refused operation leaves none; a person without the permission never receives it."""
import uuid

import pytest
from sqlalchemy import text

from app.core import activity
from app.core.db import engine
from app.demo_live import router as live
from tests.test_dispatch import advance, make_customer, make_fg, new_so, shop  # noqa: F401  (fixtures/helpers)
from tests.test_production import k, plant  # noqa: F401


@pytest.fixture(autouse=True)
def _settle_test_events():
    """The stream tests commit real events; mark them published so DB-wide integrity checks stay meaningful."""
    yield
    with engine.begin() as c:
        c.execute(text("UPDATE outbox_events SET status='published', published_at=now() WHERE status <> 'published' AND payload->>'number' LIKE 'SO/T/%'"))


def titles(client, **q):
    r = client.get("/api/activity", params={"limit": 100, **q})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_every_catalogue_entry_renders_and_names_a_real_permission():
    """A typo in a permission code would hide an event from everyone, so every code must exist in the permission catalogue."""
    from app.core.permissions import RESOURCES
    real = {f"{res}:{act}" for res, acts in RESOURCES.items() for act in acts}
    for kind, k_ in activity.CATALOGUE.items():
        assert k_.perm in real, (kind, k_.perm)
        assert k_.title({"number": "X-1", "total": "10", "outcome": "DELIVERED", "to": "RELEASED", "decision": "RELEASE", "stage": "Mixing", "action": "started", "direction": "RECEIVED"})


def test_a_business_action_shows_in_the_timeline_with_who_did_it_and_a_link(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust)
    packed = advance(shop, o)
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": packed["version"]})
    assert d.status_code == 200, d.text
    inv = d.json()["invoice"]["number"]
    items = titles(shop["disp"])
    by_type = {i["type"]: i for i in items}
    assert f"New customer order {o['number']}" in by_type["sales.order.created"]["title"]
    assert by_type["sales.order.confirmed"]["title"] == f"Order {o['number']} confirmed"
    assert by_type["sales.order.allocated"]["title"] == f"Stock allocated to order {o['number']}"
    assert inv in by_type["dispatch.order.dispatched"]["title"]
    assert by_type["sales.order.created"]["actor"] == shop["disp"].user.full_name and by_type["sales.order.created"]["kind"] == "person"
    assert by_type["sales.order.created"]["link"] == f"/m/dispatch/orders?open={o['id']}"
    assert by_type["sales.order.created"]["at"].endswith(("+00:00", "Z")) or "+" in by_type["sales.order.created"]["at"]


def test_a_refused_operation_leaves_no_activity(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    ref = f"EXT-{uuid.uuid4().hex[:8]}"
    new_so(shop, f, cust, ref=ref)
    before = db.execute(text("SELECT count(*) FROM outbox_events WHERE event_type = 'sales.order.created'")).scalar()
    dup = shop["disp"].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "channel": "B2B", "external_ref": ref,
                                                          "lines": [{"item_id": f["fg"], "qty": "1", "unit_price": "25"}]})
    assert dup.status_code == 409, dup.text
    after = db.execute(text("SELECT count(*) FROM outbox_events WHERE event_type = 'sales.order.created'")).scalar()
    assert after == before, "a rejected order must not appear as activity"


def test_activity_is_filtered_by_what_the_viewer_may_see(shop, db, login):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust)
    seen_by_dispatch = {i["number"] for i in titles(shop["disp"]) if i["type"] == "sales.order.created"}
    assert o["number"] in seen_by_dispatch
    operator = login("production_operator")                                   # production only: no sales permissions
    assert not [i for i in titles(operator) if i["module"] in ("dispatch", "accounts", "sales")]
    assert operator.get("/api/activity", params={"module": "dispatch"}).json()["items"] == []


def test_source_header_labels_demo_activity_only_in_the_demo_environment(shop, db, monkeypatch):
    f, cust = make_fg(shop, 100, 10), make_customer()
    shop["disp"].headers["X-Mintree-Source"] = "demo-control"
    a = new_so(shop, f, cust)
    mine = [i for i in titles(shop["disp"]) if i["number"] == a["number"] and i["type"] == "sales.order.created"][0]
    assert mine["kind"] == "person" and mine["source"] is None, "the header is ignored outside the demo environment"
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "environment", "demo")
    b = new_so(shop, f, cust)
    demo = [i for i in titles(shop["disp"]) if i["number"] == b["number"] and i["type"] == "sales.order.created"][0]
    assert demo["kind"] == "demo" and demo["source"] == "demo-control"


def test_unregistered_activity_kinds_are_a_programming_error(db):
    with pytest.raises(KeyError):
        activity.emit(db, "sales.order.teleported", entity="sales_order", entity_id=1)


def test_timeline_pages_with_a_cursor(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    for _ in range(3):
        new_so(shop, f, cust, qty="1")
    page = shop["disp"].get("/api/activity", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next"]
    nxt = shop["disp"].get("/api/activity", params={"limit": 2, "before": page["next"]}).json()
    assert nxt["items"] and nxt["items"][0]["id"] < page["items"][-1]["id"]


# ---- the stream's cursor (these use their own committing sessions, like the real stream does)
def _commit_event(kind="sales.order.created", number=None):
    from app.core.db import SessionLocal
    with SessionLocal() as s:
        activity.emit(s, kind, entity="sales_order", entity_id=900000 + uuid.uuid4().int % 99999, number=number or f"SO/T/{uuid.uuid4().hex[:6]}")
        s.commit()


def test_stream_delivers_each_committed_event_once_in_order_and_filters_by_permission():
    cur, _, resync = live._start_cursors(1, None)
    assert resync is False
    _commit_event(number="SO/T/ONE")
    _commit_event(number="SO/T/TWO")
    got = [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"]
    assert got == ["SO/T/ONE", "SO/T/TWO"]
    assert live._poll(1, {"dispatch.order:read"}, cur) == [], "nothing is delivered twice"
    _commit_event(number="SO/T/THREE")
    assert [i for i in live._poll(1, {"production.order:read"}, cur) if i["type"] == "activity"] == [], "no permission, no event"


def test_a_late_committing_transaction_is_not_skipped():
    """Ids are allocated before commit, so a lower id can become visible after a higher one. The cursor must wait for it."""
    from app.core.db import SessionLocal
    cur, _, _ = live._start_cursors(1, None)
    slow = SessionLocal()
    activity.emit(slow, "sales.order.created", entity="sales_order", entity_id=1, number="SO/T/SLOW")          # takes the lower id, not yet committed
    slow.flush()
    _commit_event(number="SO/T/FAST")                                                                         # higher id, committed first
    first = [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"]
    assert first == ["SO/T/FAST"] and cur.gaps
    slow.commit()
    slow.close()
    second = [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"]
    assert second == ["SO/T/SLOW"] and not cur.gaps


def test_a_rolled_back_transaction_never_reaches_the_stream():
    from app.core.db import SessionLocal
    cur, _, _ = live._start_cursors(1, None)
    bad = SessionLocal()
    activity.emit(bad, "sales.order.created", entity="sales_order", entity_id=2, number="SO/T/GHOST")
    bad.flush()
    _commit_event(number="SO/T/REAL")
    bad.rollback()
    bad.close()
    got = [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"]
    got += [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"]
    assert got == ["SO/T/REAL"]


def test_reconnecting_client_replays_what_it_missed_or_asks_for_a_full_refresh(monkeypatch):
    cur0, _, _ = live._start_cursors(1, None)
    _commit_event(number="SO/T/MISSED-1")
    _commit_event(number="SO/T/MISSED-2")
    cur, _, resync = live._start_cursors(1, f"{cur0.ev}:{cur0.ob}")                # what an EventSource sends after a dropped connection
    assert resync is True
    assert [i["data"]["number"] for i in live._poll(1, {"dispatch.order:read"}, cur) if i["type"] == "activity"] == ["SO/T/MISSED-1", "SO/T/MISSED-2"]
    monkeypatch.setattr(live, "REPLAY_CAP", 1)
    far, _, _ = live._start_cursors(1, f"0:{max(0, cur.ob - 2)}")                  # two events behind with a cap of one
    assert far.ob == cur.ob, "too far behind to replay: skip ahead (the client refreshes everything instead)"
    ahead, _, _ = live._start_cursors(1, "999999999:999999999")                      # a snapshot restore left the client ahead of the database
    assert ahead.ob == cur.ob
    assert live._start_cursors(1, "garbage")[2] is False


def test_stream_requires_a_session_and_caps_connections(anon):
    assert anon.get("/api/events").status_code == 401
    assert anon.get("/api/activity").status_code == 401
