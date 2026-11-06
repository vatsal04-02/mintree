"""Live 2-process server: concurrent order releases competing for limited stock, with BOMs that list components in OPPOSITE orders."""
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.db import engine
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from tests.test_production import extra_rm
from app.core.clock import business_today  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")
key = lambda p="k": f"{p}-{uuid.uuid4().hex}"


def test_concurrent_releases_never_oversell_and_never_deadlock(live_users):
    buyer, admin, stores, qm = (live_users(r)[0] for r in ("purchase_manager", "system_admin", "stores_manager", "quality_manager"))
    dev, appr = live_users("rnd_manager", n=2)
    pms = live_users("production_manager", n=16)
    m1 = mk_master("PCC")
    rm2, bulk = extra_rm(m1)

    def stock(item_id, price):
        po = buyer.post("/api/procurement/purchase-orders", json={"supplier_id": m1["supplier_id"], "items": [{"item_id": item_id, "qty": "100", "unit_price": price}]}).json()
        buyer.post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
        po = admin.post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"] + 1}).json()
        gr = stores.post("/api/procurement/goods-receipts", key=key("gr"), json={"po_id": po["id"], "warehouse_id": m1["wh1"], "delivery_note_no": f"DN-{uuid.uuid4().hex[:8]}",
             "lines": [{"po_item_id": po["items"][0]["id"], "qty": "100", "mfg_date": str(business_today()), "expiry_date": str(business_today() + timedelta(days=300))}]}).json()
        assert qm.post(f"/api/inventory/batches/{gr['lines'][0]['batch_id']}/qc-decision", key=key("qc"), json={"decision": "RELEASED"}).status_code == 200

    stock(m1["item_id"], "10"); stock(rm2, "20")
    f = dev.post("/api/rnd/formulations", json={"code": f"F-{uuid.uuid4().hex[:6]}", "name": "x", "product_item_id": bulk, "batch_size": "100",
                 "ingredients": [{"item_id": m1["item_id"], "qty": "60"}, {"item_id": rm2, "qty": "40"}]}).json()
    v = f["versions"][0]["id"]
    t = dev.post(f"/api/rnd/versions/{v}/trials", json={"quantity": "1"}).json()
    dev.post(f"/api/rnd/trials/{t['id']}/conclude", json={"status": "PASSED", "observations": "good stable batch"})
    dev.post(f"/api/rnd/versions/{v}/submit")
    assert appr.post(f"/api/rnd/versions/{v}/approve").status_code == 200
    bom_a = pms[0].post("/api/production/boms", json={"formulation_version_id": v}).json()           # lines: RM1 then RM2
    # a second, manually built BOM for a different output with the SAME components listed in the REVERSE order
    _, bulk2 = extra_rm(m1)
    bom_b = pms[0].post("/api/production/boms/manual", json={"output_item_id": bulk2, "output_qty": "100",
                        "lines": [{"item_id": rm2, "qty": "40"}, {"item_id": m1["item_id"], "qty": "60"}]}).json()
    assert pms[1].post(f"/api/production/boms/{bom_b['id']}/approve").status_code == 200
    orders = []
    for i in range(16):
        bom = bom_a if i % 2 == 0 else bom_b
        o = pms[i].post("/api/production/orders", json={"bom_id": bom["id"], "planned_qty": "25", "warehouse_id": m1["wh1"]})
        assert o.status_code == 201, o.text
        orders.append(o.json())
    with engine.connect() as c:
        dl0 = c.execute(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")).scalar()

    def release(i):
        return pms[i].post(f"/api/production/orders/{orders[i]['id']}/release", key=key("rel"), json={"expected_version": 1})

    with ThreadPoolExecutor(16) as ex:
        rs = list(ex.map(release, range(16)))
    out = Counter((r.status_code, (r.json().get("detail") or {}).get("code") if r.status_code != 200 else "released") for r in rs)
    # stock: 100 kg RM1 / 100 kg RM2; each 25 kg order needs 15 / 10  ->  floor(100/15) = 6 orders fit; the rest must get a clean shortage
    assert out == {(200, "released"): 6, (409, "material_shortage"): 10}, out
    with engine.connect() as c:
        assert c.execute(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")).scalar() == dl0, "opposite component order must not deadlock"
        res = c.execute(text("SELECT sum(qty_reserved) FROM stock_balances WHERE item_id = :i"), {"i": m1["item_id"]}).scalar()
        assert res == 6 * 15, "exactly the released orders' materials are reserved (rolled-back attempts left nothing)"
    bad = [x for x in run_checks(engine)["checks"] if not x["ok"]]
    assert not bad, bad
