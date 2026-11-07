"""The demo system, end to end, through the real CLI in subprocesses against throwaway databases:
guards refuse unsafe targets, reset -> init -> verify works, a second init changes nothing, and verify FAILS when the data is tampered with."""
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from scripts.demo import guard
from tests.conftest import TEST_DB

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ pure guard rules (fast)
@pytest.mark.parametrize("env,url,ok", [
    ("demo", "postgresql+psycopg://u:p@localhost:5433/mintree_demo", True),
    ("demo", "postgresql+psycopg://u:p@localhost:5433/demo", True),
    ("demo", "postgresql+psycopg://u:p@localhost:5433/mintree-demo-2", True),
    ("production", "postgresql+psycopg://u:p@localhost:5433/mintree_demo", False),
    ("development", "postgresql+psycopg://u:p@localhost:5433/mintree_demo", False),      # environment must be exactly 'demo'
    ("test", "postgresql+psycopg://u:p@localhost:5433/mintree_demo", False),
    ("demo", "postgresql+psycopg://u:p@localhost:5433/mintree", False),                   # not a demo database name
    ("demo", "postgresql+psycopg://u:p@localhost:5433/mintree_test", False),
    ("demo", "postgresql+psycopg://u:p@localhost:5433/mintree_demonstration", False),     # 'demo' must be a whole word, not a substring
    ("demo", "postgresql+psycopg://u:p@localhost:5433/postgres", False),
])
def test_demo_target_guard(env, url, ok):
    if ok:
        assert guard.check_demo_target(env, url)
    else:
        with pytest.raises(SystemExit, match="REFUSED"):
            guard.check_demo_target(env, url)


def test_reset_guard_needs_every_condition():
    url = "postgresql+psycopg://u:p@localhost:5433/mintree_demo"
    good = dict(has_marker=True, is_empty=False)
    assert guard.check_reset("demo", url, "mintree_demo", **good) == "mintree_demo"
    assert guard.check_reset("demo", url, "mintree_demo", has_marker=False, is_empty=True)                   # an empty database may be reset
    for confirm in (None, "", "mintree", "MINTREE_DEMO2", "yes"):
        with pytest.raises(SystemExit, match="repeat the database name"):
            guard.check_reset("demo", url, confirm, **good)
    with pytest.raises(SystemExit, match="no demo marker"):                                                   # a demo-named database holding someone's data
        guard.check_reset("demo", url, "mintree_demo", has_marker=False, is_empty=False)
    with pytest.raises(SystemExit, match="not local"):
        guard.check_reset("demo", "postgresql+psycopg://u:p@prod-db.example.com:5432/mintree_demo", "mintree_demo", **good)
    assert guard.check_reset("demo", "postgresql+psycopg://u:p@prod-db.example.com:5432/mintree_demo", "mintree_demo", allow_remote=True, **good)
    with pytest.raises(SystemExit, match="production"):
        guard.check_reset("production", url, "mintree_demo", **good)


# ------------------------------------------------------------------ the real CLI
def _admin_url() -> str:
    return TEST_DB.rsplit("/", 1)[0] + "/postgres"


def _db_url(name: str) -> str:
    return TEST_DB.rsplit("/", 1)[0] + "/" + name


def _psql(db_url: str, sql: str) -> str:
    import psycopg
    with psycopg.connect(db_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as c:
        cur = c.execute(sql)
        return str(cur.fetchone()[0]) if cur.description else ""


def cli(db: str, *args: str, env: str = "demo") -> subprocess.CompletedProcess:
    e = dict(os.environ, MINTREE_ENVIRONMENT=env, MINTREE_DATABASE_URL=_db_url(db), MINTREE_STORAGE_ROOT=tempfile.mkdtemp(prefix="demo-storage-"), PYTHONPATH=str(BACKEND))
    e.pop("MINTREE_TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "scripts.demo_data", *args], cwd=BACKEND, env=e, capture_output=True, text=True, timeout=900)


@pytest.fixture
def scratch_dbs():
    made = []

    def make(name: str) -> str:
        _psql(_admin_url(), f'DROP DATABASE IF EXISTS "{name}"')
        _psql(_admin_url(), f'CREATE DATABASE "{name}"')
        made.append(name)
        return name
    yield make
    for n in made:
        try:
            _psql(_admin_url(), f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')
        except Exception:
            pass


def test_cli_refuses_unsafe_targets_and_touches_nothing(scratch_dbs):
    # 1) the shared test database: wrong name -> refused, still intact
    before = _psql(TEST_DB, "SELECT count(*) FROM users")
    for args in (["init"], ["verify"], ["reset", "--confirm-reset-demo-database", "mintree_test"]):
        r = cli("mintree_test", *args)
        assert r.returncode != 0 and "REFUSED" in (r.stdout + r.stderr), (args, r.stdout[-300:], r.stderr[-300:])
    assert _psql(TEST_DB, "SELECT count(*) FROM users") == before
    # 2) right database name but the environment is not 'demo' (and not 'production' either)
    db = scratch_dbs(f"mintree_demo_{uuid.uuid4().hex[:6]}")
    for env in ("development", "production", "test"):
        assert "REFUSED" in cli(db, "init", env=env).stdout + cli(db, "init", env=env).stderr
    # 3) a demo-named database that holds foreign data and no marker: reset refuses and the data survives
    foreign = scratch_dbs(f"mintree_demo_foreign_{uuid.uuid4().hex[:6]}")
    _psql(_db_url(foreign), "CREATE TABLE precious(id int)")
    _psql(_db_url(foreign), "INSERT INTO precious VALUES (1)")
    r = cli(foreign, "reset", "--confirm-reset-demo-database", foreign)
    assert r.returncode != 0 and "no demo marker" in (r.stdout + r.stderr)
    assert _psql(_db_url(foreign), "SELECT count(*) FROM precious") == "1"
    # 4) reset without the typed confirmation
    r = cli(db, "reset")
    assert r.returncode != 0 and "repeat the database name" in (r.stdout + r.stderr)


def test_reset_init_verify_idempotent_and_tamper_evident(scratch_dbs):
    db = scratch_dbs(f"mintree_demo_{uuid.uuid4().hex[:6]}")
    url = _db_url(db)
    r = cli(db, "reset", "--confirm-reset-demo-database", db)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    r = cli(db, "init")
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert "Verification: " in r.stdout and "checks pass" in r.stdout and "FAIL" not in r.stdout
    assert '"logger"' not in r.stdout + r.stderr, "the CLI must not leak the application's JSON request logs"
    counts = "SELECT concat_ws(',',(SELECT count(*) FROM users),(SELECT count(*) FROM purchase_orders),(SELECT count(*) FROM production_orders),(SELECT count(*) FROM sales_orders),(SELECT count(*) FROM shipments)," \
             "(SELECT count(*) FROM journal_entries),(SELECT count(*) FROM stock_ledger),(SELECT count(*) FROM audit_logs WHERE action <> 'login'),(SELECT count(*) FROM external_orders),(SELECT count(*) FROM opportunities),(SELECT count(*) FROM items))"
    first = _psql(url, counts)
    r2 = cli(db, "init")
    assert r2.returncode == 0 and "0 step(s) ran" in r2.stdout
    assert _psql(url, counts) == first, "a second init must not create anything"
    assert cli(db, "verify").returncode == 0
    rep = cli(db, "report")
    assert rep.returncode == 0 and "NOT MET" not in rep.stdout, rep.stdout[-1500:]        # every coverage target (100+ sales orders, 100+ marketplace orders, 30+ Meta records, ...) is met
    assert "[target 100: met]" in rep.stdout
    assert "[x]" in cli(db, "status").stdout and "[ ]" not in cli(db, "status").stdout
    # the verifier must be able to FAIL: break the ledger/balance agreement, then an invoice total
    _psql(url, "UPDATE stock_balances SET qty_physical = qty_physical + 1 WHERE id = (SELECT min(id) FROM stock_balances WHERE qty_physical > 0)")
    bad = cli(db, "verify")
    assert bad.returncode == 1 and "FAIL" in bad.stdout
    # and reset really wipes: afterwards the marker and every step are gone
    assert cli(db, "reset", "--confirm-reset-demo-database", db).returncode == 0
    assert _psql(url, "SELECT count(*) FROM demo_seed_steps") == "0" and _psql(url, "SELECT count(*) FROM purchase_orders") == "0"
