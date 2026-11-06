import os
import tempfile

TEST_DB = os.environ.get("MINTREE_TEST_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_test")
os.environ["MINTREE_DATABASE_URL"] = TEST_DB
os.environ["MINTREE_ENVIRONMENT"] = "test"
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
