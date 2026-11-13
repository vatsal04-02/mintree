"""Golden snapshot, instant reset and date re-anchoring for the DEMO database. Same safety rules as the demo seeder (scripts.demo.guard):
environment exactly 'demo' and a database name containing the word 'demo'. Nothing here can touch any other database.

snapshot  CREATE DATABASE <db>_golden TEMPLATE <db>      (a file-level copy: seconds, not minutes)
reset     DROP DATABASE <db> WITH (FORCE); CREATE DATABASE <db> TEMPLATE <db>_golden; keep signed-in sessions and the live controls; re-anchor dates; clear caches
anchor    shift every business date forward by (today - anchor_date) so the newest data is "today" and the 12-month history ends today"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

import psycopg
import psycopg.rows
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import get_settings

log = logging.getLogger("mintree.demo_ops")

# Tables whose dates are definitions, not history (periods, calendars of the system itself) or are session/transient control data.
NO_SHIFT = {"demo_scenario", "accounting_periods", "sessions", "idempotency_keys", "alembic_version", "demo_live_state", "demo_live_events", "demo_live_triggers", "demo_seed_steps",
            "outbox_events", "processed_events", "job_failures", "users", "roles", "permissions", "role_permissions", "user_roles", "company_settings",
            "units_of_measure", "unit_conversions", "tax_classes", "departments", "payment_methods", "lead_sources", "product_categories", "brands"}


def _target() -> tuple[str, dict]:
    from scripts.demo.guard import check_demo_target
    s = get_settings()
    name = check_demo_target(s.environment, s.database_url)
    u = make_url(s.database_url)
    return name, {"host": u.host or "localhost", "port": u.port or 5432, "user": u.username, "password": u.password}


def golden_name(db: str) -> str:
    return f"{db}_golden"


def _admin(conn_args: dict) -> psycopg.Connection:
    return psycopg.connect(dbname="postgres", autocommit=True, **conn_args)


def _exists(c: psycopg.Connection, name: str) -> bool:
    return c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None


def snapshot(note: str = "") -> dict:
    """Take the golden snapshot of the current demo database (replacing an older one). Briefly disconnects other sessions of the demo database."""
    db, args = _target()
    g = golden_name(db)
    t0 = time.perf_counter()
    with _admin(args) as c:
        anchor_today(db, args)                                                  # record "the data is anchored to today" inside the snapshot
        if _exists(c, g):
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(g)))
        for attempt in range(5):                                                # a template must have no other connections
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (db,))
            try:
                c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(g), sql.Identifier(db)))
                break
            except psycopg.errors.ObjectInUse:
                time.sleep(0.5)
        else:
            raise RuntimeError("could not take the snapshot: the database stayed busy. Stop the API/worker and retry.")
    return {"snapshot": g, "seconds": round(time.perf_counter() - t0, 1), "note": note}


def has_snapshot() -> bool:
    db, args = _target()
    with _admin(args) as c:
        return _exists(c, golden_name(db))


def reset() -> dict:
    """Restore the golden snapshot in place. Signed-in sessions and the live controls survive, so the presenter is not logged out and Live Mode keeps its settings."""
    db, args = _target()
    g = golden_name(db)
    t0 = time.perf_counter()
    keep_sessions: list[dict] = []
    keep_state: dict = {}
    with psycopg.connect(dbname=db, row_factory=psycopg.rows.dict_row, **args) as src:
        keep_sessions = src.execute("SELECT * FROM sessions WHERE revoked_at IS NULL AND expires_at > now()").fetchall()
        try:
            keep_state = src.execute("SELECT enabled, paused, speed FROM demo_live_state WHERE id = 1").fetchone() or {}
        except psycopg.Error:
            keep_state = {}
    with _admin(args) as c:
        if not _exists(c, g):
            raise RuntimeError(f"no golden snapshot named '{g}': run `python -m scripts.demo_ops snapshot` first")
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
        c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(db), sql.Identifier(g)))
    with psycopg.connect(dbname=db, **args) as conn:
        conn.execute("DELETE FROM sessions")
        for row in keep_sessions:
            cols = list(row)
            conn.execute(sql.SQL("INSERT INTO sessions ({}) VALUES ({})").format(sql.SQL(", ").join(map(sql.Identifier, cols)), sql.SQL(", ").join(sql.Placeholder() * len(cols))),
                         [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()])
        conn.execute("SELECT setval(pg_get_serial_sequence('sessions', 'id'), GREATEST((SELECT COALESCE(max(id), 1) FROM sessions), 1))")      # kept sessions carry their old ids
        if keep_state:
            conn.execute("UPDATE demo_live_state SET enabled = %s, paused = %s, speed = %s, counters = '{}', heartbeat_at = NULL WHERE id = 1", (keep_state["enabled"], keep_state["paused"], keep_state["speed"]))
        conn.commit()
    shifted = anchor_today(db, args)
    _flush_caches()
    _prewarm(db, args)
    return {"restored_from": g, "seconds": round(time.perf_counter() - t0, 1), "sessions_kept": len(keep_sessions), **shifted}


def _prewarm(db: str, args: dict) -> None:
    """A database freshly created from the template starts with an empty buffer cache: the first requests after a reset ran for a minute. Read the big tables and their
    indexes once (a few seconds) so the first click is as fast as the hundredth."""
    with psycopg.connect(dbname=db, autocommit=True, **args) as c:
        tables = [r[0] for r in c.execute("SELECT relname FROM pg_stat_user_tables WHERE n_live_tup > 500 ORDER BY n_live_tup DESC LIMIT 40")]
        for t in tables:
            c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
            for (ix,) in c.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = %s::regclass", (t,)).fetchall():
                c.execute("SELECT count(*) FROM (SELECT 1 FROM pg_class WHERE oid = %s::regclass) x", (ix,))          # touches the index metadata
        c.execute("ANALYZE")


def _flush_caches() -> None:
    try:
        import redis
        redis.Redis.from_url(get_settings().redis_url, socket_timeout=2).flushdb()      # response cache, session cache, rate-limit counters: all rebuildable
    except Exception as e:  # noqa: BLE001
        log.warning("cache flush skipped: %s", e)


def anchor_today(db: str | None = None, args: dict | None = None) -> dict:
    """Shift history so the newest data is today. The anchor date lives in demo_live_state; first call just records today (data was seeded today)."""
    if db is None or args is None:
        db, args = _target()
    today = _business_today()
    with psycopg.connect(dbname=db, **args) as conn:
        row = conn.execute("SELECT anchor_date FROM demo_live_state WHERE id = 1").fetchone()
        anchor: date | None = row[0] if row else None
        if anchor is None:
            conn.execute("UPDATE demo_live_state SET anchor_date = %s WHERE id = 1", (today,))
            conn.commit()
            return {"anchored_to": str(today), "shifted_days": 0, "columns": 0}
        days = (today - anchor).days
        if days <= 0:
            return {"anchored_to": str(anchor), "shifted_days": 0, "columns": 0}
        cols = conn.execute("""SELECT c.table_name, c.column_name, c.data_type FROM information_schema.columns c JOIN information_schema.tables t
                                 ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
                                WHERE c.table_schema = 'public' AND c.data_type IN ('date', 'timestamp with time zone', 'timestamp without time zone')
                                ORDER BY 1, 2""").fetchall()
        by_table: dict[str, list[tuple[str, str]]] = {}
        for t, col, typ in cols:
            if t not in NO_SHIFT:
                by_table.setdefault(t, []).append((col, typ))
        conn.execute("SET LOCAL session_replication_role = replica")             # append-only guards and FK re-checks off: every date moves by the same amount, so order is preserved
        n = 0
        def shift(t: str, cs: list[tuple[str, str]], d: int) -> None:
            sets = sql.SQL(", ").join(sql.SQL("{c} = {c} + {d}").format(c=sql.Identifier(c), d=sql.Literal(d) if typ == "date" else sql.SQL("make_interval(days => {})").format(sql.Literal(d))) for c, typ in cs)
            conn.execute(sql.SQL("UPDATE {} SET {}").format(sql.Identifier(t), sets))

        n = 0
        for t, cs in by_table.items():
            try:
                with conn.transaction():                                            # a savepoint: one table failing must not abort the rest
                    shift(t, cs, days)
            except psycopg.errors.UniqueViolation:
                # moving rows by a few days onto each other (a daily series with a unique day) collides while the update is half done: park every row far away first, then bring it home
                shift(t, cs, days + 200000)
                shift(t, cs, -200000)
            n += len(cs)
        conn.execute("UPDATE demo_live_state SET anchor_date = %s WHERE id = 1", (today,))
        conn.commit()
    return {"anchored_to": str(today), "shifted_days": days, "columns": n}


def _business_today() -> date:
    from app.core.clock import business_today
    return business_today()


# --------------------------------------------------------------------------------------------------------------------------- history fidelity
IST = "Asia/Kolkata"
_D = lambda col: f"(({col}) AT TIME ZONE '{IST}')::date"          # noqa: E731  the business date of a timestamp
_TODAY = f"(now() AT TIME ZONE '{IST}')::date"
# (table, column, joins, target business date). A timestamp is only ever moved EARLIER (never forward), by whole days, keeping its time of day.
BACKDATE: list[tuple[str, str, str, str]] = [
    ("purchase_orders", "created_at", "", "t.expected_date - 7"),
    ("purchase_orders", "updated_at", "", "t.expected_date - 7"),
    ("purchase_orders", "approved_at", "", "t.expected_date - 7"),
    ("goods_receipts", "received_at", "JOIN purchase_orders po ON po.id = t.po_id", f"LEAST({_D('po.created_at')} + 3, {_TODAY})"),
    ("production_orders", "created_at", "", "t.planned_date"),
    ("production_orders", "started_at", "", "t.planned_date"),
    ("production_orders", "completed_at", "", "t.planned_date"),
    ("production_stages", "started_at", "JOIN production_orders p ON p.id = t.order_id", _D("p.started_at")),
    ("production_stages", "completed_at", "JOIN production_orders p ON p.id = t.order_id", _D("p.started_at")),
    ("process_checks", "recorded_at", "JOIN production_orders p ON p.id = t.order_id", _D("p.started_at")),
    ("inventory_batches", "created_at", "JOIN goods_receipts g ON g.id = t.source_id AND t.source_type = 'goods_receipt'", _D("g.received_at")),
    ("inventory_batches", "created_at", "JOIN production_orders p ON p.id = t.source_id AND t.source_type = 'production_order'", _D("p.completed_at")),
    ("inspections", "created_at", "JOIN inventory_batches b ON b.id = t.batch_id", _D("b.created_at")),
    ("inspections", "decided_at", "JOIN inventory_batches b ON b.id = t.batch_id", _D("b.created_at")),
    ("inspection_results", "tested_at", "JOIN inspections i ON i.id = t.inspection_id", _D("i.created_at")),
    ("sales_invoices", "created_at", "", "t.invoice_date"),
    ("shipments", "created_at", "JOIN sales_invoices i ON i.shipment_id = t.id", "i.invoice_date"),
    ("shipments", "delivered_at", "JOIN sales_invoices i ON i.shipment_id = t.id", f"LEAST(i.invoice_date + 3, {_TODAY})"),
    ("sales_orders", "created_at", "", f"COALESCE((SELECT min(e.order_date::date) FROM external_orders e WHERE e.sales_order_id = t.id), (SELECT min(i.invoice_date) - 2 FROM sales_invoices i WHERE i.so_id = t.id))"),
    ("stock_ledger", "posted_at", "JOIN shipments sh ON sh.id = t.source_id AND t.source_type = 'shipment'", _D("sh.created_at")),
    ("stock_ledger", "posted_at", "JOIN goods_receipts g ON g.id = t.source_id AND t.source_type = 'goods_receipt'", _D("g.received_at")),
    ("stock_ledger", "posted_at", "JOIN production_orders p ON p.id = t.source_id AND t.source_type = 'production_order'", f"{_D('CASE WHEN t.qty_delta < 0 THEN p.started_at ELSE p.completed_at END')}"),
    ("payments", "created_at", "", "t.payment_date"),
    ("journal_entries", "created_at", "", "t.entry_date"),
]


def backdate() -> dict:
    """Server-stamped timestamps (created_at, posted_at, received_at ...) are 'now' for rows the seeders create in a hurry; the business dates (invoice_date, planned_date, entry_date ...) are
    correctly spread over 12 months. This derives the former from the latter so charts built on timestamps show the same history. Idempotent; only moves timestamps earlier."""
    db, args = _target()
    moved: dict[str, int] = {}
    with psycopg.connect(dbname=db, **args) as conn:
        conn.execute("SET LOCAL session_replication_role = replica")                 # append-only guards (ledger, journals) off for this one-time repair of the demo clock
        for table, col, joins, target in BACKDATE:
            q = (f"UPDATE {table} u SET {col} = u.{col} - make_interval(days => s.n::int) FROM "
                 f"(SELECT t.id, ({_D(f't.{col}')} - ({target})::date) AS n FROM {table} t {joins}) s WHERE s.id = u.id AND s.n > 0 AND u.{col} IS NOT NULL")
            cur = conn.execute(q)
            moved[f"{table}.{col}"] = moved.get(f"{table}.{col}", 0) + cur.rowcount
        conn.commit()
    return {"rows_moved": moved, "total": sum(moved.values())}
