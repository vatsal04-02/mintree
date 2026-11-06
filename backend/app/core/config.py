from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MINTREE_", extra="ignore")

    database_url: str = "postgresql+psycopg://mintree:mintree@localhost:5433/mintree"
    environment: str = "development"  # development | test | production

    # --- Connection pool & concurrency (per API process; see docs/SCALABILITY_STRATEGY.md for the capacity maths) ---
    # Total DB connections one process may hold = db_pool_size + db_max_overflow.
    db_pool_size: int = 10
    db_max_overflow: int = 0
    db_pool_timeout_s: float = 10.0       # fail fast (HTTP 503) instead of queueing for 30 s
    db_pool_recycle_s: int = 1800
    db_pre_ping: bool = True                 # SELECT 1 on every checkout: protects against stale connections, costs a round-trip
    db_prepare_threshold: str = "5"          # psycopg server-side auto-prepare after N executions; "none" disables (needed for PgBouncer txn pooling)
    # Admission control: max requests in flight per process (0 = DB pool capacity). Requests beyond it wait without
    # holding a thread or connection and are rejected with 503 after admission_timeout_s.
    max_in_flight: int = 0
    admission_timeout_s: float = 5.0
    # Thread pool for sync handlers; must be >= in-flight slots (0 = slots + 4 headroom for non-DB sync work).
    threadpool_size: int = 0
    # Messaging (Redis = cache/coordination only; RabbitMQ = durable work queue; neither is a source of truth)
    broker_url: str = "amqp://guest:guest@127.0.0.1:5672//"
    redis_url: str = "redis://127.0.0.1:6379/0"
    rabbitmq_api_url: str = "http://guest:guest@127.0.0.1:15672/api"
    outbox_batch: int = 100
    outbox_lease_s: int = 60            # a 'publishing' row older than this is considered abandoned (publisher crash)
    outbox_max_attempts: int = 8        # then status='dead' for operator inspection
    outbox_retention_days: int = 7

    # Dispatch / courier
    courier_webhook_secret: str = "dev-courier-secret"     # HMAC key for inbound courier webhooks; MUST be overridden in production
    marketplace_webhook_secret: str = "dev-marketplace-secret"
    courier_default: str = "fake"
    courier_booking_lease_s: int = 120

    # Observability
    metrics_token: str = ""            # if set, /api/metrics requires "Authorization: Bearer <token>"
    slow_request_ms: int = 1000        # log requests slower than this with a DB breakdown
    slow_tx_ms: int = 300              # log DB transactions slower than this with their slowest statements
    log_json: bool = True

    # Server-side safety nets applied to every connection (milliseconds; 0 disables).
    db_statement_timeout_ms: int = 15000
    db_lock_timeout_ms: int = 5000
    db_idle_in_tx_timeout_ms: int = 30000

    # Session / cookie security
    session_cookie_name: str = "mintree_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # must be True behind HTTPS in production
    cookie_samesite: str = "lax"

    # Login protection
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Document storage (local filesystem now; S3-compatible backend is a drop-in
    # implementation of app.documents.storage.Storage)
    storage_root: str = "storage"
    max_upload_mb: int = 25

    # First-run bootstrap admin (used only by `python -m app.seed`)
    bootstrap_admin_email: str = "admin@example.com"  # override via MINTREE_BOOTSTRAP_ADMIN_EMAIL
    bootstrap_admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
