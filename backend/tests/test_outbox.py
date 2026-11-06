"""Transactional outbox, publisher semantics and idempotent consumers (real Postgres; real RabbitMQ where noted)."""
import threading
import time
import uuid

import pytest
from sqlalchemy import select, text

from app.core import outbox
from app.core.db import engine
from app.core.platform_models import JobFailure, Notification, OutboxEvent
from app.core.uow import run_tx
from app.workers.consumers import REGISTRY
from app.workers.outbox_publisher import OutboxPublisher


class FakeBroker:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for, self.lock = [], set(fail_for), threading.Lock()

    def publish(self, *, event_id, event_type, payload, request_id):
        if payload.get("tag") in self.fail_for:
            raise ConnectionError("broker unavailable")
        with self.lock:
            self.sent.append(event_id)


@pytest.fixture(autouse=True)
def _cleanup_test_events():
    """Tests here create pending/dead events on purpose; settle them so DB-wide integrity checks stay meaningful."""
    yield
    with engine.begin() as c:
        c.execute(text("UPDATE outbox_events SET status='published', published_at=now() WHERE payload ? 'tag' AND status <> 'published'"))
        c.execute(text("UPDATE job_failures SET resolved_at = now() WHERE resolved_at IS NULL"))


def emit(committed, tag, n=1):
    ids = []
    with committed() as db:
        def work(d):
            for i in range(n):
                ids.append(str(outbox.add(d, event_type="test.thing", aggregate_type="thing", aggregate_id=i,
                                          payload={"tag": tag, "i": i})))
        run_tx(db, work, name="test.emit")
    return ids


def rows(tag):
    with engine.connect() as c:
        return c.execute(text("SELECT event_id::text, status, attempts FROM outbox_events WHERE payload->>'tag'=:t ORDER BY id"),
                         {"t": tag}).all()


def test_event_is_atomic_with_the_business_transaction(committed, uid):
    tag = uid("atomic")
    with committed() as db, pytest.raises(RuntimeError):
        def work(d):
            outbox.add(d, event_type="test.thing", aggregate_type="thing", aggregate_id=1, payload={"tag": tag})
            raise RuntimeError("business rule failed after emitting")
        run_tx(db, work, name="test.atomic")
    assert rows(tag) == [], "rolled-back business change must not leave an event"
    emit(committed, tag)
    assert len(rows(tag)) == 1


def test_publisher_publishes_committed_events_once_and_marks_them(committed, uid):
    tag = uid("pub")
    ids = emit(committed, tag, n=5)
    b = FakeBroker()
    # other tests' pending events may exist; drain until ours are done
    while OutboxPublisher(b).run_once():
        pass
    assert sorted(b.sent) >= sorted(ids) or set(ids) <= set(b.sent)
    assert [r[1] for r in rows(tag)] == ["published"] * 5
    assert all(b.sent.count(i) == 1 for i in ids)


def test_failure_backs_off_then_goes_dead_for_inspection(committed, uid, monkeypatch):
    tag = uid("fail")
    emit(committed, tag)
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "outbox_max_attempts", 2)
    pub = OutboxPublisher(FakeBroker(fail_for={tag}))
    # Claim only our event: make other pending rows invisible by processing until ours has been attempted.
    for expected in ("pending", "dead"):
        with engine.begin() as c:  # make the retry due now (simulating the back-off elapsing)
            c.execute(text("UPDATE outbox_events SET next_attempt_at = now() WHERE payload->>'tag'=:t AND status='pending'"), {"t": tag})
        pub.run_once()
        st = rows(tag)[0]
        assert st[1] == expected
    assert rows(tag)[0][2] == 2
    with engine.connect() as c:
        err = c.execute(text("SELECT last_error FROM outbox_events WHERE payload->>'tag'=:t"), {"t": tag}).scalar()
    assert "ConnectionError" in err


def test_crashed_publisher_is_recovered_after_lease_expiry(committed, uid):
    """Claim then 'crash' (never mark). After the lease another publisher re-publishes: at-least-once delivery."""
    tag = uid("crash")
    ids = emit(committed, tag, n=3)
    with committed() as db:  # the doomed publisher claims and dies
        run_tx(db, lambda d: d.execute(outbox.CLAIM_SQL, {"n": 1000, "me": "doomed:1", "lease": 60}).all(), name="test.claim")
    assert all(r[1] == "publishing" for r in rows(tag))
    b = FakeBroker()
    OutboxPublisher(b).run_once()
    assert not any(i in b.sent for i in ids), "lease still valid: nobody may steal the claim yet"
    with engine.begin() as c:
        c.execute(text("UPDATE outbox_events SET locked_at = now() - interval '5 minutes' WHERE payload->>'tag'=:t"), {"t": tag})
    while OutboxPublisher(b).run_once():
        pass
    assert all(b.sent.count(i) == 1 for i in ids)
    assert [r[1] for r in rows(tag)] == ["published"] * 3


def test_concurrent_publishers_never_double_claim(committed, uid):
    tag = uid("conc")
    ids = emit(committed, tag, n=300)
    b = FakeBroker()
    stop = threading.Event()

    def run(name):
        p = OutboxPublisher(b, name=name)
        p.batch = 25
        while not stop.is_set():
            if not p.run_once():
                if all(r[1] == "published" for r in rows(tag)):
                    break
                time.sleep(0.01)

    ths = [threading.Thread(target=run, args=(f"p{i}",)) for i in range(4)]
    [t.start() for t in ths]; [t.join(60) for t in ths]; stop.set()
    mine = [e for e in b.sent if e in set(ids)]
    assert len(mine) == 300 and len(set(mine)) == 300, "SKIP LOCKED: every event claimed by exactly one publisher"


def test_consumer_is_idempotent_under_redelivery(committed, uid):
    from app.workers.tasks import consume
    eid = str(uuid.uuid4())
    args = ["notify.goods_receipt_posted", eid, "inventory.goods_receipt.posted", {"number": "GR-9"}, "req-1", time.time()]
    assert consume.apply(args=args).get() == "processed"
    assert consume.apply(args=args).get() == "duplicate"   # broker redelivered the same message
    assert consume.apply(args=args).get() == "duplicate"
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM notifications WHERE event_id=:e"), {"e": eid}).scalar() == 1
        assert c.execute(text("SELECT request_id FROM notifications WHERE event_id=:e"), {"e": eid}).scalar() == "req-1"


def test_failing_handler_leaves_no_partial_effect_and_no_dedupe_marker(committed, monkeypatch):
    from app.workers import consumers as C
    from app.workers.tasks import consume
    eid = str(uuid.uuid4())

    def bad(db, ev):
        db.execute(text("INSERT INTO notifications(event_id, kind, title) VALUES (:e, 'half', 'x')"), {"e": ev.event_id})
        raise RuntimeError("handler exploded after writing")
    monkeypatch.setitem(REGISTRY, "test.bad", C.Consumer("test.bad", "test.thing", "default", bad))
    with pytest.raises(Exception):
        consume.apply(args=["test.bad", eid, "test.thing", {}, None, None], throw=True)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM notifications WHERE event_id=:e"), {"e": eid}).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM processed_events WHERE event_id=:e"), {"e": eid}).scalar() == 0, \
            "marker must roll back with the effects, otherwise the retry would be skipped as a duplicate"


def test_permanent_failure_is_recorded_for_inspection(committed):
    from app.workers.tasks import ConsumerTask
    eid = uuid.uuid4()
    t = ConsumerTask()
    t.name = "mintree.consume"
    t.request_stack = type("S", (), {"top": type("R", (), {"retries": 5})()})()
    ConsumerTask.on_failure(type("T", (), {"name": "mintree.consume", "request": type("R", (), {"retries": 5})()})(),
                            RuntimeError("boom"), "task-1", ["notify.x", str(eid), "e", {"a": 1}, "req-9"], {}, None)
    with committed() as db:
        f = db.scalar(select(JobFailure).where(JobFailure.event_id == eid))
    assert f and "RuntimeError: boom" in f.error and f.request_id == "req-9" and f.resolved_at is None and f.attempts == 6


def _broker_up():
    import socket
    try:
        socket.create_connection(("127.0.0.1", 5672), 1).close(); return True
    except OSError:
        return False


@pytest.mark.skipif(not _broker_up(), reason="RabbitMQ not running")
def test_real_rabbitmq_roundtrip_is_durable_and_carries_correlation_id(committed, uid):
    from app.workers.broker import CeleryBroker
    from app.workers.celery_app import celery
    broker, eid = CeleryBroker(), str(uuid.uuid4())
    broker.publish(event_id=eid, event_type="inventory.goods_receipt.posted", payload={"number": "GR-RT"}, request_id="rid-77")
    body = persistent = None
    with celery.connection_for_read() as conn:
        q = conn.SimpleQueue(celery.amqp.queues["notifications"])  # same args (DLX) as the worker declares
        for _ in range(200):  # the queue may hold messages from other tests/runs; find ours by event id
            msg = q.get(block=True, timeout=5)
            if msg.payload[0][1] == eid:
                body, persistent = msg.payload, msg.properties.get("delivery_mode")
            msg.ack()
            if body:
                break
    assert persistent == 2, "persistent delivery"
    assert body[0][:2] == ["notify.goods_receipt_posted", eid] and body[0][4] == "rid-77"


def test_admin_can_inspect_and_requeue_dead_events_and_see_failed_jobs(admin, login, committed, uid):
    tag = uid("dead")
    with committed() as db:
        run_tx(db, lambda d: outbox.add(d, event_type="test.thing", aggregate_type="thing", aggregate_id=1, payload={"tag": tag}), name="t")
    with engine.begin() as c:
        c.execute(text("UPDATE outbox_events SET status='dead', attempts=8, last_error='ConnectionError: x' WHERE payload->>'tag'=:t"), {"t": tag})
        oid = c.execute(text("SELECT id FROM outbox_events WHERE payload->>'tag'=:t"), {"t": tag}).scalar()
        c.execute(text("INSERT INTO job_failures(task_name, error, attempts) VALUES ('notify.x', 'RuntimeError: boom', 6)"))
    body = admin.get("/api/admin/outbox").json()
    assert any(d["id"] == oid and "ConnectionError" in d["last_error"] for d in body["dead"]) and body["counts"].get("dead", 0) >= 1
    assert any(f["task"] == "notify.x" for f in admin.get("/api/admin/job-failures").json())
    assert login("management").get("/api/admin/outbox").status_code == 403
    assert admin.post(f"/api/admin/outbox/{oid}/requeue").status_code == 204
    assert all(d["id"] != oid for d in admin.get("/api/admin/outbox").json()["dead"])  # no longer dead (same tx as the API)
    assert admin.post(f"/api/admin/outbox/{oid}/requeue").status_code == 422  # no longer dead
    assert admin.get("/api/admin/audit-logs", params={"action": "outbox_requeue"}).json()["total"] >= 1
