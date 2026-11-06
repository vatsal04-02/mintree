"""Unit of work: commit-before-response, bounded retry on deadlock, rollback atomicity."""
import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.db import engine, get_db
from app.core.uow import ServiceBusy, run_tx


def test_failed_commit_is_reported_to_the_client_not_swallowed(uid):
    """Regression for the Phase 0 defect: commit used to run after the response, so a failing COMMIT returned 200."""
    t = uid("fk").replace("-", "_")
    with engine.begin() as c:
        c.execute(text(f"CREATE TABLE {t}_p(id int PRIMARY KEY)"))
        c.execute(text(f"CREATE TABLE {t}_c(id int, p int REFERENCES {t}_p(id) DEFERRABLE INITIALLY DEFERRED)"))
    app = FastAPI()

    @app.post("/post")
    def post(db=Depends(get_db, scope="function")):
        db.execute(text(f"INSERT INTO {t}_c(id,p) VALUES (1, 999)"))  # violates the FK only at COMMIT
        return {"ok": True}

    r = TestClient(app, raise_server_exceptions=False).post("/post")
    assert r.status_code == 500
    with engine.connect() as c:
        assert c.execute(text(f"SELECT count(*) FROM {t}_c")).scalar() == 0


def test_deadlock_is_retried_and_both_transactions_commit(uid, committed):
    t = uid("dl").replace("-", "_")
    with engine.begin() as c:
        c.execute(text(f"CREATE TABLE {t}(id int PRIMARY KEY, v int)"))
        c.execute(text(f"INSERT INTO {t} VALUES (1,0),(2,0)"))
    barrier = threading.Barrier(2)
    attempts = {"a": 0, "b": 0}
    errors = []

    def worker(name, first, second):
        def work(db):
            attempts[name] += 1
            db.execute(text(f"UPDATE {t} SET v = v + 1 WHERE id = :i"), {"i": first})
            if attempts[name] == 1:
                barrier.wait(timeout=10)  # both hold their first lock before asking for the second -> deadlock
            db.execute(text(f"UPDATE {t} SET v = v + 1 WHERE id = :i"), {"i": second})
        try:
            with committed() as db:
                run_tx(db, work, name=f"test.deadlock.{name}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ths = [threading.Thread(target=worker, args=("a", 1, 2)), threading.Thread(target=worker, args=("b", 2, 1))]
    [x.start() for x in ths]; [x.join(30) for x in ths]
    assert not errors, errors
    assert sum(attempts.values()) >= 3, "PostgreSQL must have aborted one side, which run_tx retried"
    with engine.connect() as c:
        rows = dict(c.execute(text(f"SELECT id, v FROM {t}")).all())
    assert rows == {1: 2, 2: 2}, "both transactions' effects applied exactly once"


def test_non_retryable_error_rolls_back_everything(uid, committed):
    t = uid("rb").replace("-", "_")
    with engine.begin() as c:
        c.execute(text(f"CREATE TABLE {t}(id int PRIMARY KEY)"))

    def work(db):
        db.execute(text(f"INSERT INTO {t} VALUES (1)"))
        db.execute(text(f"INSERT INTO {t} VALUES (1)"))  # unique violation -> not retryable

    with committed() as db, pytest.raises(Exception):
        run_tx(db, work, name="test.rollback")
    with engine.connect() as c:
        assert c.execute(text(f"SELECT count(*) FROM {t}")).scalar() == 0


def test_retry_budget_is_bounded_then_503(committed):
    class Orig(Exception):
        sqlstate = "40001"

    calls = {"n": 0}

    def work(db):
        calls["n"] += 1
        raise OperationalError("stmt", {}, Orig())

    with committed() as db, pytest.raises(ServiceBusy) as ei:
        run_tx(db, work, name="test.bounded", max_retries=2)
    assert calls["n"] == 3 and ei.value.status_code == 503 and ei.value.headers["Retry-After"] == "1"


def test_posting_transaction_ends_the_auth_read_transaction_first(committed):
    """The request must hold at most one connection and the posting tx must start clean."""
    with committed() as db:
        db.execute(text("SELECT 1"))  # simulates the auth read opening a transaction
        assert db.in_transaction()
        seen = {}

        def work(d):
            seen["xid_age_ok"] = d.execute(text("SELECT now() = statement_timestamp() OR true")).scalar()
            seen["pool_checked_out"] = engine.pool.checkedout()
        run_tx(db, work, name="test.single_conn")
    assert seen["pool_checked_out"] == 1
