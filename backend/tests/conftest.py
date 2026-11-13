import os
import tempfile

TEST_DB = os.environ.get("MINTREE_TEST_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_test")
os.environ["MINTREE_DATABASE_URL"] = TEST_DB
os.environ["MINTREE_ENVIRONMENT"] = "test"
# Tests must not depend on a developer's local .env (which may enable the AI layer or hold keys); env vars beat .env in pydantic-settings.
os.environ["MINTREE_AI_ENABLED"] = "false"
os.environ["MINTREE_AI_API_KEY"] = ""
os.environ["MINTREE_AI_PROVIDER"] = "anthropic"
os.environ["MINTREE_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="mintree-test-storage-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.administration.models import Role, User  # noqa: E402
from app.core.db import engine, get_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

PASSWORD = "Str0ng-Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the schema from the real Alembic migrations (so migrations are tested), then seed once."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    with Session(engine) as s:
        seed(s)
        s.commit()
    yield


@pytest.fixture(autouse=True)
def _test_hygiene():
    """(1) The response cache is process-wide; a test must never see another test's cached list or dashboard (cache behaviour has its own tests in test_response_cache.py).
    (2) No publisher runs during the suite, so outbox events committed by earlier tests (live-server tests commit for real) would age past the integrity check's
    five-minute 'stuck unpublished' limit in a long run and fail an unrelated later test. Debris older than three minutes is marked published (never deleted: other checks require every
    goods receipt to keep its event); events from the running test are untouched."""
    from app.core import rcache
    rcache.clear()
    with engine.begin() as c:
        c.execute(text("UPDATE outbox_events SET status = 'published', published_at = now() WHERE status IN ('pending', 'publishing') AND created_at < now() - interval '3 minutes'"))
    yield


@pytest.fixture
def db():
    """Each test runs inside an outer transaction that is rolled back; app commits become savepoints."""
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False)

    def override():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_db] = override
    yield session
    app.dependency_overrides.clear()
    session.close()
    outer.rollback()
    conn.close()


@pytest.fixture
def make_user(db):
    n = {"i": 0}

    def _make(*role_codes: str, email: str | None = None, password: str = PASSWORD, **kw) -> User:
        n["i"] += 1
        roles = list(db.scalars(select(Role).where(Role.code.in_(role_codes)))) if role_codes else []
        u = User(email=email or f"user{n['i']}@example.com", full_name=f"Test User {n['i']}",
                 password_hash=hash_password(password), roles=roles, **kw)
        db.add(u)
        db.flush()
        return u

    return _make


def new_client(email: str, password: str = PASSWORD) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


@pytest.fixture
def login(make_user):
    def _login(*role_codes: str, **kw) -> TestClient:
        u = make_user(*role_codes, **kw)
        c = new_client(u.email)
        c.user = u  # type: ignore[attr-defined]
        return c

    return _login


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def admin(login):
    return login("system_admin")


# ---- helpers for tests that need REAL commits / threads (the `db` fixture rolls everything back) ----
import uuid as _uuid  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402


@pytest.fixture
def committed():
    """Factory for independent, really-committing sessions. Tests must use unique keys (the DB is rebuilt per run)."""
    return SessionLocal


@pytest.fixture
def uid():
    return lambda prefix="t": f"{prefix}-{_uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------------------------------------------
# Live multi-process server for REAL concurrency tests (HTTP + threads + separate OS processes).
# ---------------------------------------------------------------------------------------------------------------
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

LIVE_PORT = 8211


@pytest.fixture(scope="session")
def live_server(_schema):
    env = dict(os.environ, MINTREE_DATABASE_URL=TEST_DB, MINTREE_ENVIRONMENT="test",
               PROMETHEUS_MULTIPROC_DIR=tempfile.mkdtemp(prefix="prom-test-"), PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(LIVE_PORT), "--workers", "2",
                             "--no-access-log", "--log-level", "warning"], env=env, start_new_session=(os.name != "nt"),
                            stdout=subprocess.DEVNULL, stderr=open(Path(tempfile.gettempdir()) / "mintree-live-server.log", "w"))
    base = f"http://127.0.0.1:{LIVE_PORT}"
    for _ in range(60):
        try:
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("live server failed to start")
    yield base
    if os.name == "nt":                                   # no process groups / killpg on Windows: kill the whole tree (uvicorn --workers spawns children)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        return
    import os as _os, signal as _sig
    _os.killpg(proc.pid, _sig.SIGTERM)
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        _os.killpg(proc.pid, _sig.SIGKILL)


class Api:
    """Thin httpx client with session cookie + CSRF, one per thread."""

    def __init__(self, base: str, email: str, password: str = PASSWORD):
        self.c = httpx.Client(base_url=base, timeout=30)
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        self.user_id = r.json()["id"]

    def get(self, url, **kw): return self.c.get(url, **kw)
    def post(self, url, json=None, key=None, **kw):
        h = {"Idempotency-Key": key} if key else {}
        return self.c.post(url, json=json, headers=h, **kw)


@pytest.fixture
def live_users(live_server):
    """Factory: committed users with given roles, logged in against the live server."""
    from sqlalchemy.orm import Session as S

    def make(*roles: str, n: int = 1) -> list[Api]:
        out = []
        with S(engine) as s:
            rs = list(s.scalars(select(Role).where(Role.code.in_(roles))))
            emails = []
            for _ in range(n):
                u = User(email=f"live-{_uuid.uuid4().hex[:10]}@example.com", full_name="Live", password_hash=_HASH, roles=rs)
                s.add(u)
                emails.append(u.email)
            s.commit()
        return [Api(live_server, e) for e in emails]
    return make


_HASH = hash_password(PASSWORD)


def mk_master(prefix: str, *, shelf_life: int | None = 365, batch_tracked: bool = True) -> dict:
    """Committed master data for one test (unique codes): supplier (approved), item, warehouses. Returns ids."""
    from sqlalchemy.orm import Session as S
    from app.masters.models import Item, Supplier, UnitOfMeasure, Warehouse
    tag = _uuid.uuid4().hex[:8].upper()
    with S(engine) as s:
        uom = s.scalar(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG"))
        sup = Supplier(code=f"S-{tag}", name=f"Supplier {tag}", approval_status="APPROVED", category="RM_PM")
        item = Item(item_code=f"{prefix}-{tag}", name=f"Item {tag}", kind="RM", uom_id=uom.id, shelf_life_days=shelf_life,
                    is_batch_tracked=batch_tracked, gst_rate=18)
        w1 = Warehouse(code=f"W1-{tag}", name="W1", kind="RM")
        w2 = Warehouse(code=f"W2-{tag}", name="W2", kind="RM")
        s.add_all([sup, item, w1, w2])
        s.commit()
        return {"supplier_id": sup.id, "item_id": item.id, "wh1": w1.id, "wh2": w2.id, "tag": tag}


# ---- test execution register: every full pytest run appends a record (see docs/TEST_EXECUTION_REGISTER.md) ----
_T0 = {"t": None}


def pytest_sessionstart(session):
    _T0["t"] = time.time()


def pytest_sessionfinish(session, exitstatus):
    try:
        _record_run(session, exitstatus)
    except Exception as e:  # the register must never break or mask a test run
        print(f"\n[test register] not recorded: {type(e).__name__}: {e}")


def _record_run(session, exitstatus):
    import json, subprocess as sp, datetime as dt
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None or session.config.getoption("collectonly", False):
        return
    stats = {k: len(v) for k, v in tr.stats.items() if k in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")}
    total = sum(stats.values())
    selected = [a for a in session.config.invocation_params.args if not str(a).startswith("-")]
    ksel = any(str(a) in ("-k", "-m") or str(a).startswith(("-k", "-m")) for a in session.config.invocation_params.args)
    if selected or ksel or total < 20:   # only whole-suite runs are registered; targeted runs are not
        return
    root = Path(__file__).resolve().parents[2]
    git = lambda *a: sp.run(["git", *a], capture_output=True, text=True, cwd=root).stdout.strip()
    try:
        pg = sp.run(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "-tAc", "show server_version"], capture_output=True, text=True).stdout.strip()
    except Exception:
        pg = None
    rec = {"kind": "backend (pytest)", "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "commit": git("rev-parse", "--short=10", "HEAD"),
           "dirty": bool(git("status", "--porcelain")), "result": "PASS" if exitstatus == 0 else "FAIL", **stats,
           "duration_s": round(time.time() - (_T0["t"] or time.time()), 1), "environment": f"PostgreSQL {pg}, Python {sys.version.split()[0]}, RabbitMQ local, 4 vCPU sandbox"}
    with open(root / "docs" / "test-runs.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
