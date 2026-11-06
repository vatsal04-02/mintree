import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core.db import engine
from app.core.idempotency import IdempotencyConflict, idempotent, require_idempotency_key
from app.core.platform_models import IdempotencyKey
from app.core.uow import run_tx


def post(committed, scope, key, payload, effect):
    with committed() as db:
        return run_tx(db, lambda d: idempotent(d, scope=scope, key=key, actor_id=None, payload=payload, effect=effect),
                      name="test.idem")


def test_replay_returns_stored_response_and_runs_effect_once(committed, uid):
    scope, key, n = uid("scope"), uid("key"), {"n": 0}

    def effect():
        n["n"] += 1
        return 201, {"id": 42, "number": "GR-1"}, "goods_receipt", "42"

    a = post(committed, scope, key, {"po": 1}, effect)
    b = post(committed, scope, key, {"po": 1}, effect)
    assert (a.status, a.body, a.replayed) == (201, {"id": 42, "number": "GR-1"}, False)
    assert (b.status, b.body, b.replayed) == (201, {"id": 42, "number": "GR-1"}, True)
    assert n["n"] == 1


def test_same_key_different_payload_is_a_clear_conflict(committed, uid):
    scope, key = uid("scope"), uid("key")
    post(committed, scope, key, {"qty": 10}, lambda: (201, {"ok": 1}, None, None))
    with pytest.raises(IdempotencyConflict) as ei:
        post(committed, scope, key, {"qty": 11}, lambda: (201, {"ok": 2}, None, None))
    assert ei.value.status_code == 409 and ei.value.detail["code"] == "idempotency_key_reuse"


def test_payload_hash_ignores_key_order(committed, uid):
    scope, key = uid("scope"), uid("key")
    post(committed, scope, key, {"a": 1, "b": [1, 2]}, lambda: (201, {"v": 1}, None, None))
    r = post(committed, scope, key, {"b": [1, 2], "a": 1}, lambda: (201, {"v": 2}, None, None))
    assert r.replayed and r.body == {"v": 1}


def test_concurrent_duplicates_have_exactly_one_effect(committed, uid):
    """16 simultaneous requests with the same key: the unique index serialises them; one effect, 15 replays."""
    scope, key, lock, n, results, errs = uid("scope"), uid("key"), threading.Lock(), {"n": 0}, [], []
    start = threading.Barrier(16)

    def effect():
        with lock:
            n["n"] += 1
        return 201, {"id": 7}, "x", "7"

    def go():
        try:
            start.wait(10)
            results.append(post(committed, scope, key, {"p": 1}, effect))
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ths = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ths]; [t.join(30) for t in ths]
    assert not errs, errs
    assert n["n"] == 1 and len(results) == 16
    assert sum(1 for r in results if not r.replayed) == 1 and all(r.body == {"id": 7} for r in results)
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(IdempotencyKey).where(IdempotencyKey.key == key)).scalar() == 1


def test_failed_first_attempt_leaves_no_key_so_retry_can_proceed(committed, uid):
    scope, key = uid("scope"), uid("key")

    def boom():
        raise RuntimeError("business rule failed")

    with pytest.raises(RuntimeError):
        post(committed, scope, key, {"p": 1}, boom)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM idempotency_keys WHERE key=:k"), {"k": key}).scalar() == 0
    ok = post(committed, scope, key, {"p": 1}, lambda: (201, {"done": True}, None, None))
    assert not ok.replayed


def test_header_is_required_and_validated():
    app = FastAPI()

    @app.post("/x")
    def x(key: str = Depends(require_idempotency_key)):
        return {"key": key}

    c = TestClient(app)
    assert c.post("/x").status_code == 400
    assert c.post("/x", headers={"Idempotency-Key": "short"}).status_code == 400
    assert c.post("/x", headers={"Idempotency-Key": "has spaces in it!"}).status_code == 400
    assert c.post("/x", headers={"Idempotency-Key": "order-2026-11-06-0001"}).json() == {"key": "order-2026-11-06-0001"}
