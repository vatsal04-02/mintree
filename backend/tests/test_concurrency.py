"""Concurrency & stock-integrity tests against a LIVE 2-process server (real HTTP, real threads, real locks).

Every test ends by re-deriving the invariants from the tables (scripts/check_integrity.py). Expected business
conflicts (409) are counted separately from system failures (5xx / unexpected status).
"""
import random
import re
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

from app.core.db import engine
from scripts.check_integrity import run_checks
from tests.conftest import mk_master
from app.core.clock import business_today  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def key(prefix="k"):
    return f"{prefix}-{uuid.uuid4().hex}"


def N(v):
    return Decimal(str(v))


def integrity():
    rep = run_checks(engine)
    bad = [c for c in rep["checks"] if not c["ok"]]
    assert not bad, bad


def pg_deadlocks() -> int:
    with engine.connect() as c:
        return c.execute(text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")).scalar()


def metric(base: str, name: str) -> float:
    body = httpx.get(f"{base}/api/metrics", timeout=10).text
    return sum(float(m.group(2)) for m in re.finditer(rf"^({name}(?:\{{[^}}]*\}})?) ([0-9.e+\-]+)$", body, re.M))


@pytest.fixture
def team(live_users):
    return {"buyer": live_users("purchase_manager")[0], "admin": live_users("system_admin")[0], "stores": live_users("stores_manager")[0],
            "qc": live_users("quality_manager")[0]}


def seed_stock(t, m, qty, *, release=True, note="DN"):
    r = t["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [
        {"item_id": m["item_id"], "qty": str(qty), "unit_price": "10"}]})
    po = r.json()
    t["buyer"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": po["version"]})
    po = t["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"] + 1}).json()
    gr = t["stores"].post("/api/procurement/goods-receipts", key=key("gr"), json={
        "po_id": po["id"], "warehouse_id": m["wh1"], "delivery_note_no": f"{note}-{uuid.uuid4().hex[:8]}",
        "lines": [{"po_item_id": po["items"][0]["id"], "qty": str(qty), "supplier_lot_no": "L1", "mfg_date": str(business_today()),
                   "expiry_date": str(business_today() + timedelta(days=200))}]})
    assert gr.status_code == 201, gr.text
    line = gr.json()["lines"][0]
    if release:
        assert t["qc"].post(f"/api/inventory/batches/{line['batch_id']}/qc-decision", key=key("qc"), json={"decision": "RELEASED"}).status_code == 200
    return po, line


def run_parallel(fn, n_tasks, threads):
    with ThreadPoolExecutor(threads) as ex:
        return list(ex.map(fn, range(n_tasks)))


def classify(resp: httpx.Response, expected: set[int]) -> str:
    if resp.status_code in expected:
        return f"ok{resp.status_code}"
    try:
        why = str(resp.json().get("detail"))[:70]
    except Exception:
        why = resp.text[:70]
    return f"SYSTEM_FAILURE_{resp.status_code}:{why}"


def test_hot_stock_200_reservations_against_100_units(live_users, team, live_server):
    m = mk_master("HOT")
    seed_stock(team, m, 100)
    clients = live_users("production_manager", n=16)
    dl0 = pg_deadlocks()

    def go(i):
        return clients[i % 16].post("/api/inventory/reservations", key=key("res"), json={
            "item_id": m["item_id"], "warehouse_id": m["wh1"], "qty": "1", "reference_type": "so", "reference_id": str(i)})

    out = Counter(classify(r, {201, 409}) for r in run_parallel(go, 200, 16))
    assert out == {"ok201": 100, "ok409": 100}, out     # exactly the stock that exists; nothing oversold; no 5xx/503
    t = team["stores"].get("/api/inventory/availability", params={"item_id": m["item_id"]}).json()["total"]
    assert (N(t["physical"]), N(t["reserved"]), N(t["available"])) == (100, 100, 0)
    assert pg_deadlocks() == dl0, "single hot row: waiters queue on the row lock, nothing may deadlock"
    integrity()


def test_concurrent_identical_receipts_post_exactly_once(live_users, team):
    m = mk_master("DUP")
    r = team["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100", "unit_price": "1"}]})
    po = r.json()
    team["buyer"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": 1})
    po = team["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": 2}).json()
    stores = live_users("stores_manager", n=16)
    k, note = key("gr-dup"), f"DN-{uuid.uuid4().hex[:8]}"
    body = {"po_id": po["id"], "warehouse_id": m["wh1"], "delivery_note_no": note, "lines": [{
        "po_item_id": po["items"][0]["id"], "qty": "40", "mfg_date": str(business_today()), "expiry_date": str(business_today() + timedelta(days=100))}]}
    barrier = threading.Barrier(16)

    def go(i):
        barrier.wait(10)
        return stores[i].post("/api/procurement/goods-receipts", key=k, json=body)

    with ThreadPoolExecutor(16) as ex:
        rs = list(ex.map(go, range(16)))
    assert Counter(r.status_code for r in rs) == {201: 16}, Counter(r.status_code for r in rs)
    assert len({r.json()["id"] for r in rs}) == 1, "all 16 responses describe the same single goods receipt"
    assert sum(1 for r in rs if r.headers.get("idempotent-replayed") == "true") == 15
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM goods_receipts WHERE po_id=:p"), {"p": po["id"]}).scalar() == 1
        assert c.execute(text("SELECT sum(qty_physical) FROM stock_balances WHERE item_id=:i"), {"i": m["item_id"]}).scalar() == 40
    integrity()


def test_concurrent_receipts_cannot_over_receive_a_po_line(live_users, team):
    m = mk_master("OVR")
    r = team["buyer"].post("/api/procurement/purchase-orders", json={"supplier_id": m["supplier_id"], "items": [{"item_id": m["item_id"], "qty": "100", "unit_price": "1"}]})
    po = r.json()
    team["buyer"].post(f"/api/procurement/purchase-orders/{po['id']}/submit", json={"expected_version": 1})
    po = team["admin"].post(f"/api/procurement/purchase-orders/{po['id']}/approve", json={"expected_version": 2}).json()
    stores = live_users("stores_manager", n=10)

    def go(i):
        return stores[i].post("/api/procurement/goods-receipts", key=key("gr"), json={
            "po_id": po["id"], "warehouse_id": m["wh1"], "delivery_note_no": f"DN-{uuid.uuid4().hex[:8]}",
            "lines": [{"po_item_id": po["items"][0]["id"], "qty": "30", "mfg_date": str(business_today()), "expiry_date": str(business_today() + timedelta(days=100))}]})

    rs = run_parallel(go, 10, 10)
    out = Counter(classify(r, {201, 409}) for r in rs)
    assert out == {"ok201": 3, "ok409": 7}, out      # 3 x 30 = 90 fits; a 4th would make 120 > 100
    assert {r.json()["detail"]["code"] for r in rs if r.status_code == 409} == {"over_receipt"}
    with engine.connect() as c:
        assert c.execute(text("SELECT qty_received FROM purchase_order_items WHERE po_id=:p"), {"p": po["id"]}).scalar() == 90
    integrity()


def test_opposite_direction_transfers_do_not_deadlock(live_users, team):
    """A->B and B->A on the same batch is the textbook deadlock. Global lock ordering must make it impossible."""
    m = mk_master("XFR")
    _, line = seed_stock(team, m, 2000)
    assert team["stores"].post("/api/inventory/transfers", key=key("t"), json={
        "item_id": m["item_id"], "batch_id": line["batch_id"], "from_warehouse_id": m["wh1"], "to_warehouse_id": m["wh2"], "qty": "1000", "source_id": 1}).status_code == 201
    clients = live_users("stores_manager", n=16)
    dl0, seq = pg_deadlocks(), iter(range(10_000_000))

    def go(i):
        a, b = (m["wh1"], m["wh2"]) if i % 2 == 0 else (m["wh2"], m["wh1"])
        return clients[i % 16].post("/api/inventory/transfers", key=key("t"), json={
            "item_id": m["item_id"], "batch_id": line["batch_id"], "from_warehouse_id": a, "to_warehouse_id": b, "qty": "1", "source_id": 10 + i})

    out = Counter(classify(r, {201}) for r in run_parallel(go, 800, 16))
    assert out == {"ok201": 800}, out
    assert pg_deadlocks() == dl0, "no deadlocks may be detected by PostgreSQL"
    with engine.connect() as c:
        assert c.execute(text("SELECT sum(qty_physical) FROM stock_balances WHERE item_id=:i"), {"i": m["item_id"]}).scalar() == 2000, "stock is conserved"
    integrity()


def test_mixed_random_operations_keep_every_invariant(live_users, team, live_server):
    m = mk_master("MIX")
    _, line = seed_stock(team, m, 300)
    clients = live_users("system_admin", n=12)
    rnd = random.Random(7)
    ops = [rnd.choice(["reserve", "reserve", "issue", "transfer", "adjust", "release", "qc"]) for _ in range(600)]
    resv: list[int] = []
    lock = threading.Lock()
    dl0, to0, shed0 = pg_deadlocks(), metric(live_server, "mintree_db_pool_timeouts_total"), metric(live_server, "mintree_admission_shed_total")

    def go(i):
        c, op = clients[i % 12], ops[i]
        if op == "reserve":
            r = c.post("/api/inventory/reservations", key=key(), json={"item_id": m["item_id"], "warehouse_id": m["wh1"], "qty": str(rnd.randint(1, 5)),
                                                                          "reference_type": "so", "reference_id": str(i)})
            if r.status_code == 201:
                with lock:
                    resv.extend(x["reservation_id"] for x in r.json()["reservations"])
            return classify(r, {201, 409})
        if op == "release":
            with lock:
                ids = [resv.pop()] if resv else []
            if not ids:
                return "ok-skip"
            return classify(c.post("/api/inventory/reservations/release", key=key(), json={"reservation_ids": ids}), {200})
        if op == "issue":
            return classify(c.post("/api/inventory/issues", key=key(), json={"item_id": m["item_id"], "warehouse_id": m["wh1"], "qty": str(rnd.randint(1, 3)),
                                                                               "source_type": "mix", "source_id": 1000 + i}), {201, 409})
        if op == "transfer":
            a, b = rnd.sample([m["wh1"], m["wh2"]], 2)
            return classify(c.post("/api/inventory/transfers", key=key(), json={"item_id": m["item_id"], "batch_id": line["batch_id"], "from_warehouse_id": a,
                                                                                  "to_warehouse_id": b, "qty": "1", "source_id": 5000 + i}), {201, 409})
        if op == "adjust":
            return classify(c.post("/api/inventory/adjustments", key=key(), json={"balance_id": line["balance_id"], "delta": rnd.choice(["1", "-1"]),
                                                                                    "reason": "cycle count adjustment", "source_id": 9000 + i}), {201, 409})
        # qc hold/release toggling while everything else runs
        return classify(c.post(f"/api/inventory/batches/{line['batch_id']}/qc-decision", key=key(), json={"decision": rnd.choice(["HOLD", "RELEASED"])}), {200, 409})

    out = Counter(run_parallel(go, 600, 12))
    failures = {k: v for k, v in out.items() if k.startswith("SYSTEM_FAILURE")}
    assert not failures, out
    assert out["ok201"] > 100, out    # the workload really did post stock movements
    assert pg_deadlocks() == dl0
    assert metric(live_server, "mintree_db_pool_timeouts_total") == to0, "no connection-pool exhaustion"
    assert metric(live_server, "mintree_admission_shed_total") == shed0, "no load shedding at this concurrency"
    integrity()
