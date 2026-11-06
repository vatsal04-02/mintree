"""Live 2-process server: contended allocation, parallel dispatch (gapless invoice numbers), duplicate-dispatch and duplicate-webhook storms."""
import hashlib
import hmac
import json
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.core.db import engine
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_dispatch import make_customer

pytestmark = pytest.mark.filterwarnings("ignore")
key = lambda p="k": f"{p}-{uuid.uuid4().hex}"


def live_fg(live_users, qty=100):
    buyer, admin, stores, qm = (live_users(r)[0] for r in ("purchase_manager", "system_admin", "stores_manager", "quality_manager"))
    pm, pm2 = live_users("production_manager", n=2)
    op = live_users("production_operator")[0]
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.masters.models import Item, UnitOfMeasure
    m = mk_master("DCC")
    po = buyer.post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": str(qty), "unit_price": "10"}]}).json()
    buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
    po = admin.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"] + 1}).json()
    gr = stores.post("/api/procurement/goods-receipts", key=key("gr"), json={"po_id": po["id"], "warehouse_id": m["wh1"], "delivery_note_no": f"DN-{uuid.uuid4().hex[:8]}",
         "lines": [{"po_item_id": po["items"][0]["id"], "qty": str(qty), "mfg_date": str(date.today()), "expiry_date": str(date.today() + timedelta(days=300))}]}).json()
    qm.post(f"/api/inventory/batches/{gr['lines'][0]['batch_id']}/qc-decision", key=key("qc"), json={"decision": "RELEASED"})
    with S(engine) as s:
        fg = Item(item_code=f"FG-{uuid.uuid4().hex[:6].upper()}", name="Serum", kind="FG", uom_id=s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "PCS")).id, gst_rate=18, shelf_life_days=700)
        s.add(fg); s.commit(); fg_id = fg.id
    bom = pm.post("/api/production/boms/manual", json={"output_item_id": fg_id, "output_qty": str(qty), "lines": [{"item_id": m["item_id"], "qty": str(qty)}]}).json()
    pm2.post(f"/api/production/boms/{bom['id']}/approve")
    o = pm.post("/api/production/orders", json={"bom_id": bom["id"], "planned_qty": str(qty), "warehouse_id": m["wh1"]}).json()
    pm.post(f"/api/production/orders/{o['id']}/release", key=key("r"), json={"expected_version": 1})
    pm.post(f"/api/production/orders/{o['id']}/issue", key=key("i"))
    for q in (1, 2, 3):
        op.post(f"/api/production/orders/{o['id']}/stages/{q}/start", json={}); op.post(f"/api/production/orders/{o['id']}/stages/{q}/complete", json={})
    done = pm.post(f"/api/production/orders/{o['id']}/complete", key=key("c"), json={"expected_version": 3, "actual_qty": str(qty)}).json()
    assert qm.post(f"/api/inventory/batches/{done['output_batch_id']}/qc-decision", key=key("qc"), json={"decision": "RELEASED"}).status_code == 200
    return {"fg": fg_id, "wh": m["wh1"]}


def test_contended_allocation_parallel_dispatch_gapless_numbers_and_duplicate_storms(live_users, live_server):
    f = live_fg(live_users, 100)
    disps = live_users("dispatch_manager", n=12)
    cust = make_customer()
    orders = []
    for i in range(10):
        r = disps[i].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "lines": [{"item_id": f["fg"], "qty": "25", "unit_price": "40"}]})
        assert r.status_code == 201, r.text
        orders.append(r.json())
        assert disps[i].post(f"/api/dispatch/orders/{orders[-1]['id']}/confirm", json={"expected_version": 1}).status_code == 200
    with engine.connect() as c:
        dl0 = c.execute(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")).scalar()

    def allocate(i):
        return disps[i].post(f"/api/dispatch/orders/{orders[i]['id']}/allocate", key=key("al"), json={"expected_version": 2})

    with ThreadPoolExecutor(10) as ex:
        rs = list(ex.map(allocate, range(10)))
    out = Counter((r.status_code, "ok" if r.status_code == 200 else r.json()["detail"]["code"]) for r in rs)
    assert out == {(200, "ok"): 4, (409, "insufficient_stock"): 6}, out      # 100 pcs / 25 = exactly 4 orders fit
    won = [i for i, r in enumerate(rs) if r.status_code == 200]
    for i in won:
        assert disps[i].post(f"/api/dispatch/orders/{orders[i]['id']}/pack", json={"expected_version": 3, "cartons": 1, "weight_kg": "2"}).status_code == 200

    def dispatch(i):
        return disps[i].post(f"/api/dispatch/orders/{orders[i]['id']}/dispatch", key=key("ds"), json={"expected_version": 4})

    with ThreadPoolExecutor(4) as ex:
        ds = list(ex.map(dispatch, won))
    assert [r.status_code for r in ds] == [200] * 4, [r.text for r in ds]
    nums = sorted(int(r.json()["invoice"]["number"].rsplit("/", 1)[1]) for r in ds)
    assert nums == list(range(nums[0], nums[0] + 4)), "invoice numbers from concurrent dispatches are contiguous"
    # duplicate-dispatch storm on an already-dispatched order with ONE key: replays only
    k1 = key("dup")
    o2 = disps[11].post("/api/dispatch/orders", json={"customer_id": cust, "warehouse_id": f["wh"], "lines": [{"item_id": f["fg"], "qty": "1", "unit_price": "5"}]}).json()
    # (no stock left -> allocation would fail; the storm targets an order dispatched above)
    target = orders[won[0]]
    with ThreadPoolExecutor(12) as ex:
        storm = list(ex.map(lambda i: disps[i].post(f"/api/dispatch/orders/{target['id']}/dispatch", key=k1, json={"expected_version": 4}), range(12)))
    assert Counter(r.status_code for r in storm) == {409: 12}, Counter(r.status_code for r in storm)       # already DISPATCHED: invalid_status, never a second invoice
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM sales_invoices WHERE so_id = ANY(:ids)"), {"ids": [orders[i]["id"] for i in won]}).scalar() == 4
        assert c.execute(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")).scalar() == dl0
    # duplicate webhook storm: 16 deliveries of ONE event -> one effect
    ship = disps[0].get(f"/api/dispatch/shipments/{ds[0].json()['shipment_id']}").json()
    assert disps[0].post(f"/api/dispatch/shipments/{ship['id']}/book").json()["result"] == "booked"
    awb = disps[0].get(f"/api/dispatch/shipments/{ship['id']}").json()["awb"]
    raw = json.dumps({"event_id": "storm-1", "awb": awb, "event_type": "DELIVERED", "occurred_at": "2026-11-06T12:00:00+00:00"}).encode()
    sig = hmac.new(b"dev-courier-secret", raw, hashlib.sha256).hexdigest()

    def deliver(_):
        return httpx.post(f"{live_server}/api/dispatch/webhooks/courier/fake", content=raw, headers={"X-Signature": sig, "Content-Type": "application/json"}, timeout=30).json()["result"]

    with ThreadPoolExecutor(16) as ex:
        res = Counter(ex.map(deliver, range(16)))
    assert res == {"processed": 1, "duplicate": 15}, res
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM courier_events WHERE shipment_id=:s AND event_type='DELIVERED'"), {"s": ship["id"]}).scalar() == 1
    bad = [x for x in run_checks(engine)["checks"] if not x["ok"]]
    assert not bad, bad
