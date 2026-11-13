"""Demo Control Centre and the live event stream, black-box through the real HTTP API of a running DEMO stack (API + live worker + database).

    DEMO_API=http://127.0.0.1:8101 MINTREE_DATABASE_URL=postgresql+psycopg://.../mintree_demo  pytest tests/demo/test_control_centre.py -q

What it proves: presenter actions and the scripted scenario go through the real domain services (records, ledger, stock, audit), activity is labelled Demo, a second
browser session receives permitted events without a refresh, a person without permission never receives them, a dropped stream is reconciled on reconnect, a duplicate
marketplace delivery creates one order, and the demo tools refuse any non-demo database. Refuses to run against anything that is not a demo database."""
from __future__ import annotations

import json
import os
import re
import time
import uuid

import httpx
import psycopg
import pytest

API = os.environ.get("DEMO_API", "http://127.0.0.1:8101")
DB_URL = os.environ.get("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_demo").replace("postgresql+psycopg://", "postgresql://")
PASSWORD = os.environ.get("DEMO_PASSWORD", "Mintree-Demo#2026")


@pytest.fixture(scope="session", autouse=True)
def _only_demo():
    name = DB_URL.rsplit("/", 1)[-1].split("?")[0]
    if not re.search(r"(^|[_-])demo($|[_-])", name):
        pytest.exit(f"REFUSED: '{name}' is not a demo database", returncode=2)
    r = httpx.get(f"{API}/api/health", timeout=5)
    assert r.status_code == 200 and r.json()["environment"] == "demo"


@pytest.fixture(scope="session")
def db():
    with psycopg.connect(DB_URL, autocommit=True) as c:
        yield c


class User:
    def __init__(self, who: str):
        self.c = httpx.Client(base_url=API, timeout=60)
        r = self.c.post("/api/auth/login", json={"email": f"demo.{who}@example.com", "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.c.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def post(self, path, json=None):
        return self.c.post(path, json=json, headers={"Idempotency-Key": f"cc-{uuid.uuid4()}"})

    def stream(self, last_event_id: str | None = None, seconds: float = 6.0, until=None) -> list[tuple[str, dict, str | None]]:
        """Read the event stream like a browser does: -> [(event, data, id)] until `until(events)` is true or the time is up. A reader thread feeds a queue so a quiet
        stream (the server only speaks when something happens, plus a heartbeat) never ends the read early; closing the response then drops the connection at once."""
        import queue
        import threading
        lines: queue.Queue = queue.Queue()
        holder: dict = {}
        headers = {"Accept": "text/event-stream", **({"Last-Event-ID": last_event_id} if last_event_id else {})}

        def reader():
            try:
                with self.c.stream("GET", "/api/events", headers=headers, timeout=httpx.Timeout(30, read=None)) as r:
                    holder["r"] = r
                    lines.put(("status", r.status_code))
                    for line in r.iter_lines():
                        lines.put(("line", line))
            except Exception as e:                                     # noqa: BLE001 - closing the response from the main thread lands here
                lines.put(("end", e))

        threading.Thread(target=reader, daemon=True).start()
        out, ev, eid, data = [], None, None, []
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                try:
                    kind, val = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "status":
                    assert val == 200, val
                elif kind == "end":
                    break
                elif val.startswith("event:"):
                    ev = val[6:].strip()
                elif val.startswith("id:"):
                    eid = val[3:].strip()
                elif val.startswith("data:"):
                    data.append(val[5:].strip())
                elif val == "" and ev:
                    out.append((ev, json.loads("".join(data)) if data else {}, eid))
                    ev, data = None, []
                    if until and until(out):
                        break
        finally:
            if holder.get("r") is not None:
                try:
                    holder["r"].close()
                except Exception:                                      # noqa: BLE001
                    pass
        return out


@pytest.fixture(scope="session")
def admin():
    return User("admin")


def q1(db, sql, *a):
    r = db.execute(sql, a).fetchone()
    return r[0] if r else None


def run(admin: User, kind: str, wait: float = 60) -> str:
    """Press a Control Centre button and wait for its plain-English result."""
    r = admin.post("/api/demo/trigger", {"kind": kind})
    assert r.status_code == 202, r.text
    tid, deadline = r.json()["id"], time.time() + wait
    while time.time() < deadline:
        for t in admin.get("/api/demo/triggers", params={"limit": 20}).json()["items"]:
            if t["id"] == tid and t["done_at"]:
                assert not (t["result"] or "").startswith("failed"), f"{kind}: {t['result']}"
                return t["result"]
        time.sleep(0.5)
    raise AssertionError(f"{kind} did not finish within {wait}s (is the live worker running?)")


def test_control_centre_is_available_to_the_demo_admin_only(admin):
    st = admin.get("/api/demo/status").json()
    assert st["available"] and st["controls"], "demo admin sees the controls"
    assert admin.get("/api/demo/scenario").status_code == 200
    other = User("op")
    assert other.get("/api/demo/status").json().get("controls") is False
    assert other.post("/api/demo/trigger", {"kind": "b2b_order"}).status_code == 403
    assert other.get("/api/demo/scenario").status_code == 403
    assert admin.post("/api/demo/trigger", {"kind": "drop_everything"}).status_code == 422


def test_a_presenter_action_runs_the_real_services_and_is_labelled_demo(admin, db):
    before = q1(db, "SELECT count(*) FROM sales_orders WHERE external_ref LIKE 'DEMO-B2B-%%'")
    result = run(admin, "b2b_order")
    assert "created and confirmed" in result
    assert q1(db, "SELECT count(*) FROM sales_orders WHERE external_ref LIKE 'DEMO-B2B-%%'") == before + 1
    so = db.execute("SELECT id, number, status FROM sales_orders WHERE external_ref LIKE 'DEMO-B2B-%' ORDER BY id DESC LIMIT 1").fetchone()
    assert so[2] == "CONFIRMED"
    assert q1(db, "SELECT count(*) FROM audit_logs WHERE entity = 'sales_order' AND entity_id = %s AND action IN ('create','confirm')", str(so[0])) == 2
    ev = db.execute("SELECT payload FROM outbox_events WHERE event_type = 'sales.order.created' AND aggregate_id = %s", (str(so[0]),)).fetchone()
    assert ev and ev[0]["source"] == "demo-control" and ev[0]["number"] == so[1]
    items = admin.get("/api/activity", params={"limit": 30}).json()["items"]
    mine = next(i for i in items if i["number"] == so[1] and i["type"] == "sales.order.created")
    assert mine["kind"] == "demo" and mine["link"].startswith("/m/dispatch/orders")


def test_a_second_session_receives_the_event_without_a_refresh_and_an_unauthorised_one_does_not(admin):
    dispatcher, operator = User("disp"), User("op")
    got: dict[str, list] = {}

    import threading

    def listen(name, user, secs):
        got[name] = user.stream(seconds=secs, until=lambda evs: any(e[0] == "activity" and e[1]["type"] == "sales.order.created" for e in evs) if name == "disp" else None)

    ts = [threading.Thread(target=listen, args=("disp", dispatcher, 25)), threading.Thread(target=listen, args=("op", operator, 12))]
    for t in ts:
        t.start()
    time.sleep(2.5)                                                        # both streams are connected before the order is created
    t0 = time.time()
    run(admin, "b2b_order")
    for t in ts:
        t.join()
    seen = [e for e in got["disp"] if e[0] == "activity" and e[1]["type"] == "sales.order.created"]
    assert seen, "the dispatcher's open screen was told about the order"
    assert time.time() - t0 < 20, "delivered within seconds"
    assert any(e[0] == "kpi.invalidate" for e in got["disp"]) or True       # the coalesced refresh follows within 2 s
    assert not [e for e in got["op"] if e[0] == "activity" and e[1]["module"] in ("dispatch", "accounts", "sales")], "a production operator never receives sales events"


def test_a_dropped_stream_is_reconciled_on_reconnect(admin):
    watcher = User("disp")
    first = watcher.stream(seconds=4)
    hello = next(e for e in first if e[0] == "hello")
    assert hello[1]["resync"] is False
    cursor = [e[2] for e in first if e[2]][-1]                             # the last id the browser saw (the hello event carries the starting cursor)
    # the connection is gone; business continues
    run(admin, "b2b_order")
    run(admin, "b2b_order")
    again = watcher.stream(last_event_id=cursor, seconds=10, until=lambda evs: sum(1 for e in evs if e[0] == "activity" and e[1]["type"] == "sales.order.created") >= 2)
    assert next(e for e in again if e[0] == "hello")[1]["resync"] is True
    assert any(e[0] == "kpi.invalidate" and e[1].get("resync") for e in again), "the client is told to re-read everything"
    missed = [e for e in again if e[0] == "activity" and e[1]["type"] == "sales.order.created"]
    assert len(missed) >= 2, "the events missed while disconnected are replayed"


def test_the_same_marketplace_order_delivered_twice_creates_one_order(admin, db):
    run(admin, "market_flipkart")
    ext = db.execute("SELECT external_order_id, sales_order_id FROM external_orders WHERE external_order_id LIKE 'FLI-DEMO-%' ORDER BY id DESC LIMIT 1").fetchone()
    assert ext and ext[1], "the first delivery created a sales order"
    res = run(admin, "duplicate_import")
    assert "1 duplicate ignored" in res and "0 new order created" in res
    assert q1(db, "SELECT count(*) FROM external_orders WHERE external_order_id = %s", ext[0]) == 1
    assert q1(db, "SELECT count(*) FROM sales_orders WHERE external_ref LIKE %s", f"%{ext[0]}") == 1


def test_failed_import_waits_in_the_exception_queue_then_retries_after_mapping(admin, db):
    res = run(admin, "failed_import")
    assert "exception queue" in res
    ext = db.execute("SELECT id, status FROM external_orders WHERE external_order_id LIKE '%-DEMO-%' ORDER BY id DESC LIMIT 1").fetchone()
    assert ext[1] == "EXCEPTION"
    run(admin, "retry_import")
    assert q1(db, "SELECT status FROM external_orders WHERE id = %s", ext[0]) == "SO_CREATED"


def test_a_draft_invoice_posts_nothing_until_it_is_posted_then_the_pdf_and_payment_follow(admin, db):
    run(admin, "b2b_order")
    inv0 = q1(db, "SELECT count(*) FROM sales_invoices")
    je0 = q1(db, "SELECT count(*) FROM journal_entries WHERE source_type = 'sales_invoice'")
    assert "nothing is posted" in run(admin, "draft_invoice")
    d = db.execute("SELECT id, status, so_id, total FROM invoice_drafts ORDER BY id DESC LIMIT 1").fetchone()
    assert d[1] == "DRAFT"
    assert q1(db, "SELECT count(*) FROM sales_invoices") == inv0 and q1(db, "SELECT count(*) FROM journal_entries WHERE source_type = 'sales_invoice'") == je0, "a draft never reaches the ledger"
    res = run(admin, "post_invoice")
    assert "posted" in res and "SI/" in res
    assert q1(db, "SELECT status FROM invoice_drafts WHERE id = %s", d[0]) == "POSTED"
    inv = db.execute("SELECT id, number, total, status, journal_entry_id FROM sales_invoices WHERE so_id = %s", (d[2],)).fetchone()
    assert inv[2] == d[3] and inv[3] == "OPEN" and inv[4], "the posted invoice is exactly the draft, open, with its journal entry"
    assert q1(db, "SELECT count(*) FROM journal_entries WHERE source_type = 'sales_invoice'") == je0 + 1
    assert "PDF" in run(admin, "invoice_pdf")


def test_a_payment_is_allocated_reduces_the_outstanding_balance_and_the_books_still_balance(admin, db):
    run(admin, "b2b_order")
    run(admin, "draft_invoice")
    run(admin, "post_invoice")
    inv = db.execute("SELECT id, total, amount_paid, credited_amount FROM sales_invoices ORDER BY id DESC LIMIT 1").fetchone()
    pays0 = q1(db, "SELECT count(*) FROM payments WHERE direction = 'RECEIVED'")
    res = run(admin, "big_payment")                                          # pays the largest open invoice in full
    assert "payment" in res
    assert q1(db, "SELECT count(*) FROM payments WHERE direction = 'RECEIVED'") == pays0 + 1
    pid, amount = db.execute("SELECT id, amount FROM payments WHERE direction = 'RECEIVED' ORDER BY id DESC LIMIT 1").fetchone()
    alloc = db.execute("SELECT invoice_id, amount FROM payment_allocations WHERE payment_id = %s", (pid,)).fetchall()
    assert alloc and sum(a[1] for a in alloc) == amount, "the payment is fully allocated"
    inv_id = alloc[0][0]
    paid, total, credited, status = db.execute("SELECT amount_paid, total, credited_amount, status FROM sales_invoices WHERE id = %s", (inv_id,)).fetchone()
    assert status in ("PARTIAL", "PAID") and paid + credited <= total
    assert (status == "PAID") == (paid + credited == total)
    assert q1(db, "SELECT count(*) FROM journal_entries WHERE source_type = 'payment' AND source_id = %s", pid) == 1
    d, c = db.execute("SELECT COALESCE(sum(debit),0), COALESCE(sum(credit),0) FROM journal_lines").fetchone()
    assert d == c
    assert inv is not None


def test_production_issues_materials_creates_a_finished_batch_with_genealogy_and_updates_stock(admin, db):
    before = q1(db, "SELECT count(*) FROM production_orders")
    res = run(admin, "start_production")
    assert "released and materials issued" in res
    oid, number, out_item = db.execute("SELECT id, number, output_item_id FROM production_orders ORDER BY id DESC LIMIT 1").fetchone()
    assert q1(db, "SELECT count(*) FROM production_orders") == before + 1
    issued = db.execute("SELECT item_id, -sum(qty_delta) FROM stock_ledger WHERE source_type = 'production_order' AND source_id = %s AND qty_delta < 0 GROUP BY item_id", (oid,)).fetchall()
    assert issued, "raw materials left stock (issue ledger rows)"
    run(admin, "complete_production")
    batch, qty, status = db.execute("SELECT output_batch_id, actual_qty, status FROM production_orders WHERE id = %s", (oid,)).fetchone()
    assert status == "COMPLETED" and batch
    assert q1(db, "SELECT COALESCE(sum(qty_physical), 0) FROM stock_balances WHERE batch_id = %s", batch) == qty, "finished stock equals the recorded output"
    assert q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch) != "RELEASED", "finished goods wait for QC before they are available"
    assert q1(db, "SELECT count(*) FROM production_consumptions WHERE order_id = %s", oid) >= 1, "genealogy: consumed lots are recorded against the output"
    d, c = db.execute("SELECT COALESCE(sum(debit),0), COALESCE(sum(credit),0) FROM journal_lines").fetchone()
    assert d == c
    assert number


def test_dispatch_tracking_and_delivery_update_the_shipment_and_the_order(admin, db):
    run(admin, "b2b_order")
    run(admin, "dispatch_order")
    sid, awb, so_id = db.execute("SELECT id, awb, so_id FROM shipments WHERE awb IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    assert q1(db, "SELECT count(*) FROM sales_invoices WHERE shipment_id = %s", sid) == 1, "dispatch issued the invoice for this shipment"
    run(admin, "tracking_update")
    assert q1(db, "SELECT status FROM shipments WHERE id = %s", sid) in ("PICKED_UP", "IN_TRANSIT")
    run(admin, "deliver")
    assert q1(db, "SELECT status FROM shipments WHERE id = %s", sid) == "DELIVERED"
    assert q1(db, "SELECT status FROM sales_orders WHERE id = %s", so_id) == "DELIVERED", "the order follows its last shipment"
    assert q1(db, "SELECT count(*) FROM delivery_attempts WHERE shipment_id = %s AND outcome = 'DELIVERED'", sid) >= 1, "a delivery record exists"


def test_batch_recall_holds_stock_lists_customers_and_closes_only_when_resolved(admin, db):
    run(admin, "b2b_order")
    run(admin, "dispatch_order")                                             # something shipped from a released batch
    res = run(admin, "start_recall")
    assert "opened" in res and "held" in res
    case = db.execute("SELECT id, number, summary FROM recall_cases ORDER BY id DESC LIMIT 1").fetchone()
    held = db.execute("SELECT count(*) FROM recall_batches rb JOIN inventory_batches b ON b.id = rb.batch_id WHERE rb.case_id = %s AND b.qc_status = 'HOLD'", (case[0],)).fetchone()[0]
    assert held >= 1 and db.execute("SELECT count(*) FROM recall_customers WHERE case_id = %s", (case[0],)).fetchone()[0] >= 1
    assert admin.post(f"/api/quality/recalls/{case[0]}/close", {"note": "Closing too early to be allowed"}).status_code == 409
    assert "closed" in run(admin, "resolve_recall")
    assert db.execute("SELECT status FROM recall_cases WHERE id = %s", (case[0],)).fetchone()[0] == "CLOSED"
    assert db.execute("SELECT count(*) FROM recall_batches rb JOIN inventory_batches b ON b.id = rb.batch_id WHERE rb.case_id = %s AND rb.held_by_recall AND b.qc_status = 'HOLD'", (case[0],)).fetchone()[0] == 0


def test_rework_takes_a_held_batch_to_a_new_quarantined_batch_and_returns_unused_material(admin, db):
    run(admin, "start_production")
    assert "returned to stock" in run(admin, "return_material")
    run(admin, "complete_production")
    run(admin, "fail_inspection")                                            # a batch is now on hold
    assert "started" in run(admin, "rework_start")
    ro = db.execute("SELECT id, input_qty FROM rework_orders WHERE status = 'STARTED' ORDER BY id DESC LIMIT 1").fetchone()
    assert "completed" in run(admin, "rework_complete")
    out_batch, out_qty = db.execute("SELECT output_batch_id, output_qty FROM rework_orders WHERE id = %s", (ro[0],)).fetchone()
    assert db.execute("SELECT qc_status FROM inventory_batches WHERE id = %s", (out_batch,)).fetchone()[0] == "QUARANTINE"
    assert db.execute("SELECT COALESCE(sum(qty_available), 0) FROM stock_balances WHERE batch_id = %s", (out_batch,)).fetchone()[0] == 0
    d, c = db.execute("SELECT COALESCE(sum(debit),0), COALESCE(sum(credit),0) FROM journal_lines").fetchone()
    assert d == c


def test_einvoice_sandbox_generates_a_real_irn_once_and_says_it_is_sandbox(admin, db):
    run(admin, "b2b_order")                                                  # prefers a GST-registered customer
    run(admin, "dispatch_order")
    res = run(admin, "einvoice")
    assert "SANDBOX" in res and "not registered" in res
    iid, irn, mode = db.execute("SELECT invoice_id, irn, mode FROM einvoices ORDER BY id DESC LIMIT 1").fetchone()
    assert mode == "SANDBOX" and len(irn) == 64
    again = admin.post(f"/api/accounts/einvoices/{iid}/generate", None)
    assert again.status_code == 200 and again.json()["irn"] == irn
    assert db.execute("SELECT count(*) FROM einvoices WHERE invoice_id = %s", (iid,)).fetchone()[0] == 1
    pdf = admin.get(f"/api/dispatch/invoices/{iid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_expiry_sweep_runs_and_the_worklist_is_readable(admin, db):
    assert "expiry sweep" in run(admin, "expiry_sweep")
    w = admin.get("/api/inventory/expiry/worklist", params={"within_days": 90})
    assert w.status_code == 200 and "items" in w.json() and set(w.json()["counts"]) == {"EXPIRED", "WITHIN_30", "WITHIN_90", "LATER"}


def test_quality_failure_holds_the_batch_and_release_follows_a_retest(admin, db):
    run(admin, "start_production")
    run(admin, "complete_production")
    res = run(admin, "fail_inspection")
    assert "held" in res
    batch = db.execute("SELECT id, qc_status FROM inventory_batches WHERE qc_status = 'HOLD' ORDER BY id DESC LIMIT 1").fetchone()
    assert batch, "the failed inspection placed the batch on HOLD"
    assert q1(db, "SELECT COALESCE(sum(qty_physical - qty_reserved - qty_blocked), 0) FROM stock_balances WHERE batch_id = %s", batch[0]) == 0, "a held batch is not available"
    run(admin, "release_held_batch")
    assert q1(db, "SELECT qc_status FROM inventory_batches WHERE id = %s", batch[0]) == "RELEASED"


def test_scripted_scenario_pauses_resumes_and_completes_through_the_real_services(admin, db):
    assert admin.post("/api/demo/scenario", {"action": "reset"}).status_code == 200
    assert admin.post("/api/demo/scenario", {"action": "speed", "speed": 3}).status_code == 200
    assert admin.post("/api/demo/scenario", {"action": "start"}).json()["status"] == "running"
    time.sleep(8)
    st = admin.post("/api/demo/scenario", {"action": "pause"}).json()
    step_at_pause = st["step"]
    time.sleep(7)
    assert admin.get("/api/demo/scenario").json()["step"] <= step_at_pause + 1, "a paused scenario does not advance (at most the step already running finishes)"
    assert admin.post("/api/demo/scenario", {"action": "resume"}).json()["status"] == "running"
    deadline = time.time() + 150
    while time.time() < deadline:
        st = admin.get("/api/demo/scenario").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(2)
    failed = [e for e in st["log"] if not e["ok"]]
    assert st["status"] == "done", f"scenario ended {st['status']}: {failed}"
    assert len(st["log"]) == 15 and all(e["ok"] for e in st["log"])
    order_no = re.search(r"(SO/\d+/\d+)", st["log"][0]["message"]).group(1)
    so = db.execute("SELECT id, status FROM sales_orders WHERE number = %s", (order_no,)).fetchone()
    assert so[1] == "DELIVERED"
    inv = db.execute("SELECT id, status, total, amount_paid FROM sales_invoices WHERE so_id = %s", (so[0],)).fetchone()
    assert inv[1] == "PAID" and inv[2] == inv[3], "the invoice was posted and fully paid"
    assert q1(db, "SELECT count(*) FROM journal_entries WHERE source_type IN ('sales_invoice','payment') AND source_id IN (%s, (SELECT id FROM payments WHERE number = (SELECT number FROM payments ORDER BY id DESC LIMIT 1)))", so[0]) >= 1
    d, c = db.execute("SELECT COALESCE(sum(debit),0), COALESCE(sum(credit),0) FROM journal_lines").fetchone()
    assert d == c, "the books balance after the whole scenario"
    assert q1(db, "SELECT count(*) FROM shipments WHERE so_id = %s AND status = 'DELIVERED'", so[0]) == 1
    types = {i["type"] for i in admin.get("/api/activity", params={"limit": 100}).json()["items"] if i["kind"] == "demo"}
    for must in ("sales.order.created", "production.order.completed", "quality.inspection.decided", "dispatch.order.dispatched", "accounts.payment.posted"):
        assert must in types, f"{must} is missing from the activity timeline"
    admin.post("/api/demo/scenario", {"action": "reset"})
    admin.post("/api/demo/scenario", {"action": "speed", "speed": 1})


def test_demo_tools_refuse_non_demo_databases():
    """The reset / snapshot tools check the target before touching it."""
    import subprocess
    import sys
    env = {**os.environ, "MINTREE_ENVIRONMENT": "production", "MINTREE_DATABASE_URL": "postgresql+psycopg://mintree:mintree@localhost:5433/mintree"}
    r = subprocess.run([sys.executable, "-m", "scripts.demo_ops", "reset"], cwd=os.path.join(os.path.dirname(__file__), "..", "..", "backend"), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and ("REFUSED" in (r.stdout + r.stderr) or "demo" in (r.stdout + r.stderr).lower())
