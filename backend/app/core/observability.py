"""Metrics: HTTP, admission queue, DB pool/queries/transactions, PostgreSQL server stats.

Works with several uvicorn workers via prometheus_client multiprocess mode (set PROMETHEUS_MULTIPROC_DIR
*before* the process starts; the runner scripts do). Scraping /api/metrics aggregates all workers.
"""
import logging
import os
import re
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, multiprocess
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.core.config import get_settings
from app.core.context import RequestStats, stats_var

log = logging.getLogger("mintree.perf")

LAT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30)

HTTP_REQUESTS = Counter("mintree_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("mintree_http_request_duration_seconds", "End-to-end latency incl. admission wait",
                         ["method", "route", "status_class"], buckets=LAT_BUCKETS)
HTTP_IN_FLIGHT = Gauge("mintree_http_in_flight", "Requests admitted and running", multiprocess_mode="livesum")
ADMISSION_WAITING = Gauge("mintree_admission_waiting", "Requests queued for an admission slot", multiprocess_mode="livesum")
ADMISSION_WAIT = Histogram("mintree_admission_wait_seconds", "Time spent waiting for an admission slot", buckets=LAT_BUCKETS)
ADMISSION_SHED = Counter("mintree_admission_shed_total", "Requests rejected (503) because the wait exceeded the limit")

DB_QUERY = Histogram("mintree_db_query_duration_seconds", "DB statement duration", ["op"], buckets=LAT_BUCKETS)
DB_TX = Histogram("mintree_db_transaction_duration_seconds", "Transaction duration (BEGIN..COMMIT/ROLLBACK)",
                  ["outcome"], buckets=LAT_BUCKETS)
POOL_WAIT = Histogram("mintree_db_pool_checkout_wait_seconds", "Time waiting for a pooled connection", buckets=LAT_BUCKETS)
POOL_TIMEOUTS = Counter("mintree_db_pool_timeouts_total", "Pool checkout timeouts (pool exhaustion)")
POOL_CHECKED_OUT = Gauge("mintree_db_pool_checked_out", "Connections currently checked out", multiprocess_mode="livesum")
TX_RETRIES = Counter("mintree_tx_retries_total", "Transaction retries", ["reason"])
TX_FAILED = Counter("mintree_tx_failures_total", "Transactions that failed after retries", ["reason"])

WORKER_TASKS = Counter("mintree_worker_tasks_total", "Background task outcomes", ["task", "outcome"])
WORKER_DURATION = Histogram("mintree_worker_task_duration_seconds", "Background task run time", ["task"], buckets=LAT_BUCKETS)
WORKER_QUEUE_WAIT = Histogram("mintree_worker_queue_wait_seconds", "Time from outbox publish to first execution",
                              ["queue"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300))
OUTBOX_PUBLISHED = Counter("mintree_outbox_published_total", "Outbox events published to the broker")
OUTBOX_PUBLISH_FAILURES = Counter("mintree_outbox_publish_failures_total", "Outbox publish attempts that failed")

BUSINESS_CONFLICTS = Counter("mintree_business_conflicts_total", "Expected business conflicts (HTTP 409)", ["code"])
JOURNALS = Counter("mintree_journal_entries_total", "Journal entries posted")
STOCK_POSTINGS = Counter("mintree_stock_postings_total", "Stock ledger postings", ["movement"])

LOG_DROPPED = Counter("mintree_log_records_dropped_total", "Log records dropped because the log sink could not keep up")

_OP = re.compile(r"^\s*(\w+)")


def _op(stmt: str) -> str:
    m = _OP.match(stmt)
    w = (m.group(1).upper() if m else "OTHER")
    return w if w in ("SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT", "ROLLBACK") else "OTHER"


_slow_tokens = {"n": 10.0, "t": time.monotonic()}


def _slow_log_allowed(rate_per_s: float = 10.0) -> bool:
    """Token bucket: at most ~10 slow-transaction log lines per second per process, so a latency incident cannot
    turn into a log storm that makes the incident worse."""
    now = time.monotonic()
    _slow_tokens["n"] = min(rate_per_s, _slow_tokens["n"] + (now - _slow_tokens["t"]) * rate_per_s)
    _slow_tokens["t"] = now
    if _slow_tokens["n"] >= 1:
        _slow_tokens["n"] -= 1
        return True
    return False


def instrument_engine(engine) -> None:
    """Attach query / transaction / pool instrumentation. Idempotent per engine."""
    if getattr(engine, "_mintree_instrumented", False):
        return
    engine._mintree_instrumented = True
    slow_tx_s = get_settings().slow_tx_ms / 1000

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cur, stmt, params, ctx, many):
        ctx._t0 = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cur, stmt, params, ctx, many):
        dt = time.perf_counter() - getattr(ctx, "_t0", time.perf_counter())
        DB_QUERY.labels(_op(stmt)).observe(dt)
        st = stats_var.get()
        if st is not None:
            st.add_query(dt, " ".join(stmt.split())[:120])

    @event.listens_for(engine, "begin")
    def _begin(conn):
        conn.info["tx_t0"] = time.perf_counter()

    def _end(outcome):
        def fn(conn):
            t0 = conn.info.pop("tx_t0", None)
            if t0 is None:
                return
            dt = time.perf_counter() - t0
            DB_TX.labels(outcome).observe(dt)
            if dt >= slow_tx_s and _slow_log_allowed():
                st = stats_var.get()
                log.warning("slow transaction", extra={"fields": {
                    "tx_ms": round(dt * 1000, 1), "outcome": outcome, "route": getattr(st, "route", None),
                    "queries": getattr(st, "n_queries", None), "db_ms": round(getattr(st, "db_time_s", 0) * 1000, 1),
                    "slowest": [(round(s * 1000, 1), q) for s, q in getattr(st, "slowest", [])]}})
        return fn

    event.listen(engine, "commit", _end("commit"))
    event.listen(engine, "rollback", _end("rollback"))

    @event.listens_for(engine, "checkout")
    def _co(dbapi, rec, proxy):
        POOL_CHECKED_OUT.inc()

    @event.listens_for(engine, "checkin")
    def _ci(dbapi, rec):
        POOL_CHECKED_OUT.dec()

    # SQLAlchemy exposes no "waiting for connection" hook, so time pool.connect() directly.
    pool = engine.pool
    orig = pool.connect

    def timed_connect():
        t0 = time.perf_counter()
        try:
            return orig()
        except PoolTimeout:
            POOL_TIMEOUTS.inc()
            raise
        finally:
            w = time.perf_counter() - t0
            POOL_WAIT.observe(w)
            st = stats_var.get()
            if st is not None:
                st.pool_wait_s += w

    pool.connect = timed_connect  # type: ignore[method-assign]


def _rabbit_families() -> list:
    """Queue depth per queue from the RabbitMQ management API (best effort; absent broker => no series)."""
    try:
        import requests
        r = requests.get(get_settings().rabbitmq_api_url + "/queues", timeout=2)
        r.raise_for_status()
    except Exception:
        return []
    out = []
    for name, key, doc in (("mintree_rabbitmq_queue_messages_ready", "messages_ready", "Messages ready for delivery"),
                           ("mintree_rabbitmq_queue_messages_unacked", "messages_unacknowledged", "Delivered, not yet acked"),
                           ("mintree_rabbitmq_queue_consumers", "consumers", "Consumers attached")):
        g = GaugeMetricFamily(name, doc, labels=["queue"])
        for q in r.json():
            if q["name"] in ("default", "notifications", "integrations", "imports", "reports", "documents", "dead_letter"):
                g.add_metric([q["name"]], float(q.get(key, 0)))
        out.append(g)
    return out


class PostgresCollector:
    """Server-side stats scraped on demand (cached 5 s) over a dedicated 1-connection engine so that scraping
    never competes with request traffic for pool slots."""

    def __init__(self):
        self._engine = None
        self._cache: tuple[float, list] = (0.0, [])

    def _eng(self):
        if self._engine is None:
            from sqlalchemy import create_engine
            self._engine = create_engine(get_settings().database_url, pool_size=1, max_overflow=0, pool_timeout=3,
                                         connect_args={"application_name": "mintree-metrics",
                                                       "options": "-c statement_timeout=3000"})
        return self._engine

    def collect(self):
        now = time.time()
        if now - self._cache[0] < 5 and self._cache[1]:
            yield from self._cache[1]
            return
        fams = []
        try:
            with self._eng().connect() as c:
                a = c.execute(text("""
                    SELECT count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE state='idle'),
                           count(*) FILTER (WHERE state='idle in transaction'),
                           count(*) FILTER (WHERE wait_event_type='Lock'), count(*)
                    FROM pg_stat_activity WHERE datname = current_database()""")).one()
                d = c.execute(text("""
                    SELECT deadlocks, xact_commit, xact_rollback, temp_files, blks_read, blks_hit,
                           pg_database_size(datname) FROM pg_stat_database WHERE datname = current_database()""")).one()
                mx = int(c.execute(text("SHOW max_connections")).scalar())
                ob = c.execute(text("""
                    SELECT count(*) FILTER (WHERE status IN ('pending','publishing')),
                           COALESCE(EXTRACT(EPOCH FROM now() - min(created_at) FILTER (WHERE status IN ('pending','publishing'))), 0),
                           count(*) FILTER (WHERE status = 'dead')
                    FROM outbox_events WHERE status IN ('pending','publishing','dead')""")).one()
                jf = c.execute(text("SELECT count(*) FROM job_failures WHERE resolved_at IS NULL")).scalar()
            for name, doc, val in [
                ("mintree_pg_connections_active", "Backends executing a statement", a[0]),
                ("mintree_pg_connections_idle", "Idle backends", a[1]),
                ("mintree_pg_connections_idle_in_transaction", "Backends idle inside a transaction", a[2]),
                ("mintree_pg_lock_waiters", "Backends currently waiting on a lock", a[3]),
                ("mintree_pg_connections_total", "All backends on this database", a[4]),
                ("mintree_pg_max_connections", "max_connections", mx),
                ("mintree_pg_deadlocks_total", "Deadlocks detected (cumulative)", d[0]),
                ("mintree_pg_xact_commit_total", "Committed transactions (cumulative)", d[1]),
                ("mintree_pg_xact_rollback_total", "Rolled back transactions (cumulative)", d[2]),
                ("mintree_pg_temp_files_total", "Temp files created (cumulative)", d[3]),
                ("mintree_pg_blocks_read_total", "Disk blocks read (cumulative)", d[4]),
                ("mintree_pg_blocks_hit_total", "Buffer cache hits (cumulative)", d[5]),
                ("mintree_pg_database_size_bytes", "Database size", d[6]),
                ("mintree_outbox_unpublished", "Outbox events waiting to be published", ob[0]),
                ("mintree_outbox_oldest_unpublished_age_seconds", "Age of the oldest unpublished outbox event", ob[1]),
                ("mintree_outbox_dead", "Outbox events that exhausted publish attempts (need investigation)", ob[2]),
                ("mintree_job_failures_open", "Permanently failed background jobs not yet resolved", jf),
            ]:
                g = GaugeMetricFamily(name, doc)
                g.add_metric([], float(val))
                fams.append(g)
            fams.extend(_rabbit_families())
        except Exception as e:  # never fail the scrape because PG is unhappy; surface it as a metric instead
            g = GaugeMetricFamily("mintree_pg_scrape_error", "1 if PostgreSQL stats could not be read")
            g.add_metric([], 1.0)
            fams.append(g)
            log.warning("pg stats scrape failed: %s", e)
        self._cache = (now, fams)
        yield from fams


_pg = PostgresCollector()


def render_metrics() -> bytes:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(reg)
    else:
        from prometheus_client import REGISTRY as reg
    out = generate_latest(reg)
    pg = CollectorRegistry()
    pg.register(_pg)
    return out + generate_latest(pg)


def stats_scope() -> RequestStats:
    s = RequestStats()
    stats_var.set(s)
    return s
